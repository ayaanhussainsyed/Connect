"""Learning in the live app: what the owner told us changes the next card, and one card per season."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from cs import db as dbmod, decide_flow, learn, season
from cs.domain import Card, Suggestion

EID = "FI-D0001"


def first_card(**kw) -> Card:
    return season.build_card(EID, 2026, "baseline", target="mock", ask_question=False, **kw)


def row(d: str, value: str | None, *, basis: str = "PEERS", shown: str = "07:30-18:00", day_key: str = "BOXING_DAY",
        action: str = "SUGGEST") -> Suggestion:
    return Suggestion(entity_id=EID, kind="HOLIDAY_HOURS", date=d, day_key=day_key, shown=shown, action=action,
                      value=value, basis=basis, message_ok=action == "SUGGEST", season=2026)


def decline_card(db, day_key: str, d: str, value: str) -> Card:
    item = {"suggestion_id": f"sug:test:{d}", "date": d, "day_key": day_key, "shown": "07:30-18:00",
            "shown_source": "REGULAR_HOURS", "regular": "07:30-18:00", "value": value, "basis": "PEERS",
            "probability": None, "action": "SUGGEST", "evidence": [], "flip": False, "flip_tick_required": False,
            "deadline": d}
    card = Card(entity_id=EID, season=2026, suggestion_ids=[item["suggestion_id"]], items=[item], status="SENT",
                target="mock", owner_id=f"owner:{EID}", lang="fi", channel="SMS", expected_current={d: None},
                approve_by="2026-12-17")
    dbmod.save(card, db=db)
    decide_flow.handle_decision(card.id, {"per_date": {d: "DECLINE"}}, db=db)
    return card


def knowledge(db, rows):
    return season._owner_knowledge(rows, EID, 2026, date(2026, 11, 1), date(2026, 11, 7), db)


# --- an edit becomes the next card's value ------------------------------------------------------
@pytest.mark.mongo
def test_edit_is_used_on_the_next_card(flow):
    card = first_card()
    season.send_card(card, base_url="http://test")
    it = card.items[0]
    d, new = it["date"], "10:00-14:00" if it["value"] != "10:00-14:00" else "11:00-15:00"
    res = decide_flow.handle_decision(card.id, {"per_date": {d: {"action": "EDIT", "hours": new}}})
    assert res["applied"] == {d: new} and res["facts"]

    again = first_card()   # the edit is live now: nothing to ask about that date
    assert d not in {i["date"] for i in again.items}

    with flow["client"]() as c:   # the listing loses it (demo reset): the owner's own value comes back
        c.clear_holiday_hours(EID, decision_id="test-reset")
    later = first_card()
    i = next(x for x in later.items if x["date"] == d)
    assert i["value"] == new and i["basis"] == "OWNER_ANSWER" and not i["flip_tick_required"]
    assert i["evidence"][0]["source"] == "OWNER_ANSWER" and i["evidence"][0]["ref"].startswith("learned_fact:")
    from cs import audit
    built = audit.events(entity_id=EID, event_type="CARD_BUILT", db=flow["db"])[0]["payload"]
    assert any(x["date"] == d and x["effect"] == "as you told us" for x in built["learned"])


# --- a decline is never suggested again ---------------------------------------------------------
@pytest.mark.mongo
def test_declined_dates_are_not_asked_again(flow):
    card = first_card()
    season.send_card(card, base_url="http://test")
    dates = [i["date"] for i in card.items]
    decide_flow.handle_decision(card.id, {"per_date": {d: "DECLINE" for d in dates}})
    with pytest.raises(season.NoCard) as e:
        first_card()
    assert "already said no" in str(e.value) and not isinstance(e.value, season.CardWaiting)


@pytest.mark.mongo
def test_decline_blocks_the_value_and_peer_guesses(flow):
    db = flow["db"]
    decline_card(db, "BOXING_DAY", "2026-12-26", "12:00-16:00")
    rows, learned, _ = knowledge(db, [
        row("2026-12-26", "12:00-16:00", basis="OWN_LAST_YEAR"),              # the declined value: never
        row("2026-12-26", "10:00-14:00", basis="PEERS"),                      # a peer guess: not after a decline
        row("2026-12-26", "10:00-14:00", basis="OWN_LAST_YEAR"),              # the owner's own history: still ok
        row("2026-12-24", "07:30-13:00", basis="PEERS", day_key="XMAS_EVE"),  # another holiday: untouched
    ])
    assert [r.action for r in rows] == ["NO_ACTION", "NO_ACTION", "SUGGEST", "SUGGEST"]
    assert not rows[0].message_ok and rows[0].guardrail_notes[-1].startswith("owner_declined")
    assert [x["effect"] for x in learned] == ["declined before", "declined before"]


@pytest.mark.mongo
def test_a_fact_told_after_the_decline_wins(flow):
    db = flow["db"]
    decline_card(db, "BOXING_DAY", "2026-12-26", "12:00-16:00")
    learn.record_fact(EID, "BOXING_DAY", "2026-12-26", "12:00-16:00", 2026, db=db)
    rows, learned, known = knowledge(db, [row("2026-12-26", "10:00-14:00")])
    assert rows[0].action == "SUGGEST" and rows[0].value == "12:00-16:00" and rows[0].basis == "OWNER_ANSWER"
    assert known == {"2026-12-26": "special"} and learned[0]["effect"] == "as you told us"


# --- learned facts: decay, regular hours, special -----------------------------------------------
@pytest.mark.mongo
def test_learned_facts_decay_by_season(flow):
    db = flow["db"]
    learn.record_fact(EID, "BOXING_DAY", "2025-12-26", "11:00-15:00", 2025, db=db)
    learn.record_fact(EID, "XMAS_EVE", "2022-12-24", "08:00-12:00", 2022, db=db)
    rows, _, _ = knowledge(db, [row("2026-12-26", "10:00-14:00"),
                                row("2026-12-24", "07:30-13:00", day_key="XMAS_EVE")])
    assert rows[0].value == "11:00-15:00" and rows[0].probability == 0.95
    assert rows[1].value == "07:30-13:00" and rows[1].basis == "PEERS"   # 4 seasons old: ignored


@pytest.mark.mongo
def test_regular_and_special_answers(flow):
    db = flow["db"]
    learn.record_fact(EID, "BOXING_DAY", "2026-12-26", "REGULAR", 2026, source="OWNER_QUESTION", db=db)
    learn.record_fact(EID, "XMAS_EVE", "2026-12-24", "SPECIAL", 2026, source="OWNER_QUESTION", db=db)
    from cs import data, display
    reg = display.explain_shown(EID, "2026-12-26", data.SNAPSHOT[2026])["regular"]["hours"]
    rows, learned, known = knowledge(db, [row("2026-12-26", "closed", shown=reg),
                                          row("2026-12-24", "07:30-13:00", day_key="XMAS_EVE")])
    assert rows[0].action == "NO_ACTION" and learned[0]["effect"] == "already shown"   # Google shows it already
    assert known == {"2026-12-26": "regular"}
    assert rows[1].value == "07:30-13:00"   # "other hours" without hours: the engine's value stays


# --- the one question ----------------------------------------------------------------------------
@pytest.mark.mongo
def test_question_skips_dates_already_on_the_card(flow, monkeypatch):
    import cs.engine.api as api
    on_card = [i["date"] for i in first_card(save=False).items]
    from cs import export
    other = next(s.date for s in export.build(2026, "baseline", entity_ids=[EID]) if s.date not in on_card)
    seen = {}

    def qv(eid, season_, known):
        seen["known"] = known
        return [(on_card[0], 0.9), (other, 0.5)]
    monkeypatch.setattr(api, "question_value", qv)
    card = season.build_card(EID, 2026, "baseline", target="mock")
    assert card.question and card.question["date"] == other and seen["known"] == {}


# --- one open card per business and season ------------------------------------------------------
@pytest.mark.mongo
def test_one_waiting_card_per_season(flow):
    card = first_card()
    season.send_card(card, base_url="http://test")
    with pytest.raises(season.CardWaiting) as e:
        first_card()
    assert e.value.card.id == card.id and "waiting" in str(e.value)

    draft = first_card(save=False)          # a draft can be built, but not sent while one waits
    with pytest.raises(season.CardWaiting):
        season.send_card(draft, base_url="http://test")
    assert flow["db"].outbox.count_documents({"entity_id": EID, "kind": "CARD"}) == 1

    assert season.close_waiting(EID, 2026, "demo reset") == [card.id]
    assert season.load_card(card.id).status == "EXPIRED"
    season.send_card(first_card(), base_url="http://test")


@pytest.mark.mongo
def test_a_decided_card_does_not_block_the_next(flow):
    card = first_card()
    season.send_card(card, base_url="http://test")
    d = card.items[0]["date"]
    decide_flow.handle_decision(card.id, {"per_date": {d: "DECLINE"}})
    assert season.waiting_card(EID, 2026) is None
    assert d not in {i["date"] for i in first_card().items}


def test_card_waiting_is_a_no_card():
    c = Card(entity_id=EID, season=2026, status="SENT", approve_by="2026-12-17",
             history=[{"from": "DRAFT", "to": "SENT", "at": (date(2026, 11, 7) - timedelta(days=1)).isoformat()}])
    e = season.CardWaiting(c)
    assert isinstance(e, season.NoCard) and "since 2026-11-06" in str(e) and "2026-12-17" in str(e)
