"""B8 – "Closed today": an owner-started change, applied at once, expiring by itself.

    handle_emergency("FI-D0001", day=today, hours="closed", via="web", verified=True)
    undo_emergency(emergency_id)
    answer_open_again(emergency_id, "regular" | "closed")     # the daily "open again?" answer
    run_due_schedules(today)                                   # scripts/tick.py

Rules:
- **Sender check.** It is applied at once only if it comes from a signed link (the card page) or from
  the owner's registered channel (``sender_ok``: the contact's mobile / e-mail, or the configured
  test phone). Otherwise it is stored as NEEDS_CONFIRM and a confirm link goes to the registered
  channel; ``confirm_emergency`` applies it.
- **Rate limit.** At most ``MAX_PER_DAY`` (3) emergencies per business per day.
- **What is written.** Up to 6 days, or any closure that starts later: one holidayHours item per day
  (``closed`` or the given hours, at most 14 days); nothing has to be restored afterwards, because
  past dates stop showing and Yext drops them. A longer closure from today, or "until further
  notice", sets ``hours.reopenDate`` (the listing shows temporarily closed); "until further notice"
  uses a rolling date 14 days out and asks every day whether they open again (``schedules``).
- **No reason is ever stored.** The functions take no reason; callers pass only the parsed dates
  and hours, and the inbound text of an emergency reply is stored redacted (see decide_flow).
- **Confirmation** by ``cs.compose.compose_emergency``: fast publishers by name and time, slower ones
  named honestly (they won't catch a same-day closure). Undo restores what was live before.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

from pymongo.database import Database

from cs import audit, compose_bridge, data, db as dbmod, links, season
from cs.domain import new_id, utcnow
from cs.hours import CLOSED, normalise

MAX_PER_DAY = 3
MAX_DAYS = 14          # holidayHours window until reopenDate can be written
SHORT_RANGE = 6        # longer than this would be a reopenDate in production
OPEN_AGAIN = "OPEN_AGAIN_QUESTION"


class EmergencyError(ValueError):
    """``kind``: rate_limited | invalid | past | not_found | state."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def _digits(x: str | None) -> str:
    d = re.sub(r"\D", "", x or "")
    return d[2:] if d.startswith("00") else d


def sender_ok(entity_id: str, channel: str, sender: str | None) -> bool:
    """Is ``sender`` the owner's registered address on that channel (or the demo test phone/inbox)?"""
    if not sender:
        return False
    c = data.contacts()
    row = c[c["entity_id"] == entity_id]
    known: set[str] = set()
    if not row.empty:
        r = row.iloc[0]
        known |= {_digits(r.get("mobile")), (r.get("email") or "").strip().lower()}
    known |= {_digits(os.environ.get("WHATSAPP_TEST_TO")), (os.environ.get("SMTP_TEST_TO") or "").strip().lower()}
    known.discard("")
    s = sender.strip().lower()
    return (_digits(s) in known if channel.upper() in ("SMS", "WHATSAPP") else s in known) and bool(s)


def _today_count(entity_id: str, today: date, db: Database) -> int:
    start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=3)
    return db.emergencies.count_documents({"entity_id": entity_id, "created_at": {"$gte": start},
                                           "status": {"$in": ["APPLIED", "NEEDS_CONFIRM", "UNDONE"]}})


def _plan(day: date, until: date | None, further: bool, today: date) -> dict:
    """{mode: holidayHours | reopenDate, dates, reopen_date, notes}."""
    end = until or day
    if end < day:
        raise EmergencyError("invalid", "the end date is before the start date")
    n = (end - day).days + 1
    notes: list[str] = []
    if day == today and (further or n > SHORT_RANGE):
        if further:
            end = day + timedelta(days=MAX_DAYS - 1)
            notes.append(f"until further notice: reopenDate {MAX_DAYS} days out (rolling) and a daily 'open again?' question")
        else:
            notes.append(f"{n} days from today: written as reopenDate")
        dates = [(day + timedelta(days=i)).isoformat() for i in range((end - day).days + 1)]
        return {"mode": "reopenDate", "dates": dates, "reopen_date": (end + timedelta(days=1)).isoformat(),
                "notes": notes}
    if further:
        end = day + timedelta(days=MAX_DAYS - 1)
        notes.append(f"until further notice from a later day: {MAX_DAYS} days of holidayHours and a daily question")
    elif n > MAX_DAYS:
        end = day + timedelta(days=MAX_DAYS - 1)
        notes.append(f"{n} days asked from a later day; capped at {MAX_DAYS} days of holidayHours")
    dates = [(day + timedelta(days=i)).isoformat() for i in range((end - day).days + 1)]
    return {"mode": "holidayHours", "dates": dates, "reopen_date": None, "notes": notes}


def _dates(day: date, until: date | None, further: bool) -> tuple[list[str], list[str]]:
    """Back-compatible view of ``_plan`` for a closure that does not start today."""
    p = _plan(day, until, further, day - timedelta(days=1))
    return p["dates"], p["notes"]


def _where(entity_id: str, target: str, at) -> list[dict]:
    from cs import confirm
    return confirm.where_it_shows(entity_id, at, target=target)


@audit.batched
def handle_emergency(entity_id: str, day: date | str | None = None, hours: str = CLOSED,
                     until: date | str | None = None, until_further_notice: bool = False, *,
                     via: str = "web", target: str | None = None, verified: bool = False,
                     sender: str | None = None, db: Database | None = None, client=None,
                     today: date | None = None, base_url: str | None = None) -> dict:
    """Apply (or, for an unverified sender, hold for confirmation) an owner's emergency change."""
    db = _db(db)
    today = today or date.today()
    d = data.to_date(day) if day else today
    if d < today:
        raise EmergencyError("past", "that day has already passed")
    try:
        hours = CLOSED if (hours or CLOSED).strip().lower() == CLOSED else normalise(hours)
    except ValueError:
        raise EmergencyError("invalid", f"not valid hours: {hours!r}") from None
    plan = _plan(d, data.to_date(until) if until else None, until_further_notice, today)
    dates, notes = plan["dates"], plan["notes"]
    if _today_count(entity_id, today, db) >= MAX_PER_DAY:
        audit.log("EMERGENCY_RATE_LIMITED", {"via": via}, entity_id=entity_id, db=db)
        raise EmergencyError("rate_limited", f"at most {MAX_PER_DAY} emergency changes a day; please call us")
    target = target or season.default_target(entity_id)
    audit.log("EMERGENCY_RECEIVED", {"summary": f"owner: {hours} {dates[0]}" + (f"–{dates[-1]}" if len(dates) > 1 else ""),
                                     "via": via, "dates": dates, "hours": hours}, entity_id=entity_id, db=db)
    em = {"id": new_id("em"), "entity_id": entity_id, "dates": dates, "hours": hours, "from": dates[0],
          "until": dates[-1], "until_further_notice": bool(until_further_notice), "via": via,
          "verified": bool(verified or sender_ok(entity_id, via, sender)), "target": target,
          "status": "NEW", "notes": notes, "previous": None, "write_id": None, "mode": plan["mode"],
          "reopen_date": plan["reopen_date"], "previous_reopen": None,
          "created_at": utcnow(), "expires_on": (date.fromisoformat(dates[-1]) + timedelta(days=1)).isoformat()}
    if not em["verified"]:
        em["status"] = "NEEDS_CONFIRM"
        db.emergencies.insert_one(dict(em))
        token = links.sign(em["id"], f"owner:{entity_id}", ttl=timedelta(hours=12))
        link = f"{(base_url or links.settings.public_base_url).rstrip('/')}/em/{token}"
        contact = compose_bridge.contact(entity_id)
        compose_bridge.send(entity_id, {"text": f"Fonecta Connect: confirm the change for {dates[0]}: {link}",
                                        "buttons": [], "composer": "emergency"},
                            kind="FOLLOWUP", card_id=f"em:{em['id']}", lang=contact["lang"], channel=contact["channel"],
                            db=db)
        audit.log("EMERGENCY_NEEDS_CONFIRM", {"emergency_id": em["id"], "via": via, "dates": dates},
                  entity_id=entity_id, db=db)
        return {k: v for k, v in em.items() if k != "_id"} | {"confirm_link": link}
    db.emergencies.insert_one(dict(em))
    return _apply(em, db, client, today)


@audit.batched
def confirm_emergency(emergency_id: str, *, db: Database | None = None, client=None, today: date | None = None) -> dict:
    db = _db(db)
    em = db.emergencies.find_one({"id": emergency_id}, {"_id": 0})
    if em is None:
        raise EmergencyError("not_found", "unknown request")
    if em["status"] != "NEEDS_CONFIRM":
        raise EmergencyError("state", f"this request is already {em['status'].lower()}")
    em["verified"] = True
    db.emergencies.update_one({"id": emergency_id}, {"$set": {"verified": True}})
    return _apply(em, db, client, today or date.today())


def _apply(em: dict, db: Database, client, today: date) -> dict:
    eid = em["entity_id"]
    own = client is None
    yc = client or season.yext_client(em["target"], db)
    try:
        if em.get("mode") == "reopenDate":
            rec = yc.set_reopen_date(eid, em["reopen_date"], em["id"], today=today)
            previous: dict = {}
            em["previous_reopen"] = rec.request.get("previous")
            ok = bool(rec.readback_ok)
        else:
            live = yc.get_holiday_hours(eid)
            previous = {d: live.get(d) for d in em["dates"]}
            rec = yc.set_holiday_hours(eid, {d: em["hours"] for d in em["dates"]}, None, em["id"], today=today)
            ok = bool(rec.readback_ok and rec.request.get("applied"))
    finally:
        if own:
            yc.close()
    em.update(status="APPLIED" if ok else "FAILED", previous=previous, write_id=rec.id)
    db.emergencies.update_one({"id": em["id"]}, {"$set": {"status": em["status"], "previous": previous,
                                                          "previous_reopen": em.get("previous_reopen"),
                                                          "write_id": rec.id, "verified": True}})
    where = _where(eid, em["target"], rec.at)
    audit.log("EMERGENCY_APPLIED" if ok else "EMERGENCY_FAILED",
              {"emergency_id": em["id"], "dates": em["dates"], "hours": em["hours"], "via": em["via"],
               "until_further_notice": em["until_further_notice"], "write_id": rec.id, "notes": em["notes"]},
              entity_id=eid, db=db)
    out = {k: v for k, v in em.items() if k != "_id"}
    out["where_it_shows"] = [{**r, "expected_live_at": r["expected_live_at"].isoformat() if r.get("expected_live_at") else None}
                             for r in where]
    if not ok:
        out["message"] = None
        return out
    contact = compose_bridge.contact(eid)
    msg = _compose(em, contact, where, today)
    sent = compose_bridge.send(eid, msg, kind="EMERGENCY", card_id=f"em:{em['id']}", lang=contact["lang"],
                               channel=contact["channel"], db=db)
    out["message"] = msg["text"]
    out["message_id"] = sent["message_id"]
    audit.log("MESSAGE_SENT", {"card_id": f"em:{em['id']}", "message_id": sent["message_id"], "kind": "EMERGENCY",
                               "channel": contact["channel"], "delivery": sent["status"], "via": sent["delivered_via"]},
              entity_id=eid, db=db)
    if em["until_further_notice"]:
        sched = {"id": new_id("sch"), "kind": OPEN_AGAIN, "entity_id": eid, "emergency_id": em["id"], "active": True,
                 "next_run": today.isoformat(), "asked_for": None, "created_at": utcnow()}
        db.schedules.insert_one(dict(sched))
        out["schedule_id"] = sched["id"]
        audit.log("SCHEDULE_CREATED", {"schedule_id": sched["id"], "kind": OPEN_AGAIN, "emergency_id": em["id"]},
                  entity_id=eid, db=db)
    return out


def _compose(em: dict, contact: dict, where: list[dict], today: date) -> dict:
    try:
        from cs import compose
        res = compose.compose_emergency(compose.business_for(em["entity_id"]), em["from"], contact["lang"],
                                        hours=em["hours"], until=None if em["until_further_notice"] else em["until"],
                                        until_further_notice=em["until_further_notice"], today=today,
                                        where_it_shows=where, channel=contact["channel"])
        return {**res, "composer": "cs.compose"}
    except Exception:  # noqa: BLE001
        span = em["from"] if em["from"] == em["until"] else f"{em['from']}–{em['until']}"
        return {"text": f"✓ Saved: {em['hours']} {span}. You can undo.", "buttons": [{"id": "UNDO", "title": "Undo"}],
                "parts": {}, "composer": "fallback"}


@audit.batched
def undo_emergency(emergency_id: str, *, db: Database | None = None, client=None, today: date | None = None,
                   from_date: str | None = None) -> dict:
    """Put back what was live before: removed again if there was no exception, the old value
    otherwise, or the previous reopenDate. ``from_date`` ends the closure from that day on (the
    owner says they open again); it is used by ``answer_open_again``."""
    db = _db(db)
    today = today or date.today()
    em = db.emergencies.find_one({"id": emergency_id}, {"_id": 0})
    if em is None:
        raise EmergencyError("not_found", "unknown request")
    if em["status"] != "APPLIED":
        raise EmergencyError("state", f"nothing to undo (status {em['status'].lower()})")
    start = max(today.isoformat(), from_date or em["from"])
    own = client is None
    yc = client or season.yext_client(em["target"], db)
    try:
        if em.get("mode") == "reopenDate":
            back = em.get("previous_reopen") if from_date is None else start
            if back and back < today.isoformat():
                back = None
            rec = yc.set_reopen_date(em["entity_id"], back, f"undo:{emergency_id}", today=today)
            restored = {"reopenDate": back}
            left_alone: dict = {}
            write_ids = [rec.id]
        else:
            ds = [d for d in em["dates"] if d >= start]
            if not ds:
                raise EmergencyError("state", "those days have passed")
            r = season.restore_previous(yc, em["entity_id"], {d: (em["previous"] or {}).get(d) for d in ds},
                                        {d: em["hours"] for d in ds}, f"undo:{emergency_id}", today)
            restored, left_alone, write_ids = r["restored"], r["left_alone"], r["write_ids"]
    finally:
        if own:
            yc.close()
    status = "UNDONE" if from_date is None else "ENDED"
    db.emergencies.update_one({"id": emergency_id}, {"$set": {"status": status, "undo_write_ids": write_ids}})
    db.schedules.update_many({"emergency_id": emergency_id}, {"$set": {"active": False}})
    audit.log("EMERGENCY_UNDONE" if from_date is None else "EMERGENCY_ENDED",
              {"emergency_id": emergency_id, "restored": restored, "write_ids": write_ids},
              entity_id=em["entity_id"], db=db)
    contact = compose_bridge.contact(em["entity_id"])
    msg = compose_bridge.followup_message("UNDO", contact["lang"], contact["channel"])
    compose_bridge.send(em["entity_id"], msg, kind="FOLLOWUP", card_id=f"em:{emergency_id}", lang=contact["lang"],
                        channel=contact["channel"], db=db)
    return {"emergency_id": emergency_id, "status": status, "restored": restored, "left_alone": left_alone}


# ---------------------------------------------------------------------------
# "Until further notice": the daily question
# ---------------------------------------------------------------------------
@audit.batched
def run_due_schedules(today: date | None = None, db: Database | None = None) -> list[dict]:
    """Send every due "Open again tomorrow?" question (one per active schedule per day)."""
    db = _db(db)
    today = today or date.today()
    out = []
    for sch in db.schedules.find({"active": True, "kind": OPEN_AGAIN, "next_run": {"$lte": today.isoformat()}},
                                 {"_id": 0}):
        em = db.emergencies.find_one({"id": sch["emergency_id"]}, {"_id": 0}) or {}
        if em.get("status") != "APPLIED":
            db.schedules.update_one({"id": sch["id"]}, {"$set": {"active": False}})
            continue
        tomorrow = (today + timedelta(days=1)).isoformat()
        contact = compose_bridge.contact(sch["entity_id"])
        q = {"date": tomorrow}
        try:
            from cs import compose
            msg = {**compose.compose_question(q, contact["lang"], contact["channel"]), "composer": "cs.compose"}
        except Exception:  # noqa: BLE001
            msg = {"text": f"Are you open again on {tomorrow}? 1 = still closed, 2 = regular hours, 3 = other hours",
                   "buttons": [], "parts": {}, "composer": "fallback"}
        compose_bridge.send(sch["entity_id"], msg, kind="QUESTION", card_id=f"em:{em['id']}", lang=contact["lang"],
                            channel=contact["channel"], db=db)
        db.schedules.update_one({"id": sch["id"]}, {"$set": {"asked_for": tomorrow,
                                                             "next_run": (today + timedelta(days=1)).isoformat()}})
        audit.log("OPEN_AGAIN_ASKED", {"schedule_id": sch["id"], "emergency_id": em["id"], "for": tomorrow},
                  entity_id=sch["entity_id"], db=db)
        out.append({"schedule_id": sch["id"], "entity_id": sch["entity_id"], "asked_for": tomorrow})
    return out


@audit.batched
def answer_open_again(emergency_id: str, state: str, *, db: Database | None = None, client=None,
                      today: date | None = None) -> dict:
    """'regular' / 'special' (open again): restore from the asked day on and stop asking.
    'closed': keep the closure (and keep asking)."""
    db = _db(db)
    today = today or date.today()
    sch = db.schedules.find_one({"emergency_id": emergency_id, "active": True}, {"_id": 0})
    if state == "closed":
        em = db.emergencies.find_one({"id": emergency_id}, {"_id": 0}) or {}
        if em.get("mode") == "reopenDate" and em.get("reopen_date") and \
                date.fromisoformat(em["reopen_date"]) - today <= timedelta(days=3):
            new = (today + timedelta(days=MAX_DAYS)).isoformat()   # roll the reopen date forward
            own = client is None
            yc = client or season.yext_client(em["target"], db)
            try:
                yc.set_reopen_date(em["entity_id"], new, emergency_id, today=today)
            finally:
                if own:
                    yc.close()
            db.emergencies.update_one({"id": emergency_id}, {"$set": {"reopen_date": new}})
        audit.log("OPEN_AGAIN_ANSWERED", {"emergency_id": emergency_id, "answer": "closed"},
                  entity_id=(sch or {}).get("entity_id"), db=db)
        return {"emergency_id": emergency_id, "status": "STILL_CLOSED"}
    start = (sch or {}).get("asked_for") or (today + timedelta(days=1)).isoformat()
    res = undo_emergency(emergency_id, db=db, client=client, today=today, from_date=start)
    audit.log("OPEN_AGAIN_ANSWERED", {"emergency_id": emergency_id, "answer": state, "from": start},
              entity_id=(sch or {}).get("entity_id"), db=db)
    return res


def get(emergency_id: str, db: Database | None = None) -> dict | None:
    return _db(db).emergencies.find_one({"id": emergency_id}, {"_id": 0})
