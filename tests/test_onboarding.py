"""Onboarding on the owner's own channel: the welcome asks two questions, and the answers shape the first card."""
from __future__ import annotations

import pytest

from cs import decide_flow, learn, onboarding, season

EID = "FI-D0017"        # Kahvila Kipinä: a new customer with no history (SMS, Finnish)


@pytest.fixture
def ob(flow, monkeypatch):
    import cs.engine.api as api
    monkeypatch.setattr(api, "question_value", lambda *a, **k: [])
    for c in ("onboarding", "learned_facts", "permissions", "outbox", "reply_context"):
        flow["db"][c].delete_many({})
    return flow


def last_out(db) -> dict:
    return db.outbox.find_one({"entity_id": EID, "direction": {"$ne": "in"}}, sort=[("at", -1)])


@pytest.mark.mongo
def test_welcome_is_one_message_on_the_owners_channel(ob):
    res = onboarding.send_welcome(EID)
    assert res["sent"] and onboarding.send_welcome(EID)["sent"] is False          # never twice
    m = last_out(ob["db"])
    assert m["kind"] == "WELCOME" and "Tervetuloa Connectiin" in m["text"] and "1 = Kiinni" in m["text"]   # SMS digits
    assert [b["id"] for b in m["buttons"]] == ["OB_CLOSED", "OB_SHORT", "OB_NORMAL"]
    assert onboarding.awaiting(EID) == f"ob:{res['onboarding_id']}"


@pytest.mark.mongo
def test_closed_and_every_year_become_facts_and_a_permission(ob):
    res = onboarding.send_welcome(EID)
    cid = f"ob:{res['onboarding_id']}"
    r = decide_flow.handle_reply(EID, text="1", channel="SMS")                  # SMS digit 1 = Kiinni (closed)
    assert r["handled"] and r["action"] == "OB_CLOSED" and r["facts"] >= 3
    facts = learn.active_facts(EID, ob["db"])
    assert facts and {f.value for f in facts} == {"closed"} and {f.source for f in facts} == {"ONBOARDING"}
    r = decide_flow.handle_reply(EID, card_id=cid, button_id="OB_EVERY_YEAR", channel="WHATSAPP")
    assert r["handled"] and onboarding.prefers_every_year(EID) and onboarding.awaiting(EID) is None
    assert "Valmista" in last_out(ob["db"])["text"]

    # The first season card already says "as you told us" instead of guessing from similar cafés.
    card = season.build_card(EID, 2026, "ml", target="mock")
    told = [i for i in card.items if i["basis"] == "OWNER_ANSWER"]
    assert told and all(i["value"] == "closed" for i in told)


@pytest.mark.mongo
def test_shorter_hours_then_ask_each_time(ob):
    onboarding.send_welcome(EID)
    assert decide_flow.handle_reply(EID, text="lyhyemmät ajat", channel="SMS")["action"] == "OB_SHORT"
    assert decide_flow.handle_reply(EID, text="yleensä 10-14", channel="SMS")["hours"] == "10:00-14:00"
    assert {f.value for f in learn.active_facts(EID, ob["db"])} == {"10:00-14:00"}
    assert decide_flow.handle_reply(EID, text="ei", channel="SMS")["action"] == "OB_ASK_EACH"
    assert onboarding.prefers_every_year(EID) is False


@pytest.mark.mongo
def test_unclear_answer_and_stop(ob):
    res = onboarding.send_welcome(EID)
    assert decide_flow.handle_reply(EID, text="hmm maybe", channel="SMS")["action"] == "UNCLEAR"
    assert "Anteeksi" in last_out(ob["db"])["text"]
    assert onboarding.handle_reply(EID, f"ob:{res['onboarding_id']}", text="STOP")["action"] == "STOP"
    assert onboarding.awaiting(EID) is None
