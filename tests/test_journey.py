"""The owner's experience: join, the welcome on your own channel, and a season that unfolds by itself."""
from __future__ import annotations

import re

import pytest

from cs import compose_bridge


@pytest.fixture
def client(flow, monkeypatch):
    import cs.engine.api as api
    from app.app import app
    monkeypatch.setattr(api, "question_value", lambda *a, **k: [])
    for c in ("journeys", "profile_offers", "review_replies", "demo_overrides", "onboarding"):
        flow["db"][c].delete_many({})
    app.config.update(TESTING=True)
    yield app.test_client()
    compose_bridge.DEMO_OVERRIDES = False
    compose_bridge._cache.clear()
    flow["db"]["demo_overrides"].delete_many({})


def title(html: str) -> str:
    return " ".join(re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1).split())


@pytest.mark.mongo
def test_landing_and_join_pages(flow, client):
    assert "Start as a business owner" in client.get("/").get_data(as_text=True)
    page = client.get("/start?b=FI-D0017").get_data(as_text=True)
    assert "Kahvila Kipinä, Turku · FI-D0017" in page and page.count("btn--primary") == 1
    assert client.get("/me").headers["Location"].endswith("/start")              # not joined yet
    assert "How Connect decides" in client.get("/how").get_data(as_text=True)
    bad = client.post("/start", data={"business": "No Such Café Anywhere"})
    assert bad.status_code == 400 and "couldn&#39;t find" in bad.get_data(as_text=True)


@pytest.mark.mongo
def test_a_season_as_an_owner(flow, client):
    r = client.post("/start", data={"business": "Kahvila Kipinä, Turku · FI-D0017", "channel": "SMS", "lang": "en"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/me")
    me = client.get("/me").get_data(as_text=True)
    assert title(me) == "Welcome to Connect" and "Next: Tuesday 1 December 2026" in me
    assert "What Connect did today" in me and "our addition" in me                 # the steps, named
    welcome = flow["db"].outbox.find_one({"entity_id": "FI-D0017"})
    assert welcome and "Welcome to Connect" in welcome["text"]                   # onboarding on the owner's channel

    client.post("/me/continue")                                                  # 1 December: Connect acts by itself
    me = client.get("/me").get_data(as_text=True)
    assert "Why Connect thinks so" in me and "% sure" in me
    card = flow["db"].outbox.find_one({"entity_id": "FI-D0017", "kind": "CARD"})
    assert card and "Hi Kahvila Kipinä" in card["text"]                          # English, as chosen
    client.post("/me/say", data={"text": "Why do you suggest these hours?"})     # Ask Connect from the phone
    assert flow["db"].outbox.count_documents({"entity_id": "FI-D0017", "direction": {"$ne": "in"}}) >= 3

    mem = client.get("/me/memory")                                               # the knowledge graph view
    assert mem.status_code == 200 and "What Connect knows about" in mem.get_data(as_text=True)
    client.post("/me/continue")                                                  # life happens
    assert title(client.get("/me").get_data(as_text=True)) == "Life happens"
    back = client.get("/me?day=season").get_data(as_text=True)                  # look back at 1 December
    assert "looking back at Tuesday 1 December 2026" in back and "Why Connect thinks so" in back
    assert 'action="/me/continue"' not in back
    client.post("/me/continue")                                                  # next season
    client.post("/me/continue")                                                  # the year in numbers
    me = client.get("/me").get_data(as_text=True)
    assert title(me) == "Your year with Connect" and "messages Connect started" in me and 'action="/me/continue"' not in me
    assert client.get("/me/listing").status_code == 200

    r = client.post("/me/restart")
    assert r.headers["Location"].endswith("/start?b=FI-D0017") and flow["db"].journeys.count_documents({}) == 0
