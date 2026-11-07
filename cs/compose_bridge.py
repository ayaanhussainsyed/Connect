"""The only place the decision flow talks to the message helper's code (``cs.compose``,
``cs.channels``). Each function uses the helper's module if it imports and works, otherwise a
minimal local fallback, so the five-step chain never breaks while their code is in progress.

- ``card_message`` -> ``cs.compose.compose_card_message`` (fallback: a plain text card)
- ``confirmation_message`` -> ``cs.compose.compose_confirmation``
- ``followup_message`` -> ``cs.compose.compose_followup``
- ``send`` -> ``cs.channels.router.send`` (opt-outs, the Mongo ``outbox`` + ``messages``)
- ``thread`` / ``record_inbound`` -> ``cs.channels.outbox``
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from types import SimpleNamespace

from pymongo.database import Database

from cs import data, db as dbmod
from cs.domain import Message, utcnow
from cs.hours import CLOSED, REGULAR

log = logging.getLogger(__name__)


@lru_cache(maxsize=4096)
def contact(entity_id: str) -> dict:
    c = data.contacts()
    row = c[c["entity_id"] == entity_id]
    lang = data.entity(entity_id)["meta"].get("language", "fi")
    if row.empty:
        return {"lang": lang, "channel": "OUTBOX", "opted_out": False, "standing_permission": False}
    r = row.iloc[0]
    return {"lang": r["preferred_language"] or lang, "channel": r["preferred_channel"] or "OUTBOX",
            "opted_out": bool(r["opted_out_of_suggestions"]),
            "standing_permission": bool(r["standing_permission_holidays"]),
            "first_name": r.get("contact_first_name", "")}


def _hours_text(h: str | None) -> str:
    if h is None:
        return "–"
    return {CLOSED: "closed", REGULAR: "regular hours"}.get(h, h.replace("-", "–"))


def _fallback_card(card, link: str) -> dict:
    name = data.entity(card.entity_id).get("name", card.entity_id)
    lines = ["Fonecta Connect (automated assistant)", f"Hi {name}! Our suggestion for your upcoming special days:"]
    for i in card.items:
        lines.append(f"• {i['date']} {_hours_text(i['value'])}  (Google now: {_hours_text(i['shown'])})")
    if card.question:
        lines.append(f"• {card.question['date']} ?  ← one question: closed, regular hours or other?")
    if card.approve_by:
        lines.append(f"Answer by {card.approve_by} so every app shows it in time.")
    lines.append(f"Approve, edit or say no thanks: {link}")
    return {"text": "\n".join(lines), "buttons": [{"id": "APPROVE", "title": "Approve"},
                                                  {"id": "APPROVE_EVERY_YEAR", "title": "Approve + every year"},
                                                  {"id": "EDIT", "title": "Edit"}],
            "parts": {}, "composer": "fallback"}


def card_message(card, link: str, lang: str, channel: str, today: date | None = None) -> dict:
    try:
        from cs import compose
        biz = compose.business_for(card.entity_id)
        sugs = [SimpleNamespace(**{k: i.get(k) for k in ("date", "day_key", "value", "shown", "basis", "action")})
                for i in card.items]
        try:
            views, vday = compose.views_last_year(card.entity_id, sugs)
        except Exception:  # noqa: BLE001 - the value line is optional
            views, vday = None, None
        q = None
        if card.question:
            q = {k: card.question.get(k) for k in ("date", "regular", "candidate")}
        res = compose.compose_card_message(
            biz, sugs, lang, channel, True, q, date.fromisoformat(card.approve_by) if card.approve_by else None,
            views, value_line_day=vday, link=link, today=today)
        return {**res, "composer": "cs.compose"}
    except Exception as ex:  # noqa: BLE001
        log.warning("cs.compose unavailable for the card message (%s); using the fallback", ex)
        return _fallback_card(card, link)


def confirmation_message(card, applied: dict[str, str], where_rows: list[dict], lang: str, channel: str,
                         every_year: bool = False) -> dict:
    try:
        from cs import compose
        res = compose.compose_confirmation(compose.business_for(card.entity_id), applied, where_rows, lang,
                                           channel, every_year=every_year)
        return {**res, "composer": "cs.compose"}
    except Exception as ex:  # noqa: BLE001
        log.warning("cs.compose unavailable for the confirmation (%s); using the fallback", ex)
        saved = ", ".join(f"{d} {_hours_text(h)}" for d, h in applied.items())
        live = " · ".join(f"{r['publisher']} {r['eta_text']}" for r in where_rows if r.get("status") == "LIVE")
        text = f"✓ Saved: {saved}." + (f"\nShows on: {live}" if live else "") + "\nYou can undo any time."
        return {"text": text, "buttons": [{"id": "UNDO", "title": "Undo"}], "parts": {}, "composer": "fallback"}


def followup_message(kind: str, lang: str, channel: str = "OUTBOX", *, values: dict | None = None,
                     link: str | None = None) -> dict:
    """APPROVE | APPROVE_EVERY_YEAR | EDIT (``values`` -> read-back with Yes/No; ``link`` -> edit link) |
    DECLINE | STOP | UNDO | REMINDER."""
    try:
        from cs import compose
        return {**compose.compose_followup(kind, lang, channel=channel, values=values, link=link),
                "composer": "cs.compose"}
    except Exception:  # noqa: BLE001
        texts = {"DECLINE": "OK, we won't change anything.", "UNDO": "Undone: we restored your previous hours.",
                 "STOP": "OK, no more suggestions.",
                 "EDIT": (("Shall we save this? " + ", ".join(f"{d} {v}" for d, v in values.items())) if values
                          else f"Edit the hours here: {link}")}
        btns = [{"id": "YES", "title": "Yes"}, {"id": "NO", "title": "No"}] if (kind == "EDIT" and values) else []
        return {"text": texts.get(kind, kind), "buttons": btns, "parts": {}, "composer": "fallback"}


def send(entity_id: str, composed: dict, *, kind: str, card_id: str | None = None, token: str | None = None,
         lang: str = "fi", channel: str = "OUTBOX", db: Database | None = None) -> dict:
    """Deliver a composed message through ``cs.channels.router`` (opt-outs honoured, everything
    mirrored to the Mongo ``outbox`` and ``messages``). Fallback: a ``Message`` row in ``messages``.
    Returns ``{"message_id", "status", "delivered_via", "detail"}``."""
    db = db if db is not None else dbmod.get_db()
    try:
        from cs.channels import OutgoingMessage, router
        out = OutgoingMessage.from_composed(entity_id, composed, kind=kind, card_id=card_id, token=token,
                                            lang=lang, channel=channel)
        rec = router.send(out, db=db)
        _remember_digits(entity_id, card_id, kind, composed, rec.message_id, db)
        return {"message_id": rec.message_id, "status": rec.status, "delivered_via": rec.delivered_via,
                "detail": rec.detail}
    except ImportError:
        m = Message(card_id=card_id or "", channel=channel, language=lang, text=composed["text"],
                    buttons=composed.get("buttons") or [], token=token, sent_at=utcnow(), entity_id=entity_id,
                    kind=kind, composer=composed.get("composer", ""))
        dbmod.save(m, db=db)
        return {"message_id": m.id, "status": "STORED", "delivered_via": "OUTBOX", "detail": "local fallback"}


def _remember_digits(entity_id: str, card_id: str | None, kind: str, composed: dict, message_id: str,
                     db: Database) -> None:
    """What "1", "2", … mean in a reply to this message (SMS texts carry the digits, not buttons)."""
    parts = composed.get("parts") or {}
    digits = {str(k): v for k, v in (parts.get("reply_digits") or {}).items()} or {
        str(b["digit"]): b["id"] for b in composed.get("buttons") or [] if b.get("digit") is not None}
    if digits:
        db["reply_context"].insert_one({"entity_id": entity_id, "card_id": card_id, "kind": kind,
                                        "message_id": message_id, "digits": digits, "at": utcnow()})


def digit_meaning(entity_id: str, digit: str, card_id: str | None = None,
                  db: Database | None = None) -> tuple[str | None, str | None]:
    """(button id, card id) for a bare digit, from the newest message that offered digits."""
    db = db if db is not None else dbmod.get_db()
    q: dict = {"entity_id": entity_id}
    if card_id:
        q["card_id"] = card_id
    ctx = db["reply_context"].find_one(q, {"_id": 0}, sort=[("at", -1)])
    if not ctx:
        return None, card_id
    return ctx["digits"].get(digit), ctx.get("card_id")


def record_inbound(entity_id: str, text: str | None, *, channel: str, button_payload: str | None = None,
                   card_id: str | None = None, db: Database | None = None) -> None:
    try:
        from cs.channels import outbox
        outbox.record_inbound(entity_id, text, channel=channel, button_payload=button_payload, card_id=card_id,
                              db=db)
    except ImportError:
        pass


def thread(entity_id: str, db: Database | None = None, limit: int = 40, card_id: str | None = None) -> list[dict]:
    """The phone view: ``{text, buttons, kind, channel, direction ('IN'|'OUT'), at, via, card_id, token}``,
    oldest first, from the outbox (fallback: ``messages``)."""
    db = db if db is not None else dbmod.get_db()
    try:
        from cs.channels import outbox
        rows = outbox.thread(entity_id, limit=limit, db=db)
        out = [{"text": r.get("text") or "", "buttons": r.get("buttons") or [], "kind": r.get("kind", ""),
                "channel": r.get("channel", ""), "direction": (r.get("direction") or "out").upper(),
                "at": r.get("at"), "via": r.get("delivered_via") or r.get("status", ""),
                "card_id": r.get("card_id"), "token": r.get("token")} for r in rows]
    except ImportError:
        out = [{"text": m.text, "buttons": m.buttons, "kind": m.kind, "channel": m.channel, "direction": m.direction,
                "at": m.sent_at, "via": m.composer, "card_id": m.card_id, "token": m.token}
               for m in dbmod.find(Message, {"entity_id": entity_id}, db=db, sort=[("sent_at", 1)], limit=limit)]
    if card_id:
        out = [m for m in out if m["card_id"] in (card_id, None)]
    return out
