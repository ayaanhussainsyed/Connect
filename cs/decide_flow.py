"""A8 → A9 → A11: the owner's decision on a season card, applied to Yext, confirmed, learned; and undo.

    handle_decision(card_id, {
        "per_date": {"2026-12-24": "APPROVE", "2026-12-26": {"action": "EDIT", "hours": "10-14"},
                     "2026-12-31": "DECLINE"},
        "ticks": ["2026-12-25"],          # explicit confirmation of open<->closed flips (see below)
        "every_year": True,
        "answer": {"2026-12-26": "closed"} | None,   # the one question (closed | regular | special)
    }, via="web")
    undo_decision(batch_id_or_decision_id)

Steps of ``handle_decision`` (each one leaves an audit event):
1. Validate: unknown dates and bad hours are returned, not applied. An APPROVE of an inferred flip
   (``flip_tick_required``) without a tick is returned as ``needs_tick`` and not applied. An owner's
   own EDIT never needs a tick (it is explicit).
2. Save one ``Decision`` per date (shared ``batch_id``).
3. Guardrails on the final values (``not_past``, ``valid_hours``, ``listing_active``).
4. ``YextClient(card.target).set_holiday_hours(changes, expected_current=card snapshot,
   decision_id=batch_id)``: owner-wins cancels dates the owner changed meanwhile.
5. ``cs.confirm.where_it_shows``.
6. ``LearnedFact`` per applied date (``OWNER_ANSWER``; supersedes older facts for that holiday).
7. ``every_year`` -> a scoped ``Permission`` (the applied holiday keys and their values).
8. Card: (DRAFT ->) SENT -> DECIDED -> APPLIED -> CONFIRMED; a confirmation message is stored.
Returns the confirmation data (JSON-ready).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pymongo.database import Database

from cs import audit, compose_bridge, data, db as dbmod, guardrails, learn, season
from cs.domain import Card, Decision, Permission, Suggestion, new_id, utcnow
from cs.hours import REGULAR, normalise

OPEN_STATES = ("DRAFT", "SCHEDULED", "SENT", "QUESTION_ANSWERED", "NO_REPLY", "REMINDED")
APPLIED_STATES = ("APPLIED", "CONFIRMED")
ACTIONS = ("APPROVE", "EDIT", "DECLINE")
QUESTION_FACT = {"closed": "closed", "regular": REGULAR, "special": "SPECIAL"}


class DecisionError(ValueError):
    pass


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def parse_choice(v: Any) -> tuple[str, str | None]:
    """'APPROVE' | 'DECLINE' | 'EDIT:10-14' | ('EDIT', '10-14') | {'action': 'EDIT', 'hours': '10-14'}."""
    if isinstance(v, dict):
        act, hours = str(v.get("action", "")).upper(), v.get("hours")
    elif isinstance(v, (list, tuple)) and v:
        act, hours = str(v[0]).upper(), (v[1] if len(v) > 1 else None)
    else:
        text = str(v or "")
        act, _, hours = text.partition(":")
        act, hours = act.strip().upper(), (hours.strip() or None)
    if act not in ACTIONS:
        raise DecisionError(f"unknown decision {v!r}")
    if act == "EDIT" and not hours:
        raise DecisionError("EDIT needs hours")
    return act, hours


def _jsonable(x):
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _final_guards(card: Card, item: dict, final: str, today: date) -> str | None:
    s = Suggestion(entity_id=card.entity_id, kind="HOLIDAY_HOURS", date=item["date"], day_key=item["day_key"],
                   shown=item["shown"], action="SUGGEST", value=final, basis="OWNER_ANSWER")
    ctx = guardrails.make_context(data.SNAPSHOT[card.season], today)
    for rule in (guardrails.not_past, guardrails.valid_hours, guardrails.listing_active):
        ok, reason = rule(s, ctx)
        if not ok:
            return f"{rule.__name__}: {reason}"
    return None


@audit.batched
def handle_decision(card_id: str, decision: dict, via: str = "web", *, db: Database | None = None,
                    client=None, today: date | None = None) -> dict:
    db = _db(db)
    today = today or date.today()
    card = season.load_card(card_id, db)
    if card is None:
        raise DecisionError("unknown card")
    if card.status not in OPEN_STATES:
        raise DecisionError(f"this card is already {card.status.lower()}")
    batch_id = new_id("batch")
    items = {i["date"]: i for i in card.items}
    per_date_raw = decision.get("per_date") or {}
    per_date = {str(d): parse_choice(v) for d, v in per_date_raw.items()}
    ticks = {str(d) for d in (decision.get("ticks") or [])}
    every_year = bool(decision.get("every_year"))
    answer = {str(d): str(v).lower() for d, v in (decision.get("answer") or {}).items() if v}

    audit.log("DECISION_RECEIVED", {"card_id": card.id, "batch_id": batch_id, "via": via,
                                    "per_date": {d: [a, h] for d, (a, h) in per_date.items()},
                                    "ticks": sorted(ticks), "every_year": every_year, "answer": answer},
              entity_id=card.entity_id, db=db)

    res: dict[str, Any] = {"card_id": card.id, "entity_id": card.entity_id, "batch_id": batch_id,
                           "target": card.target, "every_year": every_year, "applied": {}, "declined": [],
                           "needs_tick": [], "invalid": {}, "blocked": {}, "cancelled": {}, "unknown_dates": [],
                           "decisions": [], "write": None, "where_it_shows": [], "facts": [], "permission": None,
                           "answer": answer or None, "message": None, "error": None}
    decisions: list[Decision] = []
    to_apply: dict[str, Decision] = {}
    for d, (act, hours) in sorted(per_date.items()):
        item = items.get(d)
        if item is None:
            res["unknown_dates"].append(d)
            continue
        base = dict(card_id=card.id, suggestion_id=item["suggestion_id"], every_year=every_year, via=via,
                    entity_id=card.entity_id, date=d, batch_id=batch_id)
        if act == "DECLINE":
            decisions.append(Decision(**base, decision="DECLINE", status="DECLINED"))
            res["declined"].append(d)
            continue
        if act == "APPROVE":
            final = item["value"]
        else:
            try:
                final = normalise(hours)
            except ValueError:
                res["invalid"][d] = f"not valid hours: {hours!r}"
                continue
        if act == "APPROVE" and item.get("flip_tick_required") and d not in ticks:
            res["needs_tick"].append(d)
            continue
        dec = Decision(**base, decision=act, final_value=final)
        blocked = _final_guards(card, item, final, today)
        if blocked:
            dec.status, dec.note = "BLOCKED", blocked
            res["blocked"][d] = blocked
        else:
            to_apply[d] = dec
        decisions.append(dec)

    if answer and card.question:
        qd, state = next(iter(answer.items()))
        q = card.question
        if state in QUESTION_FACT and qd == q.get("date"):
            value = {"closed": "closed", "regular": REGULAR, "special": None}[state]
            if state == "special" and decision.get("answer_hours"):
                try:
                    value = normalise(decision["answer_hours"])
                except ValueError:
                    res["invalid"][qd] = f"not valid hours: {decision['answer_hours']!r}"
            card.question = {**q, "answer": state, "answer_value": value, "answered_at": utcnow().isoformat()}
            res["facts"].append(learn.record_fact(
                card.entity_id, q.get("day_key") or "", qd, value or QUESTION_FACT[state], card.season,
                source="OWNER_QUESTION", batch_id=batch_id, db=db).id)
            audit.log("QUESTION_ANSWERED", {"card_id": card.id, "date": qd, "answer": state, "value": value,
                                            "batch_id": batch_id}, entity_id=card.entity_id, db=db)
            # An explicit answer is the owner's own value for that date: write it if Google shows otherwise.
            shown_now = q.get("shown")
            effective = q.get("regular") if value == REGULAR else value
            if value is not None and effective != shown_now and qd not in per_date:
                qitem = {"date": qd, "day_key": q.get("day_key") or "", "shown": shown_now}
                dec = Decision(card_id=card.id, suggestion_id=f"question:{qd}", decision="EDIT", final_value=value,
                               every_year=every_year, via=via, entity_id=card.entity_id, date=qd, batch_id=batch_id,
                               note="answer to the one question")
                blocked = _final_guards(card, qitem, effective, today)
                if blocked:
                    dec.status, dec.note = "BLOCKED", blocked
                    res["blocked"][qd] = blocked
                else:
                    to_apply[qd] = dec
                decisions.append(dec)

    if not decisions:
        if card.question and card.question.get("answer") and card.status in ("DRAFT", "SCHEDULED", "SENT"):
            if card.status != "SENT":
                season.transition(card, "SENT", "opened before it was sent", db)
            season.transition(card, "QUESTION_ANSWERED", "answer only", db)
        elif res["facts"]:
            dbmod.save(card, db=db)
        res["status"] = card.status
        if not (res["needs_tick"] or res["invalid"] or res["unknown_dates"] or res["facts"]):
            raise DecisionError("nothing was decided")
        return _jsonable(res)

    for dec in decisions:
        dbmod.save(dec, db=db)
    audit.log("DECISION_RECORDED", {"card_id": card.id, "batch_id": batch_id, "decisions": [
        {"id": x.id, "date": x.date, "decision": x.decision, "final_value": x.final_value, "status": x.status}
        for x in decisions]}, entity_id=card.entity_id, db=db)
    if card.status in ("DRAFT", "SCHEDULED"):
        season.transition(card, "SENT", "opened before it was sent", db)
    season.transition(card, "DECIDED", f"{len(decisions)} decision(s) via {via}", db)
    card.batch_ids.append(batch_id)

    if to_apply:
        _apply(card, to_apply, res, db, client, today, batch_id)
    elif res["declined"] and not res["blocked"]:
        msg = compose_bridge.followup_message("DECLINE", card.lang, card.channel)
        _store(card, msg, "FOLLOWUP", db)
        res["message"] = msg["text"]

    for dec in decisions:
        dbmod.save(dec, db=db)
    res["decisions"] = [{"id": x.id, "date": x.date, "decision": x.decision, "final_value": x.final_value,
                         "status": x.status, "note": x.note} for x in decisions]
    res["status"] = card.status
    card.result = _jsonable(res)
    dbmod.save(card, db=db)
    return card.result


def _store(card: Card, msg: dict, kind: str, db: Database) -> None:
    rec = compose_bridge.send(card.entity_id, msg, kind=kind, card_id=card.id, lang=card.lang,
                              channel=card.channel, db=db)
    audit.log("MESSAGE_SENT", {"card_id": card.id, "message_id": rec["message_id"], "kind": kind,
                               "channel": card.channel, "delivery": rec["status"], "via": rec["delivered_via"]},
              entity_id=card.entity_id, db=db)


def _apply(card: Card, to_apply: dict[str, Decision], res: dict, db: Database, client, today: date,
           batch_id: str) -> None:
    from cs import confirm
    from cs.yext import YextError, YextWriteError

    changes = {d: dec.final_value for d, dec in to_apply.items()}
    expected = {d: card.expected_current.get(d) for d in changes}
    own_client = client is None
    yc = client or season.yext_client(card.target, db)
    try:
        try:
            rec = yc.set_holiday_hours(card.entity_id, changes, expected, batch_id, today=today)
        except YextWriteError as ex:
            rec = ex.record
            res["error"] = f"the write failed: {ex}"
    except YextError as ex:
        res["error"] = f"Yext is not reachable: {ex}"
        for dec in to_apply.values():
            dec.note = "not applied: Yext not reachable"
        return
    finally:
        if own_client:
            yc.close()
    req = rec.request
    applied, cancelled = req.get("applied") or {}, req.get("cancelled") or {}
    for d, dec in to_apply.items():
        dec.write_id = rec.id
        if d in cancelled:
            dec.status, dec.note = "CANCELLED", "the listing changed after we asked (owner wins)"
        elif d in (req.get("dropped_past") or []):
            dec.status, dec.note = "BLOCKED", "the date has passed"
        elif d in applied and rec.readback_ok:
            dec.status = "APPLIED"
        else:
            dec.note = "written but the read-back did not match" if d in applied else "not written"
    res["cancelled"] = cancelled
    res["write"] = {"id": rec.id, "target": rec.target, "status": rec.response_status, "readback_ok": rec.readback_ok,
                    "note": rec.note, "readback": req.get("readback")}
    ok = {d: dec.final_value for d, dec in to_apply.items() if dec.status == "APPLIED"}
    res["applied"] = ok
    if not ok:
        return
    season.transition(card, "APPLIED", f"{len(ok)} date(s) written to the {card.target}", db)
    where = confirm.where_it_shows(card.entity_id, rec.at, target=card.target)
    res["where_it_shows"] = _jsonable(where)
    audit.log("CONFIRMATION", {"card_id": card.id, "batch_id": batch_id, "write_id": rec.id,
                               "publishers": [{"publisher": r["publisher"], "status": r["status"],
                                               "eta": r["eta_text"]} for r in where]},
              entity_id=card.entity_id, db=db)
    day_keys = {i["date"]: i["day_key"] for i in card.items}
    if card.question:
        day_keys[card.question["date"]] = card.question.get("day_key") or ""
    for d, v in ok.items():
        if d == (card.question or {}).get("date") and d not in {i["date"] for i in card.items}:
            continue  # the answer's fact is already recorded (OWNER_QUESTION)
        res["facts"].append(learn.record_fact(card.entity_id, day_keys[d], d, v, card.season,
                                              batch_id=batch_id, db=db).id)
    if res["every_year"]:
        p = learn.grant_permission(card.entity_id, {day_keys[d]: v for d, v in ok.items()},
                                   via=next(iter(to_apply.values())).via, batch_id=batch_id, db=db)
        res["permission"] = {"id": p.id, "scope": p.scope}
    msg = compose_bridge.confirmation_message(card, ok, where, card.lang, card.channel, res["every_year"])
    _store(card, msg, "CONFIRMATION", db)
    res["message"] = msg["text"]
    season.transition(card, "CONFIRMED", "confirmation sent", db)


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
@audit.batched
def undo_decision(batch_or_decision_id: str, via: str = "web", *, db: Database | None = None, client=None,
                  today: date | None = None) -> dict:
    """Put back the values that were live before (the card's snapshot), revoke a permission granted
    by that decision, retire its learned facts, and mark the card UNDONE.

    A date that had no exception before is removed again (``remove_holiday_dates``). Owner-wins
    applies here too: a date the owner has changed since our write is left alone.
    """
    db = _db(db)
    today = today or date.today()
    batch_id = batch_or_decision_id
    if not batch_id.startswith("batch-"):
        dec = dbmod.get(Decision, batch_or_decision_id, db=db)
        if dec is None or not dec.batch_id:
            raise DecisionError("unknown decision")
        batch_id = dec.batch_id
    decs = dbmod.find(Decision, {"batch_id": batch_id}, db=db)
    if not decs:
        raise DecisionError("unknown decision")
    card = season.load_card(decs[0].card_id, db)
    applied = [d for d in decs if d.status == "APPLIED"]
    if card is None or card.status not in APPLIED_STATES or not applied:
        raise DecisionError("there is nothing to undo")
    previous = {d.date: card.expected_current.get(d.date) for d in applied}
    ours = {d.date: d.final_value for d in applied}
    audit.log("UNDO_REQUESTED", {"card_id": card.id, "batch_id": batch_id, "via": via, "restore": previous},
              entity_id=card.entity_id, db=db)
    own_client = client is None
    yc = client or season.yext_client(card.target, db)
    try:
        r = season.restore_previous(yc, card.entity_id, previous, ours, f"undo:{batch_id}", today)
    finally:
        if own_client:
            yc.close()
    done = r["restored"]
    now = utcnow()
    for d in applied:
        if d.date in done and r["readback_ok"]:
            d.status, d.undone_at = "UNDONE", now
            dbmod.save(d, db=db)
    revoked = []
    for p in dbmod.find(Permission, {"source_decision_id": batch_id, "status": {"$in": ["GRANTED", "USED"]}}, db=db):
        if learn.revoke_permission(p.id, "UNDO", db=db):
            revoked.append(p.id)
    facts = learn.revert_batch(batch_id, db=db)
    season.transition(card, "UNDONE", "owner pressed undo", db)
    msg = compose_bridge.followup_message("UNDO", card.lang, card.channel)
    _store(card, msg, "FOLLOWUP", db)
    out = {"card_id": card.id, "batch_id": batch_id, "restored": done, "left_alone": r["left_alone"],
           "previous": previous, "write": {"ids": r["write_ids"], "readback_ok": r["readback_ok"]},
           "revoked_permissions": revoked, "facts_retired": facts, "status": card.status, "message": msg["text"]}
    card.result = {**(card.result or {}), "undo": _jsonable(out)}
    dbmod.save(card, db=db)
    return _jsonable(out)


# ---------------------------------------------------------------------------
# Replies by message (WhatsApp buttons, SMS digits, free text) -> the same handler
# ---------------------------------------------------------------------------
REPLY_STATES = OPEN_STATES + APPLIED_STATES
QUESTION_BUTTONS = {"Q_CLOSED": "closed", "Q_REGULAR": "regular", "Q_OTHER": "special"}


def latest_card(entity_id: str, db: Database | None = None) -> Card | None:
    found = dbmod.find(Card, {"entity_id": entity_id, "status": {"$in": list(REPLY_STATES)}}, db=_db(db),
                       sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def card_link(card: Card, db: Database | None = None, base_url: str | None = None) -> str | None:
    from cs import links
    from cs.domain import Message
    m = dbmod.find(Message, {"card_id": card.id, "token": {"$ne": None}}, db=_db(db), sort=[("sent_at", -1)], limit=1)
    return links.link_for(m[0].token, base_url) if m else None


def _say(card: Card, kind: str, db: Database, **kw) -> str:
    msg = compose_bridge.followup_message(kind, card.lang, card.channel, **kw)
    _store(card, msg, "FOLLOWUP", db)
    return msg["text"]


def _latest_out(entity_id: str, card_id: str | None, db: Database) -> dict | None:
    """The newest outgoing message with buttons (for this card, if given): its buttons give digits meaning."""
    q: dict = {"entity_id": entity_id, "direction": "out", "buttons.0": {"$exists": True}}
    if card_id:
        q["card_id"] = card_id
    return db["outbox"].find_one(q, {"_id": 0}, sort=[("at", -1)])


def _digit_button(entity_id: str, card_id: str | None, digit: str, db: Database) -> tuple[str | None, str | None]:
    button, cid = compose_bridge.digit_meaning(entity_id, digit, card_id, db)
    if button:
        return button, cid
    m = _latest_out(entity_id, card_id, db)
    if not m:
        return None, card_id
    b = next((b for b in m.get("buttons") or [] if str(b.get("digit")) == digit), None)
    return (b["id"] if b else None), (card_id or m.get("card_id"))


@audit.batched
def handle_reply(entity_id: str | None = None, *, card_id: str | None = None, button_id: str | None = None,
                 text: str | None = None, channel: str = "WHATSAPP", sender: str | None = None,
                 db: Database | None = None, client=None, today: date | None = None,
                 base_url: str | None = None) -> dict:
    """One owner reply -> the right handler.

    - A bare digit means the button with that digit on the newest message (after a confirmation
      "1" is Undo, on a card it is Approve, on a read-back it is Yes).
    - An emergency ("kiinni tänään", "suljettu huomenna") is recognised by the offline parser only:
      it never reaches the LLM, its text is never stored, and it goes to ``cs.emergency`` (sender check,
      rate limit, applied at once).
    - Messages about an emergency (``card_id = "em:<id>"``): Undo, and the daily "open again?" answer.
    - Card buttons: APPROVE, APPROVE_EVERY_YEAR, EDIT, DECLINE, UNDO, STOP, YES, NO, Q_CLOSED/Q_REGULAR/Q_OTHER.
      An APPROVE by message covers only the dates that were in the (possibly shortened) text; the other
      dates get a short follow-up card. Free text goes through ``cs.llm.parse_reply`` (None on doubt ->
      the card link) and edits are read back before saving.
    """
    from cs import emergency, llm

    db = _db(db)
    today = today or date.today()
    card = None
    if card_id and not card_id.startswith(("em:", "drift:", "rr:")):
        card = season.load_card(card_id, db)
        entity_id = card.entity_id if card else entity_id
    elif card_id and card_id.startswith(("drift:", "rr:")):
        offer = db["profile_offers"].find_one({"id": card_id.split(":", 1)[1]}, {"entity_id": 1})
        entity_id = offer["entity_id"] if offer else entity_id
    elif card_id:
        em = emergency.get(card_id[3:], db)
        entity_id = em["entity_id"] if em else entity_id
    if not entity_id:
        return {"handled": False, "reason": "unknown owner"}
    lang = compose_bridge.contact(entity_id)["lang"]
    if not button_id and text and text.strip().isdigit() and len(text.strip()) == 1:
        button_id, card_id = _digit_button(entity_id, card_id, text.strip(), db)
        if card_id and not card_id.startswith(("em:", "drift:", "rr:")) and card is None:
            card = season.load_card(card_id, db)

    # 1. Emergencies: offline parser only; the text is never stored or sent to an LLM.
    if not button_id and text:
        dates = [i["date"] for i in card.items] if card else []
        rx = llm.parse_reply_regex(text, lang, dates, today=today, expect="card")
        if rx and rx.get("action") == "EMERGENCY" and rx.get("emergency"):
            e = rx["emergency"]
            compose_bridge.record_inbound(entity_id, "[emergency request; the text is not stored]", channel=channel, db=db)
            audit.log("OWNER_REPLY", {"channel": channel, "emergency": True}, entity_id=entity_id, db=db)
            try:
                res = emergency.handle_emergency(entity_id, e.get("from"), e.get("hours") or "closed",
                                                 until=e.get("to"), until_further_notice=bool(e.get("until_further_notice")),
                                                 via=channel, sender=sender, db=db, client=client, today=today,
                                                 base_url=base_url)
            except emergency.EmergencyError as ex:
                return {"handled": False, "action": "EMERGENCY", "reason": str(ex)}
            return {"handled": True, "action": "EMERGENCY", "result": res}

    # 1b. Replies to the weekly-hours and review-reply offers (A13), and owner questions (B10, Ask Connect).
    from cs import profile_flow
    if profile_flow.is_offer_card(card_id):
        return profile_flow.handle_reply(entity_id, card_id, button_id=button_id, text=text, channel=channel, db=db)
    if not button_id and text and not card_id and (waiting := profile_flow.awaiting_text(entity_id, db)):
        return profile_flow.handle_reply(entity_id, waiting, text=text, channel=channel, db=db)
    if not button_id and profile_flow.is_question(text):
        return profile_flow.answer_question(entity_id, text, channel=channel, db=db)

    # 2. Replies about an emergency.
    if card_id and card_id.startswith("em:"):
        em_id = card_id[3:]
        compose_bridge.record_inbound(entity_id, text if button_id is None else None, channel=channel,
                                      button_payload=f"{button_id}|{card_id}" if button_id else None, card_id=card_id, db=db)
        try:
            if button_id == "UNDO":
                return {"handled": True, "action": "UNDO", "result": emergency.undo_emergency(em_id, db=db, client=client,
                                                                                            today=today)}
            state = QUESTION_BUTTONS.get(button_id or "")
            if state is None and text:
                p = llm.parse_reply(text, lang, [(today + timedelta(days=1)).isoformat()], expect="question", today=today)
                state = {"other": "special"}.get((p or {}).get("answer"), (p or {}).get("answer"))
            if state in ("closed", "regular", "special"):
                return {"handled": True, "action": "OPEN_AGAIN",
                        "result": emergency.answer_open_again(em_id, state, db=db, client=client, today=today)}
        except emergency.EmergencyError as ex:
            return {"handled": False, "reason": str(ex)}
        return {"handled": False, "reason": "nothing to do for that reply"}

    # 3. Season cards.
    card = card or (latest_card(entity_id, db) if entity_id else None)
    if card is None:
        if text:
            compose_bridge.record_inbound(entity_id, text, channel=channel, db=db)
        return {"handled": False, "reason": "no open card for this owner"}
    via = channel.lower()
    compose_bridge.record_inbound(card.entity_id, text, channel=channel, card_id=card.id, db=db,
                                  button_payload=f"{button_id}|{card.id}" if button_id else None)
    audit.log("OWNER_REPLY", {"card_id": card.id, "channel": channel, "button_id": button_id,
                              "text_chars": len(text or "")}, entity_id=card.entity_id, db=db)
    shown = card.dates_shown or [i["date"] for i in card.items]
    items = {i["date"]: i for i in card.items}
    action, parsed = (button_id or "").upper() or None, None
    if action is None and text:
        expect = "readback" if card.pending_reply else "card"
        dates = list(items) + ([card.question["date"]] if card.question else [])
        ctx = {d: {"suggested": i["value"], "shown": i["shown"]} for d, i in items.items()}
        parsed = llm.parse_reply(text, card.lang, dates, context=ctx, expect=expect, today=today)
        if parsed is None:
            out = _say(card, "EDIT", db, link=card_link(card, db, base_url))
            return {"handled": False, "reason": "unclear reply: sent the card link", "reply": out}
        action = parsed["action"]
        if expect == "readback":
            action = "YES" if action in ("APPROVE", "APPROVE_EVERY_YEAR") else "NO"
    res: dict | None = None
    try:
        if action in ("APPROVE", "APPROVE_EVERY_YEAR"):
            every = action == "APPROVE_EVERY_YEAR" or bool(parsed and parsed.get("every_year"))
            res = handle_decision(card.id, {"per_date": {d: "APPROVE" for d in shown if d in items},
                                            "every_year": every}, via=via, db=db, client=client, today=today)
            if res.get("needs_tick"):
                res["reply"] = _say(card, "EDIT", db, link=card_link(card, db, base_url))
            left = [d for d in items if d not in shown]
            if left and res.get("status") not in OPEN_STATES:
                fu = season.followup_card(card, left, db, base_url=base_url, today=today)
                res["followup_card_id"] = fu[0].id if fu else None
        elif action == "EDIT" and parsed and parsed.get("dates"):
            per, values = {}, {}
            for d, v in parsed["dates"].items():
                if d in items:
                    v = items[d]["regular"] if v == "regular" else v
                    per[d], values[d] = f"EDIT:{v}", v
            if parsed.get("approve_others"):
                per.update({d: "APPROVE" for d in shown if d in items and d not in per})
            card.pending_reply = {"per_date": per, "every_year": bool(parsed.get("every_year"))}
            dbmod.save(card, db=db)
            res = {"pending": card.pending_reply, "reply": _say(card, "EDIT", db, values=values)}
        elif action == "EDIT":
            res = {"reply": _say(card, "EDIT", db, link=card_link(card, db, base_url))}
        elif action == "YES" and card.pending_reply:
            pending, card.pending_reply = card.pending_reply, None
            dbmod.save(card, db=db)
            res = handle_decision(card.id, pending, via=via, db=db, client=client, today=today)
        elif action == "NO":
            card.pending_reply = None
            dbmod.save(card, db=db)
            res = {"reply": _say(card, "EDIT", db, link=card_link(card, db, base_url))}
        elif action == "DECLINE":
            res = handle_decision(card.id, {"per_date": {d: "DECLINE" for d in items}}, via=via, db=db,
                                  client=client, today=today)
        elif action == "UNDO" and card.batch_ids:
            res = undo_decision(card.batch_ids[-1], via=via, db=db, client=client, today=today)
        elif action == "STOP":
            revoked = [p.id for ps in learn.active_permissions(card.entity_id, db).values() for p in ps
                       if learn.revoke_permission(p.id, "STOP", db)]
            audit.log("OWNER_STOP", {"card_id": card.id, "revoked": revoked}, entity_id=card.entity_id, db=db)
            res = {"revoked": revoked, "reply": _say(card, "STOP", db)}
        elif action in QUESTION_BUTTONS or action == "ANSWER":
            state = QUESTION_BUTTONS.get(action) or (parsed or {}).get("answer")
            state = {"other": "special"}.get(state, state)
            if not card.question or state not in QUESTION_FACT:
                return {"handled": False, "reason": "no question to answer"}
            res = handle_decision(card.id, {"answer": {card.question["date"]: state}}, via=via, db=db,
                                  client=client, today=today)
        else:
            return {"handled": False, "reason": f"nothing to do for {action!r}"}
    except DecisionError as e:
        return {"handled": False, "reason": str(e), "action": action}
    return {"handled": True, "action": action, "result": res}
