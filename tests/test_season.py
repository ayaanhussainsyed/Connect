"""B1 card builder + A8/A9/A11 decision flow, against a private Yext mock and the test database."""
from __future__ import annotations

from datetime import date

import pytest

from cs import confirm, db as dbmod, decide_flow, learn, season
from cs.domain import Card, Decision, LearnedFact, Permission
from cs.hours import REGULAR

EID = "FI-D0001"
REG = "07:30-18:00"  # FI-D0001's Thursday/Friday regular hours shown on these dates


def test_is_flip():
    assert season.is_flip("07:30-18:00", "closed") and season.is_flip("closed", "10:00-14:00")
    assert not season.is_flip("07:30-18:00", "07:30-13:00") and not season.is_flip("closed", "closed")
    assert not season.is_flip(None, "closed")


def item(d: str, value: str, *, day_key: str = "XMAS_EVE", shown: str = REG, basis: str = "OWN_LAST_YEAR",
         tick: bool = False) -> dict:
    return {"suggestion_id": f"sug:test:{d}", "date": d, "day_key": day_key, "shown": shown,
            "shown_source": "REGULAR_HOURS", "regular": shown, "value": value, "basis": basis, "probability": None,
            "action": "SUGGEST", "evidence": [{"source": basis, "detail": "test", "ref": None, "weight": 1.0}],
            "flip": season.is_flip(shown, value), "flip_tick_required": tick, "deadline": d}


def make_card(db, items: list[dict], *, question: dict | None = None, status: str = "SENT",
              expected: dict | None = None, dates_shown: list[str] | None = None) -> Card:
    dates = [i["date"] for i in items] + ([question["date"]] if question else [])
    card = Card(entity_id=EID, season=2026, suggestion_ids=[i["suggestion_id"] for i in items], items=items,
                question=question, status=status, target="mock", owner_id=f"owner:{EID}", lang="fi", channel="SMS",
                expected_current=expected if expected is not None else {d: None for d in dates},
                dates_shown=dates_shown, approve_by="2026-12-17")
    dbmod.save(card, db=db)
    return card


def live(flow) -> dict:
    with flow["client"]() as c:
        return c.get_holiday_hours(EID)


# --- card builder -------------------------------------------------------------------------------
@pytest.mark.mongo
def test_build_card_from_guarded_suggestions(flow):
    with flow["client"]() as c:   # something already live on the listing: the snapshot must show it
        c.persist = False
        c.set_holiday_hours(EID, {"2026-12-24": "08:00-12:00"})
    card = season.build_card(EID, 2026, "baseline", target="mock", ask_question=False)
    assert card.status == "DRAFT" and card.items and card.target == "mock"
    assert all(i["action"] == "SUGGEST" for i in card.items)
    first = min(i["date"] for i in card.items)
    assert card.approve_by == confirm.deadline_for(EID, first).isoformat()
    assert set(card.deadline_by_publisher) == {r["publisher_id"] for r in confirm.where_it_shows(EID) if r["status"] == "LIVE"}
    assert card.expected_current["2026-12-24"] == "08:00-12:00"
    for i in card.items:
        assert i["flip"] == season.is_flip(i["shown"], i["value"])
        assert i["flip_tick_required"] == (i["flip"] and i["basis"] not in season.OWN_HISTORY_BASES)
    saved = season.load_card(card.id)
    assert saved is not None and saved.status == "DRAFT" and saved.items == card.items
    from cs import audit
    assert audit.events(entity_id=EID, event_type="CARD_BUILT", db=flow["db"])[0]["payload"]["card_id"] == card.id


@pytest.mark.mongo
def test_build_card_asks_the_one_question(flow, monkeypatch):
    import cs.engine.api as api
    monkeypatch.setattr(api, "question_value", lambda eid, season, known: [("2026-12-26", 0.65), ("2026-12-24", 0.5)])
    card = season.build_card(EID, 2026, "baseline", target="mock")
    q = card.question
    assert q and q["date"] == "2026-12-26" and q["value"] == 0.65 and q["options"] == ["closed", "regular", "special"]
    assert "2026-12-26" in card.expected_current
    monkeypatch.setattr(api, "question_value", lambda eid, season, known: [("2026-12-26", 0.0)])
    assert season.build_card(EID, 2026, "baseline", target="mock").question is None


@pytest.mark.mongo
def test_build_card_without_engine(flow, monkeypatch):
    import cs.engine.api as api

    def boom(*a, **k):
        raise ImportError("engine missing")
    monkeypatch.setattr(api, "question_value", boom)
    assert season.build_card(EID, 2026, "baseline", target="mock").question is None


@pytest.mark.mongo
@pytest.mark.parametrize("eid, why", [("FI-D0011", "flagged"), ("FI-D0013", "stay quiet")])
def test_no_card_for_flagged_or_quiet(flow, eid, why):
    with pytest.raises(season.NoCard) as e:
        season.build_card(eid, 2026, "baseline", target="mock", ask_question=False)
    assert why in str(e.value)


@pytest.mark.mongo
def test_send_card_stores_message_and_marks_sent(flow):
    card = season.build_card(EID, 2026, "baseline", target="mock", ask_question=False)
    msg, token = season.send_card(card, base_url="http://test")
    assert card.status == "SENT" and card.dates_shown and set(card.dates_shown) <= {i["date"] for i in card.items}
    assert f"/c/{token}" in msg.text or msg.composer == "fallback"
    row = flow["db"].outbox.find_one({"card_id": card.id, "kind": "CARD"})
    assert row and row["token"] == token


# --- decisions ----------------------------------------------------------------------------------
@pytest.mark.mongo
def test_approve_applies_confirms_and_learns(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"),
                                  item("2026-12-25", "closed", day_key="XMAS_DAY")])
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "APPROVE", "2026-12-25": "APPROVE"}})
    assert res["applied"] == {"2026-12-24": "07:30-13:00", "2026-12-25": "closed"}
    assert res["status"] == "CONFIRMED" and res["write"]["readback_ok"] is True
    assert live(flow) == {"2026-12-24": "07:30-13:00", "2026-12-25": "closed"}
    assert res["where_it_shows"] and res["where_it_shows"][0]["status"] == "LIVE"
    decs = dbmod.find(Decision, {"batch_id": res["batch_id"]})
    assert {d.status for d in decs} == {"APPLIED"} and all(d.write_id == res["write"]["id"] for d in decs)
    facts = learn.active_facts(EID)
    assert {(f.day_key, f.value, f.source) for f in facts} >= {("XMAS_EVE", "07:30-13:00", "OWNER_ANSWER"),
                                                             ("XMAS_DAY", "closed", "OWNER_ANSWER")}
    assert flow["db"].outbox.find_one({"card_id": card.id, "kind": "CONFIRMATION"})
    states = [h["to"] for h in season.load_card(card.id).history]
    assert states[-3:] == ["DECIDED", "APPLIED", "CONFIRMED"]


@pytest.mark.mongo
def test_edit_and_invalid_hours(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-31", "10:00-16:00", day_key="NYE")])
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "EDIT:9-14",
                                                             "2026-12-31": {"action": "EDIT", "hours": "25-99"}}})
    assert res["applied"] == {"2026-12-24": "09:00-14:00"}
    assert "2026-12-31" in res["invalid"] and "2026-12-31" not in live(flow)
    d = next(x for x in res["decisions"] if x["date"] == "2026-12-24")
    assert d["decision"] == "EDIT" and d["final_value"] == "09:00-14:00"


@pytest.mark.mongo
def test_decline_changes_nothing(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "DECLINE"}})
    assert res["declined"] == ["2026-12-24"] and res["applied"] == {} and res["write"] is None
    assert res["status"] == "DECIDED" and live(flow) == {}


@pytest.mark.mongo
def test_inferred_flip_needs_a_tick(flow):
    card = make_card(flow["db"], [item("2026-12-26", "closed", day_key="BOXING_DAY", basis="PEERS", tick=True)])
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-26": "APPROVE"}})
    assert res["needs_tick"] == ["2026-12-26"] and res["applied"] == {} and live(flow) == {}
    assert season.load_card(card.id).status == "SENT"          # nothing decided yet
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-26": "APPROVE"}, "ticks": ["2026-12-26"]})
    assert res["applied"] == {"2026-12-26": "closed"}
    # The owner's own edit is explicit: no tick needed.
    card2 = make_card(flow["db"], [item("2026-12-31", "closed", day_key="NYE", basis="PEERS", tick=True)])
    assert decide_flow.handle_decision(card2.id, {"per_date": {"2026-12-31": "EDIT:closed"}})["applied"]


@pytest.mark.mongo
def test_owner_wins_cancels_a_changed_date(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-25", "closed", day_key="XMAS_DAY")])
    with flow["client"]() as c:     # the owner edits the listing after the card was built
        c.persist = False
        c.set_holiday_hours(EID, {"2026-12-24": "06:00-10:00"})
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "APPROVE", "2026-12-25": "APPROVE"}})
    assert "2026-12-24" in res["cancelled"] and res["applied"] == {"2026-12-25": "closed"}
    assert live(flow)["2026-12-24"] == "06:00-10:00"
    assert next(d for d in res["decisions"] if d["date"] == "2026-12-24")["status"] == "CANCELLED"


@pytest.mark.mongo
def test_every_year_then_undo(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-25", "closed", day_key="XMAS_DAY")],
                     expected={"2026-12-24": None, "2026-12-25": None})
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "APPROVE", "2026-12-25": "APPROVE"},
                                                "every_year": True})
    perm = dbmod.get(Permission, res["permission"]["id"])
    assert perm.status == "GRANTED" and perm.scope["values"] == {"XMAS_EVE": "07:30-13:00", "XMAS_DAY": "closed"}
    assert learn.active_permissions(EID)[EID][0].id == perm.id
    u = decide_flow.undo_decision(res["batch_id"])
    assert u["status"] == "UNDONE" and u["revoked_permissions"] == [perm.id]
    assert live(flow) == {}                                              # no exception before -> removed
    assert dbmod.get(Permission, perm.id).status == "REVOKED"
    assert {d.status for d in dbmod.find(Decision, {"batch_id": res["batch_id"]})} == {"UNDONE"}
    assert not [f for f in learn.active_facts(EID) if f.batch_id == res["batch_id"]]
    with pytest.raises(decide_flow.DecisionError):
        decide_flow.undo_decision(res["batch_id"])


@pytest.mark.mongo
def test_undo_restores_the_previous_value(flow):
    with flow["client"]() as c:
        c.persist = False
        c.set_holiday_hours(EID, {"2026-12-24": "08:00-12:00"})
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")], expected={"2026-12-24": "08:00-12:00"})
    res = decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "EDIT:07:30-14:00"}})
    assert live(flow)["2026-12-24"] == "07:30-14:00"
    decide_flow.undo_decision(res["decisions"][0]["id"])            # by decision id works too
    assert live(flow)["2026-12-24"] == "08:00-12:00"


@pytest.mark.mongo
def test_newer_fact_supersedes_older(flow):
    a = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    decide_flow.handle_decision(a.id, {"per_date": {"2026-12-24": "APPROVE"}})
    b = make_card(flow["db"], [item("2026-12-24", "08:00-13:00")], expected={"2026-12-24": "07:30-13:00"})
    decide_flow.handle_decision(b.id, {"per_date": {"2026-12-24": "APPROVE"}})
    facts = dbmod.find(LearnedFact, {"entity_id": EID, "day_key": "XMAS_EVE"}, sort=[("created_at", 1)])
    assert len(facts) == 2 and facts[0].superseded_by == facts[1].id and facts[1].superseded_by is None
    assert [f.value for f in learn.active_facts(EID) if f.day_key == "XMAS_EVE"] == ["08:00-13:00"]


def _q(d="2026-12-26"):
    return {"date": d, "day_key": "BOXING_DAY", "value": 0.6, "shown": REG, "regular": REG,
            "candidate": None, "options": ["closed", "regular", "special"], "answer": None}


@pytest.mark.mongo
def test_answer_that_matches_google_is_only_recorded(flow):
    card = make_card(flow["db"], [], question=_q())
    res = decide_flow.handle_decision(card.id, {"answer": {"2026-12-26": "regular"}})
    assert res["status"] == "QUESTION_ANSWERED" and res["write"] is None and live(flow) == {}
    c = season.load_card(card.id)
    assert c.question["answer"] == "regular"
    assert [f.value for f in learn.active_facts(EID) if f.source == "OWNER_QUESTION"] == [REGULAR]


@pytest.mark.mongo
def test_answer_that_differs_is_applied_to_that_date_only(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")], question=_q())
    res = decide_flow.handle_decision(card.id, {"answer": {"2026-12-26": "closed"}})
    assert res["applied"] == {"2026-12-26": "closed"} and live(flow) == {"2026-12-26": "closed"}
    card2 = make_card(flow["db"], [], question=_q("2026-12-31"))
    res = decide_flow.handle_decision(card2.id, {"answer": {"2026-12-31": "special"}, "answer_hours": "10-15"})
    assert res["applied"] == {"2026-12-31": "10:00-15:00"}


@pytest.mark.mongo
def test_decided_card_cannot_be_decided_again(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "DECLINE"}})
    with pytest.raises(decide_flow.DecisionError):
        decide_flow.handle_decision(card.id, {"per_date": {"2026-12-24": "APPROVE"}})
    with pytest.raises(decide_flow.DecisionError):
        decide_flow.handle_decision("card-nope", {"per_date": {}})


def test_parse_choice():
    assert decide_flow.parse_choice("APPROVE") == ("APPROVE", None)
    assert decide_flow.parse_choice("edit:10-14") == ("EDIT", "10-14")
    assert decide_flow.parse_choice({"action": "EDIT", "hours": "closed"}) == ("EDIT", "closed")
    assert decide_flow.parse_choice(("DECLINE",)) == ("DECLINE", None)
    for bad in ("MAYBE", "EDIT", {"action": "EDIT"}):
        with pytest.raises(decide_flow.DecisionError):
            decide_flow.parse_choice(bad)


# --- replies by message -------------------------------------------------------------------------
@pytest.mark.mongo
def test_button_approve_covers_only_the_dates_in_the_text(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-25", "closed", day_key="XMAS_DAY")],
                     dates_shown=["2026-12-24"])
    r = decide_flow.handle_reply(card_id=card.id, button_id="APPROVE", channel="WHATSAPP")
    assert r["handled"] and r["result"]["applied"] == {"2026-12-24": "07:30-13:00"}
    assert "2026-12-25" not in live(flow)
    fu = season.load_card(r["result"]["followup_card_id"])           # the cut date gets a short follow-up card
    assert fu and [i["date"] for i in fu.items] == ["2026-12-25"] and fu.status == "SENT" and fu.question is None


@pytest.mark.mongo
def test_sms_digit_and_free_text_edit_with_readback(flow):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-25", "closed", day_key="XMAS_DAY")])
    r = decide_flow.handle_reply(EID, text="24.12. 10-14, muut ok", channel="SMS")
    assert r["handled"] and r["result"]["pending"]["per_date"] == {"2026-12-24": "EDIT:10:00-14:00", "2026-12-25": "APPROVE"}
    assert live(flow) == {}                                          # read back first, nothing saved yet
    r = decide_flow.handle_reply(EID, text="1", channel="SMS")      # "1 = yes" on the read-back
    assert r["handled"] and r["result"]["applied"] == {"2026-12-24": "10:00-14:00", "2026-12-25": "closed"}
    inbound = list(flow["db"].outbox.find({"card_id": card.id, "direction": "in"}))
    assert len(inbound) == 2


@pytest.mark.mongo
def test_unclear_reply_sends_the_link_and_changes_nothing(flow):
    make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    r = decide_flow.handle_reply(EID, text="hmm what is this about", channel="SMS")
    assert not r["handled"] and "link" in r["reason"] and live(flow) == {}


@pytest.mark.mongo
def test_reply_without_card(flow):
    assert decide_flow.handle_reply("FI-D0017", text="1", channel="SMS")["handled"] is False
