"""B1 v1 – the season card: one card per business and season, built from the guarded suggestions.

    card = build_card("FI-D0001", 2026, engine="ml", target="sandbox")   # saved as DRAFT
    msg, token = send_card(card)                                         # -> SENT, message stored
    preview_answer(card, "2026-12-26", "closed")                         # instant, changes nothing

What a card holds (``cs.domain.Card``):
- ``items``: the suggestions with ``action == "SUGGEST"`` and ``message_ok`` (FLAG rows, opt-outs and
  quiet rows never reach a card), each with its deadline and ``flip_tick_required``: true when the
  suggestion switches open <-> closed and its basis is not the owner's own history
  (OWN_LAST_YEAR / OWN_2_SEASONS). Such a date is applied only with an explicit tick.
- ``deadline_by_publisher`` and ``approve_by`` for the earliest date (``cs.confirm``).
- ``expected_current``: the live holidayHours for the card's dates when the card was built
  (``YextClient.get_holiday_hours``). The writer cancels a date if the owner changed it since.
- ``question``: the one question from ``cs.engine.api.question_value`` if its value is > 0
  (skipped if the engine is not importable or fails).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from types import SimpleNamespace

from pymongo.database import Database

from cs import audit, data, db as dbmod, display, export, links
from cs.domain import CARD_TRANSITIONS, Card, Message, can_transition, utcnow
from cs.hours import CLOSED

log = logging.getLogger(__name__)

OWN_HISTORY_BASES = frozenset({"OWN_LAST_YEAR", "OWN_2_SEASONS"})
QUESTION_STATES = ("closed", "regular", "special")


class NoCard(Exception):
    """Nothing to send for this business (quiet, flagged, opted out, …). ``str(e)`` says why."""


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def yext_client(target: str = "mock", db: Database | None = None):
    """The one place the app makes Yext clients (tests patch this to point at their mock)."""
    from cs.yext import YextClient
    return YextClient(target, db=db)


def default_target(entity_id: str) -> str:
    """FI-D0001 is demoed on the real sandbox (if configured); everything else on the mock."""
    from cs.yext import SANDBOX_MAP
    from cs.config import settings
    return "sandbox" if entity_id in SANDBOX_MAP and settings.has("yext_sandbox_key") else "mock"


def is_flip(shown: str | None, value: str | None) -> bool:
    return value is not None and shown is not None and ((shown == CLOSED) != (value == CLOSED))


def _item(s, entity_id: str, as_of: date) -> dict:
    from cs import confirm
    flip = is_flip(s.shown, s.value)
    ex = display.explain_shown(entity_id, s.date, as_of)
    return {
        "suggestion_id": s.id, "date": s.date, "day_key": s.day_key, "shown": s.shown,
        "shown_source": ex["source"], "regular": ex["regular"]["hours"], "value": s.value,
        "basis": s.basis, "probability": s.probability, "action": "SUGGEST",
        "evidence": [e.to_doc() for e in s.evidence[:4]],
        "flip": flip, "flip_tick_required": flip and s.basis not in OWN_HISTORY_BASES,
        "deadline": confirm.deadline_for(entity_id, s.date).isoformat(),
    }


def _question(entity_id: str, season: int, all_rows: list, as_of: date) -> dict | None:
    try:
        from cs.engine.api import question_value
        ranked = question_value(entity_id, season, {})
    except Exception as ex:  # noqa: BLE001 - the card works without a question
        log.info("no question for %s: %s", entity_id, ex)
        return None
    by_date = {s.date: s for s in all_rows}
    for d, value in ranked:
        if value <= 0 or d not in by_date:
            continue
        s = by_date[d]
        ex = display.explain_shown(entity_id, d, as_of)
        return {"date": d, "day_key": s.day_key, "value": float(value), "shown": ex["shown"],
                "regular": ex["regular"]["hours"], "candidate": s.value if s.action == "SUGGEST" else None,
                "options": list(QUESTION_STATES), "answer": None, "source": "cs.engine.api.question_value"}
    return None


@audit.batched
def build_card(entity_id: str, season: int = 2026, engine: str | None = None, *, target: str | None = None,
               db: Database | None = None, client=None, ask_question: bool = True,
               today: date | None = None, save: bool = True, only_dates: list[str] | None = None,
               kind: str = "SEASON") -> Card:
    """Build (and save as DRAFT) the season card. Raises ``NoCard`` if there is nothing to ask.

    ``only_dates`` limits the card to those dates (follow-up cards, autopilot dates that failed the
    agreement check); such cards never carry the one question."""
    from cs import compose_bridge, confirm
    from cs.config import settings

    db = _db(db)
    engine = (engine or settings.engine or "baseline").lower()
    target = target or default_target(entity_id)
    today = today or date.today()
    as_of = data.SNAPSHOT[season]
    rows = export.build(season, engine, today=today, entity_ids=[entity_id])
    if not rows:
        raise NoCard(f"{entity_id} has no target days in {season}")
    send = sorted((s for s in rows if s.action == "SUGGEST" and s.message_ok
                   and (only_dates is None or s.date in only_dates)), key=lambda s: s.date)
    question = _question(entity_id, season, rows, as_of) if (ask_question and only_dates is None) else None
    if not send and question is None:
        flagged = sum(1 for s in rows if s.action == "FLAG")
        opted = [s for s in rows if s.action == "SUGGEST" and not s.message_ok]
        if flagged:
            raise NoCard(f"{flagged} date(s) flagged for staff (likely closed for good); no owner message")
        if opted:
            raise NoCard("the owner opted out of suggestions; nothing is sent")
        raise NoCard("Google already shows the right hours on every upcoming special day: stay quiet")

    items = [_item(s, entity_id, as_of) for s in send]
    dates = [i["date"] for i in items] + ([question["date"]] if question else [])
    first = min(dates)
    yc = client or yext_client(target, db)
    try:
        live = yc.get_holiday_hours(entity_id)
    finally:
        if client is None:
            yc.close()
    contact = compose_bridge.contact(entity_id)
    card = Card(
        entity_id=entity_id, season=season, suggestion_ids=[i["suggestion_id"] for i in items],
        deadline_by_publisher=confirm.deadline_by_publisher(entity_id, first), question=question,
        status="DRAFT", items=items, expected_current={d: live.get(d) for d in sorted(set(dates))},
        approve_by=confirm.deadline_for(entity_id, first).isoformat(), target=target, engine=engine,
        owner_id=f"owner:{entity_id}", lang=contact.get("lang", "fi"), channel=contact.get("channel", "OUTBOX"),
        history=[{"from": None, "to": "DRAFT", "at": utcnow().isoformat(), "reason": f"built ({kind.lower()})"}],
    )
    if save:
        export.save_to_mongo(rows, db=db)
        dbmod.save(card, db=db)
        audit.log("CARD_BUILT", {
            "card_id": card.id, "season": season, "engine": engine, "target": target,
            "items": [{k: i[k] for k in ("date", "day_key", "shown", "value", "basis", "flip_tick_required")}
                      for i in items],
            "question": question and {k: question[k] for k in ("date", "value")},
            "approve_by": card.approve_by, "expected_current": card.expected_current, "kind": kind,
            "flagged": sum(1 for s in rows if s.action == "FLAG"),
        }, entity_id=entity_id, db=db)
    return card


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_card(card_id: str, db: Database | None = None) -> Card | None:
    return dbmod.get(Card, card_id, db=_db(db))


def transition(card: Card, new: str, reason: str = "", db: Database | None = None) -> Card:
    """Move the card along CARD_TRANSITIONS (raises ValueError otherwise), save, audit."""
    if card.status == new:
        return card
    if not can_transition(CARD_TRANSITIONS, card.status, new):
        raise ValueError(f"card {card.id}: {card.status} -> {new} is not allowed")
    db = _db(db)
    old, card.status, card.updated_at = card.status, new, utcnow()
    card.history.append({"from": old, "to": new, "at": card.updated_at.isoformat(), "reason": reason})
    dbmod.save(card, db=db)
    audit.log("CARD_STATE", {"card_id": card.id, "from": old, "to": new, "reason": reason},
              entity_id=card.entity_id, db=db)
    return card


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
@audit.batched
def send_card(card: Card, db: Database | None = None, *, channel: str | None = None, lang: str | None = None,
              today: date | None = None, base_url: str | None = None) -> tuple[Message, str]:
    """Sign a link, compose the message, store it (the outbox) and mark the card SENT."""
    from cs import compose_bridge

    db = _db(db)
    token = links.sign(card.id, card.owner_id)
    link = links.link_for(token, base_url)
    channel = (channel or card.channel or "OUTBOX").upper()
    lang = lang or card.lang
    msg_parts = compose_bridge.card_message(card, link, lang, channel, today=today)
    rec = compose_bridge.send(card.entity_id, msg_parts, kind="CARD", card_id=card.id, token=token, lang=lang,
                              channel=channel, db=db)
    msg = Message(card_id=card.id, channel=channel, language=lang, text=msg_parts["text"],
                  buttons=msg_parts.get("buttons") or [], token=token, sent_at=utcnow(),
                  entity_id=card.entity_id, kind="CARD", composer=msg_parts["composer"], id=rec["message_id"])
    card.dates_shown = (msg_parts.get("parts") or {}).get("dates_shown") or [i["date"] for i in card.items]
    dbmod.save(card, db=db)
    audit.log("MESSAGE_SENT", {"card_id": card.id, "message_id": msg.id, "channel": channel, "lang": lang,
                               "composer": msg.composer, "chars": len(msg.text), "delivery": rec["status"],
                               "via": rec["delivered_via"], "dates_shown": card.dates_shown},
              entity_id=card.entity_id, db=db)
    if card.status in ("DRAFT", "SCHEDULED"):
        transition(card, "SENT", f"sent via {channel}", db)
    return msg, token


# ---------------------------------------------------------------------------
# The one question: preview of the answer (changes nothing)
# ---------------------------------------------------------------------------
def preview_answer(card: Card, answer_date: str | None, answer: str | None) -> dict:
    """What the answer means for the answered date only (the ML findings: other dates do not move,
    so the page must not claim they do). Returns ``{"date", "answer", "hours"}`` or ``{}``.
    ``hours`` is "closed", the regular hours, or None for "other hours" (asked on the card).
    Nothing is saved: the answer is stored only when the owner submits the card."""
    q = card.question or {}
    if answer not in QUESTION_STATES or not answer_date or answer_date != q.get("date"):
        return {}
    hours = {"closed": CLOSED, "regular": q.get("regular"), "special": None}[answer]
    return {"date": answer_date, "answer": answer, "hours": hours}


def card_message_suggestions(card: Card) -> list[SimpleNamespace]:
    return [SimpleNamespace(**{k: i.get(k) for k in ("date", "day_key", "value", "shown", "basis", "action")})
            for i in card.items]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def followup_card(card: Card, dates: list[str], db: Database | None = None, *, base_url: str | None = None,
                  today: date | None = None) -> tuple[Card, str] | None:
    """A short card for dates left undecided (e.g. cut from a shortened message). None if nothing is left."""
    try:
        new = build_card(card.entity_id, card.season, card.engine, target=card.target, db=db, ask_question=False,
                         only_dates=dates, today=today, kind="FOLLOWUP")
    except NoCard:
        return None
    _, token = send_card(new, db, base_url=base_url, today=today)
    audit.log("FOLLOWUP_CARD", {"card_id": new.id, "from_card": card.id, "dates": [i["date"] for i in new.items]},
              entity_id=card.entity_id, db=_db(db))
    return new, token


def restore_previous(yc, entity_id: str, previous: dict[str, str | None], ours: dict[str, str],
                     decision_id: str, today: date) -> dict:
    """Undo our holidayHours write: dates that had a value get it back; dates that had none are
    removed again (``remove_holiday_dates``). Owner-wins on both: ``ours`` (what we wrote) is the
    expected live value, so anything the owner changed since is left alone."""
    out: dict = {"restored": {}, "left_alone": {}, "write_ids": [], "readback_ok": True}
    set_back = {d: v for d, v in previous.items() if v is not None}
    remove = sorted(d for d, v in previous.items() if v is None)
    if set_back:
        rec = yc.set_holiday_hours(entity_id, set_back, {d: ours[d] for d in set_back}, decision_id, today=today)
        out["restored"].update(rec.request.get("applied") or {})
        out["left_alone"].update(rec.request.get("cancelled") or {})
        out["write_ids"].append(rec.id)
        out["readback_ok"] &= bool(rec.readback_ok) or not rec.request.get("applied")
    if remove:
        rec = yc.remove_holiday_dates(entity_id, remove, {d: ours[d] for d in remove}, decision_id, today=today)
        out["restored"].update({d: None for d in rec.request.get("removed") or {}})
        out["left_alone"].update(rec.request.get("cancelled") or {})
        out["write_ids"].append(rec.id)
        out["readback_ok"] &= bool(rec.readback_ok) or not rec.request.get("removed")
    return out
