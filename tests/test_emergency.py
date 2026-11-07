"""B8 emergency: parsing, applying at once, sender check, rate limit, expiry, undo, no reason stored."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from cs import data, decide_flow, emergency, llm
from tests.test_season import EID, live

TODAY = date.today()
T0, T1 = TODAY.isoformat(), (TODAY + timedelta(days=1)).isoformat()


def _mobile(eid: str = EID) -> str:
    return data.contacts().set_index("entity_id").loc[eid]["mobile"]


@pytest.mark.parametrize("text, lang, start", [
    ("kiinni tänään", "fi", 0), ("suljettu huomenna", "fi", 1), ("stängt idag", "sv", 0),
    ("stengt i dag", "nb", 0), ("closed today", "en", 0),
])
def test_parse_emergency_texts(text, lang, start):
    r = llm.parse_reply_regex(text, lang, [], today=TODAY)
    assert r and r["action"] == "EMERGENCY"
    assert r["emergency"]["from"] == (TODAY + timedelta(days=start)).isoformat() and r["emergency"]["hours"] == "closed"


def test_sender_check():
    assert emergency.sender_ok(EID, "SMS", _mobile())
    assert emergency.sender_ok(EID, "WHATSAPP", _mobile().replace("+", "00"))
    assert not emergency.sender_ok(EID, "SMS", "+358401234567")
    assert not emergency.sender_ok(EID, "SMS", None)


def test_plan_modes():
    D = timedelta
    p = emergency._plan(TODAY, TODAY + D(days=3), False, TODAY)
    assert p["mode"] == "holidayHours" and len(p["dates"]) == 4 and not p["notes"]
    p = emergency._plan(TODAY, TODAY + D(days=9), False, TODAY)              # long, from today
    assert p["mode"] == "reopenDate" and p["reopen_date"] == (TODAY + D(days=10)).isoformat()
    p = emergency._plan(TODAY + D(days=1), TODAY + D(days=9), False, TODAY)  # long, from a later day
    assert p["mode"] == "holidayHours" and len(p["dates"]) == 9
    p = emergency._plan(TODAY + D(days=1), TODAY + D(days=40), False, TODAY)
    assert len(p["dates"]) == emergency.MAX_DAYS and "capped" in p["notes"][0]
    p = emergency._plan(TODAY, None, True, TODAY)                            # until further notice
    assert p["mode"] == "reopenDate" and p["reopen_date"] == (TODAY + D(days=emergency.MAX_DAYS)).isoformat()
    assert emergency._plan(TODAY + D(days=1), None, True, TODAY)["mode"] == "holidayHours"
    with pytest.raises(emergency.EmergencyError):
        emergency._plan(TODAY, TODAY - D(days=1), False, TODAY)


def reopen(flow) -> str | None:
    with flow["client"]() as c:
        return (c.get_entity(EID).get("hours") or {}).get("reopenDate")


@pytest.mark.mongo
def test_applies_at_once_and_confirms(flow):
    res = emergency.handle_emergency(EID, via="web", verified=True)
    assert res["status"] == "APPLIED" and res["dates"] == [T0] and live(flow) == {T0: "closed"}
    assert res["message"] and res["where_it_shows"][0]["status"] == "LIVE"
    row = flow["db"].outbox.find_one({"entity_id": EID, "kind": "EMERGENCY"})
    assert row and row["card_id"] == f"em:{res['id']}"
    from cs import audit
    assert audit.events(entity_id=EID, event_type="EMERGENCY_APPLIED", db=flow["db"])


@pytest.mark.mongo
def test_open_shorter_and_a_range(flow):
    res = emergency.handle_emergency(EID, TODAY, "12-16", via="web", verified=True)
    assert live(flow)[T0] == "12:00-16:00"
    res = emergency.handle_emergency(EID, TODAY + timedelta(days=2), until=TODAY + timedelta(days=4), via="web",
                                     verified=True)
    assert len(res["dates"]) == 3 and all(live(flow)[d] == "closed" for d in res["dates"])


@pytest.mark.mongo
def test_past_and_invalid(flow):
    with pytest.raises(emergency.EmergencyError) as e:
        emergency.handle_emergency(EID, TODAY - timedelta(days=1), verified=True)
    assert e.value.kind == "past"
    with pytest.raises(emergency.EmergencyError) as e:
        emergency.handle_emergency(EID, hours="25-99", verified=True)
    assert e.value.kind == "invalid"


@pytest.mark.mongo
def test_spoofed_sender_needs_confirmation(flow):
    r = decide_flow.handle_reply(EID, text="kiinni tänään", channel="SMS", sender="+358401234567")
    em = r["result"]
    assert em["status"] == "NEEDS_CONFIRM" and "/em/" in em["confirm_link"] and live(flow) == {}
    res = emergency.confirm_emergency(em["id"])
    assert res["status"] == "APPLIED" and live(flow) == {T0: "closed"}
    with pytest.raises(emergency.EmergencyError):
        emergency.confirm_emergency(em["id"])


@pytest.mark.mongo
def test_confirm_link_routes(flow):
    from app.app import app
    from cs import links
    c = app.test_client()
    em = emergency.handle_emergency(EID, sender="+358401234567", via="SMS")
    token = em["confirm_link"].split("/em/")[1]
    assert c.get(f"/em/{token}").status_code == 200 and live(flow) == {}          # GET changes nothing
    r = c.post(f"/em/{token}/confirm")
    assert r.status_code == 200 and "Saved" in r.get_data(as_text=True) and live(flow) == {T0: "closed"}
    assert c.post(f"/em/{token}/confirm").status_code == 409                      # single use
    assert c.get(f"/em/{links.sign(em['id'], 'owner:SOMEONE')}").status_code == 400


@pytest.mark.mongo
def test_rate_limit(flow):
    for _ in range(emergency.MAX_PER_DAY):
        emergency.handle_emergency(EID, via="web", verified=True)
    with pytest.raises(emergency.EmergencyError) as e:
        emergency.handle_emergency(EID, via="web", verified=True)
    assert e.value.kind == "rate_limited"


@pytest.mark.mongo
def test_no_reason_is_stored_anywhere(flow):
    reason = "olen sairastunut flunssaan"
    r = decide_flow.handle_reply(EID, text=f"kiinni tänään, {reason}", channel="SMS", sender=_mobile())
    assert r["handled"] and r["result"]["status"] == "APPLIED"
    for coll in ("outbox", "emergencies", "audit_events", "messages", "reply_context", "decisions", "cards"):
        for doc in flow["db"][coll].find({}):
            assert "sairastunut" not in str(doc) and "flunssa" not in str(doc), coll
    inbound = flow["db"].outbox.find_one({"entity_id": EID, "direction": "in"})
    assert inbound["text"] == "[emergency request; the text is not stored]"


@pytest.mark.mongo
def test_undo_restores_previous_values(flow):
    with flow["client"]() as c:
        c.persist = False
        c.set_holiday_hours(EID, {T1: "09:00-12:00"})
    res = emergency.handle_emergency(EID, TODAY, until=TODAY + timedelta(days=1), via="web", verified=True)
    assert live(flow) == {T0: "closed", T1: "closed"}
    r = decide_flow.handle_reply(EID, text="1", channel="SMS", sender=_mobile())   # "1 = Undo" on the confirmation
    assert r["action"] == "UNDO" and r["result"]["status"] == "UNDONE"
    assert live(flow) == {T1: "09:00-12:00"}                      # removed again / put back
    assert emergency.get(res["id"])["status"] == "UNDONE"


@pytest.mark.mongo
def test_expires_by_itself(flow):
    emergency.handle_emergency(EID, via="web", verified=True)
    assert live(flow) == {T0: "closed"}
    later = (TODAY + timedelta(days=10)).isoformat()
    with flow["client"]() as c:          # any later write: yesterday is in the past, so it is dropped
        c.persist = False
        rec = c.set_holiday_hours(EID, {later: "closed"}, today=TODAY + timedelta(days=1))
    assert T0 not in {i["date"] for i in rec.request["body"]["hours"]["holidayHours"]}
    assert T0 not in live(flow)


@pytest.mark.mongo
def test_until_further_notice_sets_reopen_date_and_asks_daily(flow):
    res = emergency.handle_emergency(EID, until_further_notice=True, via="web", verified=True)
    assert res["mode"] == "reopenDate" and res["schedule_id"]
    assert reopen(flow) == (TODAY + timedelta(days=emergency.MAX_DAYS)).isoformat() and live(flow) == {}
    asked = emergency.run_due_schedules(TODAY)
    assert asked and asked[0]["asked_for"] == T1
    assert emergency.run_due_schedules(TODAY) == []                       # once a day
    q = flow["db"].outbox.find_one({"entity_id": EID, "kind": "QUESTION"})
    assert q and q["card_id"] == f"em:{res['id']}"
    r = decide_flow.handle_reply(EID, text="1", channel="SMS", sender=_mobile())    # 1 = still closed
    assert r["action"] == "OPEN_AGAIN" and r["result"]["status"] == "STILL_CLOSED"
    r = decide_flow.handle_reply(card_id=f"em:{res['id']}", button_id="Q_REGULAR", channel="WHATSAPP")
    assert r["result"]["status"] == "ENDED" and reopen(flow) == T1        # open again from tomorrow
    assert flow["db"].schedules.count_documents({"active": True}) == 0


@pytest.mark.mongo
def test_long_closure_undo_clears_reopen_date(flow):
    res = emergency.handle_emergency(EID, until=TODAY + timedelta(days=9), via="web", verified=True)
    assert res["mode"] == "reopenDate" and reopen(flow) == (TODAY + timedelta(days=10)).isoformat()
    emergency.undo_emergency(res["id"])
    assert reopen(flow) is None


@pytest.mark.mongo
def test_holiday_closure_until_further_notice_from_tomorrow(flow):
    res = emergency.handle_emergency(EID, TODAY + timedelta(days=1), until_further_notice=True, via="web", verified=True)
    assert res["mode"] == "holidayHours" and len(res["dates"]) == emergency.MAX_DAYS
    r = emergency.answer_open_again(res["id"], "regular", today=TODAY)
    assert r["status"] == "ENDED" and live(flow) == {}


@pytest.mark.mongo
def test_tick_script(flow, capsys):
    import importlib.util

    from cs.config import settings
    emergency.handle_emergency(EID, until_further_notice=True, via="web", verified=True)
    spec = importlib.util.spec_from_file_location("tick", settings.root / "scripts" / "tick.py")
    tick = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tick)
    assert tick.main(["--today", T0]) == 0
    assert "1 question(s) sent" in capsys.readouterr().out
