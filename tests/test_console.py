"""The console's one button, and the features wired into the owner's conversation and card page:
weekly hours that changed (A13), replies to "it was closed" reviews (A13), Ask Connect (B10),
and "every year" as a checkbox on the card."""
from __future__ import annotations

import re

import pytest

from cs import db as dbmod, links, season
from cs.domain import Permission
from tests.test_season import EID, item, live, make_card


@pytest.fixture
def client(flow, monkeypatch):
    import cs.engine.api as api
    from app.app import app
    monkeypatch.setattr(api, "question_value", lambda *a, **k: [])   # fast, and no question on these cards
    for c in ("profile_offers", "review_replies"):
        flow["db"][c].delete_many({})
    app.config.update(TESTING=True)
    return app.test_client()


def check(client, eid: str, engine: str = "baseline") -> str:
    r = client.post("/demo/check", data={"entity_id": eid, "engine": engine, "target": "mock"})
    assert r.status_code == 200 and r.headers.get("HX-Trigger") == "connect-changed"
    return r.get_data(as_text=True)


@pytest.mark.mongo
def test_console_page_has_one_primary_button(flow, client):
    html = client.get(f"/?b={EID}").get_data(as_text=True)
    assert html.count("btn--primary") == 1 and "Check Kahvila Aurora" in html
    assert 'hx-post="/demo/check"' in html and "Kahvila Kipinä" in html          # the picker lists the demo businesses
    assert client.get("/demo").status_code == 200


@pytest.mark.mongo
def test_check_sends_a_card_and_the_owner_decides_on_the_phone(flow, client):
    html = check(client, EID)
    assert "sent the owner one message" in html
    link = re.search(r'href="(/c/[^"]+)"', html).group(1)
    assert client.get(link).status_code == 200
    phone = client.get(f"/demo/phone?entity_id={EID}").get_data(as_text=True)
    assert "Open the card" in phone and "1 = " in phone                # Aurora prefers SMS: digits, not buttons
    client.post("/demo/reply", data={"entity_id": EID, "text": "1"})      # SMS digit 1 = Approve
    assert live(flow)


@pytest.mark.mongo
def test_one_card_per_season_then_reset(flow, client):
    first = check(client, EID)
    assert "sent the owner one message" in first
    second = check(client, EID)                                   # the owner hasn't answered: never ask twice
    assert "Waiting for the owner" in second and 'href="/c/' in second
    assert flow["db"].cards.count_documents({"entity_id": EID}) == 1
    client.post("/demo/clear", data={"entity_id": EID, "target": "mock"})   # demo reset expires the waiting card
    assert "sent the owner one message" in check(client, EID)


@pytest.mark.mongo
@pytest.mark.parametrize("eid, words", [("FI-D0013", "stays quiet"), ("FI-D0011", "Flagged for a Fonecta person")])
def test_check_stays_quiet_or_flags(flow, client, eid, words):
    html = check(client, eid)
    assert words in html
    assert flow["db"].outbox.count_documents({"entity_id": eid}) == 0        # no message to the owner


@pytest.mark.mongo
def test_check_runs_autopilot_and_offers_the_review_reply(flow, client):
    html = check(client, "FI-D0012", engine="ml")           # Autopilot needs the ML engine to agree (P >= 0.5)
    assert "automatically" in html and "standing permission" in html
    assert {p.source for p in dbmod.find(Permission, {"entity_id": "FI-D0012"})} == {"CRM_2025"}
    offer = flow["db"].profile_offers.find_one({"entity_id": "FI-D0012", "kind": "REVIEW_REPLY"})
    assert offer and offer["status"] == "OPEN"
    r = client.post("/demo/reply", data={"entity_id": "FI-D0012", "button_id": "APPROVE",
                                         "card_id": f"rr:{offer['id']}"})
    assert r.status_code == 200 and "Not applied" not in r.get_data(as_text=True)
    assert flow["db"].review_replies.count_documents({"entity_id": "FI-D0012"}) == 1
    assert flow["db"].profile_offers.find_one({"id": offer["id"]})["status"] == "APPLIED"
    check(client, "FI-D0012", engine="ml")                                      # a second check sends no new offer
    assert flow["db"].profile_offers.count_documents({"entity_id": "FI-D0012", "kind": "REVIEW_REPLY"}) == 1


@pytest.mark.mongo
def test_weekly_hours_change_is_offered_and_applied(flow, client):
    eid = "SE-D0016"
    html = check(client, eid)
    assert "weekly hours" in html.lower()
    offer = flow["db"].profile_offers.find_one({"entity_id": eid, "kind": "DRIFT"})
    assert offer and offer["status"] == "OPEN"
    phone = client.get(f"/demo/phone?entity_id={eid}").get_data(as_text=True)
    assert f"drift:{offer['id']}" in phone                                       # the offer's buttons are on the phone
    r = client.post("/demo/reply", data={"entity_id": eid, "button_id": "APPROVE", "card_id": f"drift:{offer['id']}"})
    assert r.status_code == 200
    done = flow["db"].profile_offers.find_one({"id": offer["id"]})
    assert done["status"] in ("APPLIED", "CANCELLED")                             # cancelled = owner-wins re-read
    from cs.hours import DAYS, week_from_yext
    from cs.profile import drift
    if done["status"] == "APPLIED":
        with flow["client"]() as c:
            week = dict(zip(DAYS, week_from_yext(c.get_entity(eid).get("hours") or {})))
        assert week == drift.week_of(drift.drift_suggestion(eid).value)


@pytest.mark.mongo
def test_decline_and_edit_on_an_offer(flow, client):
    from cs import profile_flow
    eid = "SE-D0016"
    res = profile_flow.offer_drift(eid)
    assert profile_flow.handle_reply(eid, f"drift:{res['offer_id']}", text="nej")["action"] == "DECLINE"
    assert profile_flow.offer_drift(eid) is None                                 # a "no" means no: never asked again
    profile_flow.reset_offers(eid)                                               # the demo reset forgets the answer
    res = profile_flow.offer_drift(eid)
    assert res and res["sent"]
    assert profile_flow.handle_reply(eid, f"drift:{res['offer_id']}", button_id="EDIT")["action"] == "EDIT"
    # The owner's own words arrive without a card id: they go to the offer that asked for them.
    out = client.post("/demo/reply", data={"entity_id": eid, "text": "We now open at 12 on weekdays"})
    assert out.status_code == 200
    assert flow["db"].profile_offers.find_one({"id": res["offer_id"]})["status"] == "EDIT_RECEIVED"


@pytest.mark.mongo
def test_owner_question_is_answered_from_the_listing(flow, client):
    from cs import profile_flow
    assert profile_flow.is_question("Why do you suggest closing on the 26th?")
    assert profile_flow.is_question("Miksi ehdotatte kiinni 26.12.?")
    assert not profile_flow.is_question("ok?") and not profile_flow.is_question("kiinni 26.12.")
    r = client.post("/demo/reply", data={"entity_id": EID, "text": "What hours do we have on Christmas Eve?"})
    assert r.status_code == 200 and "Not applied" not in r.get_data(as_text=True)
    last = flow["db"].outbox.find_one({"entity_id": EID, "direction": {"$ne": "IN"}}, sort=[("at", -1)])
    assert last and last.get("text")


@pytest.mark.mongo
def test_card_page_every_year_checkbox_and_ask(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    t = links.sign(card.id, card.owner_id)
    r = client.post(f"/c/{t}/ask", data={"q": "Why 13:00 on Christmas Eve?"})
    assert r.status_code == 200 and "role=\"alert\"" not in r.get_data(as_text=True)
    r = client.post(f"/c/{t}/decide", data={"action": "approve", "every_year": "1", "hours_2026-12-24": "07:30-13:00"})
    assert r.status_code == 200 and "Saved" in r.get_data(as_text=True) and "Every year" in r.get_data(as_text=True)
    assert live(flow) == {"2026-12-24": "07:30-13:00"}
    assert dbmod.find(Permission, {"entity_id": EID, "status": "GRANTED"})
    assert season.load_card(card.id).status in ("APPLIED", "CONFIRMED")
