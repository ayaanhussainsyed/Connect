"""B1 autopilot: CRM permission (FI-D0012), the agreement rule, short window, undo/stop, time machine."""
from __future__ import annotations

from datetime import date

import pytest

from cs import autopilot, db as dbmod, decide_flow, learn, season
from cs.domain import Card, LearnedFact, Permission
from cs.suggest import engine_adapter

pytestmark = pytest.mark.skipif(not engine_adapter.available(2026), reason="engine_2026.parquet not present")
OTSO = "FI-D0012"
TODAY = date(2026, 11, 7)


def live(flow, eid=OTSO) -> dict:
    with flow["client"]() as c:
        return c.get_holiday_hours(eid)


def test_newest_facts_decay(monkeypatch):
    facts = [LearnedFact(entity_id="X", day_key="XMAS_DAY", date="2025-12-25", value="closed", season=2025),
             LearnedFact(entity_id="X", day_key="NYE", date="2022-12-31", value="closed", season=2022),
             LearnedFact(entity_id="X", day_key="EPIPHANY", date="2026-01-06", value="10:00-14:00", season=2026)]
    monkeypatch.setattr(learn, "active_facts", lambda eid, db=None: facts)
    got = autopilot.newest_facts("X", 2026)
    assert set(got) == {"XMAS_DAY", "EPIPHANY"}                      # 4 seasons old: dropped
    assert got["XMAS_DAY"][1] == pytest.approx(0.95) and got["EPIPHANY"][1] == 1.0


@pytest.mark.mongo
def test_crm_permission_applies_public_holidays(flow):
    res = autopilot.run_autopilot(OTSO, 2026, target="mock", today=TODAY, time_machine=True)
    # The rule, not a fixed count (the engine file may change): only PUBLIC_HOLIDAY dates with a 2025
    # pilot answer, with exactly that answer, and only where this season's engine agrees (P >= 0.5).
    from cs import data, guardrails
    t = data.targets(2026)
    public = set(t[(t["entity_id"] == OTSO) & (t["day_type"] == "PUBLIC_HOLIDAY")]["date"])
    facts = {f.day_key: f.value for f in learn.active_facts(OTSO)}
    rows = {s.date: s for s in engine_adapter.suggestions(2026, [OTSO])}
    assert res["applied"] and set(res["applied"]) <= public
    for d, v in res["applied"].items():
        assert v == facts[rows[d].day_key] and guardrails.engine_p(rows[d], v) >= 0.5 and rows[d].shown != v
    assert res["skipped"]["2026-12-26"] == "already shown correctly"          # a Saturday: already closed
    assert "2026-12-24" not in res["applied"]                                 # an EVE, not a PUBLIC_HOLIDAY
    assert live(flow) == res["applied"]
    assert {p["source"] for p in res["permissions"]} == {autopilot.CRM_SOURCE}  # time machine not for CRM owners
    perm = dbmod.find(Permission, {"entity_id": OTSO, "source": autopilot.CRM_SOURCE})[0]
    assert perm.status == "USED" and perm.uses and perm.scope["day_types"] == ["PUBLIC_HOLIDAY"]
    assert {f.source for f in learn.active_facts(OTSO)} == {"PILOT_2025"}
    card = season.load_card(res["card_id"])
    assert card.status == "CONFIRMED" and card.batch_ids
    note = flow["db"].outbox.find_one({"entity_id": OTSO, "kind": "AUTOPILOT"})
    assert note and note["card_id"] == card.id and res["message"]


@pytest.mark.mongo
def test_agreement_rule_sends_doubtful_dates_to_a_card(flow):
    autopilot.crm_permission(OTSO)
    learn.record_fact(OTSO, "XMAS_DAY", "2025-12-25", "10:00-14:00", 2025, source="OWNER_ANSWER")  # engine disagrees
    res = autopilot.run_autopilot(OTSO, 2026, target="mock", today=TODAY)
    assert "2026-12-25" in res["ask"] and "2026-12-25" not in res["applied"]
    assert res["followup_card_id"]
    fu = season.load_card(res["followup_card_id"])
    assert [i["date"] for i in fu.items] == ["2026-12-25"] and fu.status == "SENT"
    from cs import audit
    ev = audit.events(entity_id=OTSO, event_type="AUTOPILOT_ASK", db=flow["db"])[0]["payload"]
    assert ev["date"] == "2026-12-25" and "P=" in ev["reason"]


@pytest.mark.mongo
def test_short_window_is_applied_and_logged(flow):
    today = date(2026, 12, 20)
    res = autopilot.run_autopilot(OTSO, 2026, target="mock", today=today)
    assert res["applied"]
    for d in res["applied"]:
        assert (d in res["short_window"]) == ((date.fromisoformat(d) - today).days < autopilot.MIN_DAYS_AHEAD)
    assert set(res["short_window"]) <= set(res["applied"])


@pytest.mark.mongo
def test_undo_and_stop(flow):
    res = autopilot.run_autopilot(OTSO, 2026, target="mock", today=TODAY)
    r = decide_flow.handle_reply(OTSO, text="1", channel="SMS", today=TODAY)       # 1 = Undo on the note
    assert r["action"] == "UNDO" and r["result"]["status"] == "UNDONE"
    assert live(flow) == {}
    r = decide_flow.handle_reply(card_id=res["card_id"], button_id="STOP", channel="WHATSAPP")
    assert r["handled"] and r["result"]["revoked"]
    again = autopilot.run_autopilot(OTSO, 2026, target="mock", today=TODAY)
    assert again["permissions"] == [] and again["applied"] == {}


@pytest.mark.mongo
def test_time_machine_for_fi_d0001(flow):
    res = autopilot.run_autopilot("FI-D0001", 2026, target="mock", today=TODAY, time_machine=True)
    perm = dbmod.find(Permission, {"entity_id": "FI-D0001", "source": autopilot.TIME_MACHINE_SOURCE})[0]
    assert perm.scope["values"]["XMAS_EVE"] == "07:30-13:00"
    assert res["applied"].get("2026-12-24") == "07:30-13:00"
    assert all(v == perm.scope["values"][dk] for d, v in res["applied"].items()
               for dk in [next(i["day_key"] for i in season.load_card(res["card_id"]).items if i["date"] == d)])


@pytest.mark.mongo
def test_no_permission(flow):
    res = autopilot.run_autopilot("FI-D0013", 2026, target="mock", today=TODAY)
    assert res["permissions"] == [] and res["reason"] == "no standing permission"
    assert dbmod.find(Card, {"entity_id": "FI-D0013"}) == []
