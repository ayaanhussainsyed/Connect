"""Owner app: GETs never create or decide anything; POSTs check, decide, undo, and report closed today."""
from __future__ import annotations

from datetime import date

import pytest

from cs import db as dbmod
from cs.domain import Card, Decision
from tests.test_season import EID, item, live, make_card


@pytest.fixture
def client(flow):
    from app.app import app
    app.config.update(TESTING=True)
    return app.test_client()


def _decide(client, card, **form):
    return client.post("/app/decide", data={"card_id": card.id, "b": EID, **form})


@pytest.mark.mongo
def test_gets_change_nothing(flow, client):
    landing = client.get("/").get_data(as_text=True)
    assert "Kahvila Aurora" in landing and "/app?b=FI-D0001" in landing
    r = client.get(f"/app?b={EID}")
    assert r.status_code == 200 and "Kahvila Aurora" in r.get_data(as_text=True)
    for path in (f"/app/needs?b={EID}", f"/app/listing?b={EID}", f"/app/upcoming?b={EID}"):
        assert client.get(path).status_code == 200
    assert "Check now" in client.get(f"/app/needs?b={EID}").get_data(as_text=True)
    assert dbmod.find(Card, {"entity_id": EID}) == [] and dbmod.find(Decision, {}) == []
    assert live(flow) == {}


@pytest.mark.mongo
def test_approve_then_undo(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    html = client.get(f"/app/needs?b={EID}").get_data(as_text=True)
    assert "your OK" in html and card.id in html
    r = _decide(client, card, action="approve", **{"hours_2026-12-24": "07:30-13:00"})
    assert r.status_code == 200 and "Updated everywhere" in r.get_data(as_text=True)
    assert r.headers.get("HX-Trigger") == "listing-changed"
    assert live(flow).get("2026-12-24") == "07:30-13:00"
    r = client.post("/app/undo", data={"card_id": card.id, "b": EID})
    assert "Undone" in r.get_data(as_text=True) and "2026-12-24" not in live(flow)


@pytest.mark.mongo
def test_flip_needs_tick_and_decline_writes_nothing(flow, client):
    card = make_card(flow["db"], [item("2026-12-25", "closed", day_key="XMAS_DAY", tick=True)])
    r = _decide(client, card, action="approve", **{"hours_2026-12-25": "closed"})
    assert r.status_code == 200 and "Tick the box" in r.get_data(as_text=True)
    r = _decide(client, card, action="decline")
    assert "No changes made" in r.get_data(as_text=True)
    assert "2026-12-25" not in live(flow)


@pytest.mark.mongo
def test_check_builds_a_card_or_says_why_not(flow, client):
    html = client.post("/app/needs", data={"b": EID}).get_data(as_text=True)
    assert "your OK" in html or "Nothing needs you" in html


@pytest.mark.mongo
def test_closed_today_and_undo(flow, client):
    today = date.today().isoformat()
    r = client.post("/app/closed-today", data={"b": EID})
    assert "Closed today" in r.get_data(as_text=True) and r.headers.get("HX-Trigger") == "listing-changed"
    assert live(flow).get(today) == "closed"
    em_id = flow["db"].emergencies.find_one({})["id"]
    r = client.post("/app/closed-today/undo", data={"b": EID, "em_id": em_id})
    assert "usual hours are back" in r.get_data(as_text=True) and today not in live(flow)
