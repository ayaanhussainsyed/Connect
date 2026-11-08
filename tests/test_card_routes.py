"""Owner card routes (GET never decides, single-use POSTs), webhooks, demo/ops pages, and scripts/demo.py."""
from __future__ import annotations

import pytest

from cs import db as dbmod, links, season
from cs.domain import Decision
from tests.test_season import EID, item, live, make_card


@pytest.fixture
def client(flow):
    from app.app import app
    app.config.update(TESTING=True)
    return app.test_client()


def _token(card) -> str:
    return links.sign(card.id, card.owner_id)


@pytest.mark.mongo
def test_get_never_decides(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    t = _token(card)
    for _ in range(3):   # link scanners / previews may open it many times
        r = client.get(f"/c/{t}")
        assert r.status_code == 200 and b"Approve these hours" in r.data and b"No thanks" in r.data
        assert b'name="every_year"' in r.data          # "every year" is one checkbox, not a second button
    r = client.get(f"/c/{t}/preview?answer=closed")
    assert r.status_code == 200
    assert dbmod.find(Decision, {"card_id": card.id}) == []
    assert season.load_card(card.id).status == "SENT" and live(flow) == {}
    assert flow["db"].link_nonces.count_documents({}) == 0


@pytest.mark.mongo
def test_bad_links(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    t = _token(card)
    assert client.get(f"/c/{t[:-3]}xyz").status_code == 400
    assert client.post(f"/c/{t[:-3]}xyz/decide", data={"action": "approve"}).status_code == 400
    from datetime import datetime, timedelta, timezone
    old = links.sign(card.id, card.owner_id, now=datetime.now(timezone.utc) - timedelta(days=40))
    assert client.get(f"/c/{old}").status_code == 410
    other = links.sign(card.id, "owner:SOMEONE-ELSE")
    assert client.get(f"/c/{other}").status_code == 400


@pytest.mark.mongo
def test_decide_then_replay_then_undo(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-25", "closed", day_key="XMAS_DAY")])
    t = _token(card)
    r = client.post(f"/c/{t}/decide", data={"action": "approve_every_year", "hours_2026-12-24": "07:30-13:00",
                                             "hours_2026-12-25": "closed"})
    assert r.status_code == 200 and "Saved" in r.get_data(as_text=True) and "Where it shows" in r.get_data(as_text=True)
    assert live(flow) == {"2026-12-24": "07:30-13:00", "2026-12-25": "closed"}
    assert client.post(f"/c/{t}/decide", data={"action": "decline"}).status_code == 409    # replay rejected
    assert "Saved" in client.get(f"/c/{t}").get_data(as_text=True)                      # GET shows the result
    r = client.post(f"/c/{t}/undo")
    assert r.status_code == 200 and "Undone" in r.get_data(as_text=True)
    assert season.load_card(card.id).status == "UNDONE"
    assert client.post(f"/c/{t}/undo").status_code == 409


@pytest.mark.mongo
def test_form_edit_skip_and_missing_tick(flow, client):
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00"), item("2026-12-31", "10:00-16:00", day_key="NYE"),
                                  item("2026-12-26", "closed", day_key="BOXING_DAY", basis="PEERS", tick=True)])
    t = _token(card)
    form = {"action": "approve", "hours_2026-12-24": "8-13", "skip_2026-12-31": "1", "hours_2026-12-26": "closed"}
    r = client.post(f"/c/{t}/decide", data=form)
    assert r.status_code == 200
    assert live(flow) == {"2026-12-24": "08:00-13:00"}        # edited; skipped; flip without tick not applied
    decs = {d.date: d for d in dbmod.find(Decision, {"card_id": card.id})}
    assert decs["2026-12-24"].decision == "EDIT" and decs["2026-12-31"].decision == "DECLINE"
    assert "2026-12-26" not in decs


@pytest.mark.mongo
def test_only_a_missing_tick_keeps_the_card_open(flow, client):
    card = make_card(flow["db"], [item("2026-12-26", "closed", day_key="BOXING_DAY", basis="PEERS", tick=True)])
    t = _token(card)
    r = client.post(f"/c/{t}/decide", data={"action": "approve", "hours_2026-12-26": "closed"})
    assert r.status_code == 422 and "Tick the box" in r.get_data(as_text=True)
    r = client.post(f"/c/{t}/decide", data={"action": "approve", "hours_2026-12-26": "closed", "tick_2026-12-26": "1"})
    assert r.status_code == 200 and live(flow) == {"2026-12-26": "closed"}


@pytest.mark.mongo
def test_closed_today_from_the_card_page(flow, client):
    from datetime import date
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    t = _token(card)
    r = client.post(f"/c/{t}/closed-today")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Saved: closed" in html and "/undo" in html
    assert live(flow) == {date.today().isoformat(): "closed"}
    assert season.load_card(card.id).status == "SENT"                       # the card itself is untouched
    assert "already been used" in client.post(f"/c/{t}/closed-today").get_data(as_text=True)
    undo = html.split('hx-post="')[1].split('"')[0]
    assert "usual hours are back" in client.post(undo).get_data(as_text=True)
    assert live(flow) == {}


@pytest.mark.mongo
def test_webhooks(flow, client, monkeypatch):
    import hashlib
    import hmac
    import json
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-secret")       # Meta signs every webhook with the app secret
    card = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    assert client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=x").status_code == 403
    payload = {"entry": [{"changes": [{"value": {"messages": [{
        "from": "358000", "id": "wamid.1", "timestamp": "1", "type": "interactive",
        "interactive": {"button_reply": {"id": f"APPROVE|{card.id}", "title": "Hyväksy"}}}]}}]}]}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    hdr = {"Content-Type": "application/json"}
    assert client.post("/webhooks/whatsapp", data=body, headers=hdr).status_code == 403        # unsigned
    assert client.post("/webhooks/whatsapp", data=body,
                       headers={**hdr, "X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 403  # forged
    r = client.post("/webhooks/whatsapp", data=body, headers={**hdr, "X-Hub-Signature-256": sig})
    assert r.status_code == 200 and r.get_json()["results"][0]["handled"] is True
    assert live(flow) == {"2026-12-24": "07:30-13:00"}
    card2 = make_card(flow["db"], [item("2026-12-31", "10:00-16:00", day_key="NYE")])
    r = client.post("/webhooks/sms", json={"card_id": card2.id, "text": "ei kiitos"})
    assert r.get_json()["handled"] is True and r.get_json()["action"] == "DECLINE"
    assert client.post("/webhooks/sms", json={"entity_id": EID}).status_code == 400


@pytest.mark.mongo
def test_demo_and_ops_pages(flow, client, monkeypatch):
    import cs.engine.api as api
    monkeypatch.setattr(api, "question_value", lambda *a, **k: [])
    old = client.get(f"/demo?entity_id={EID}&engine=baseline&target=mock")          # the old demo URL
    assert old.status_code == 302 and old.headers["Location"].endswith(f"/b/{EID}")
    assert client.get(old.headers["Location"]).status_code == 200
    r = client.post("/demo/send", data={"entity_id": EID, "engine": "baseline", "target": "mock"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Open the card" in html
    link = html.split('href="')[1].split('"')[0]
    assert link.startswith("/c/") and client.get(link).status_code == 200
    assert "Kahvila Aurora" in client.get(f"/demo/entity?entity_id={EID}&target=mock").get_data(as_text=True)
    steps = client.get(f"/demo/steps?entity_id={EID}").get_data(as_text=True)
    assert "Detect" in steps and "Contact" in steps
    r = client.post("/demo/reply", data={"entity_id": EID, "text": "1"})
    assert r.status_code == 200 and live(flow)
    r = client.post("/demo/emergency", data={"entity_id": EID, "target": "mock"})
    assert r.status_code == 200 and "Not applied" not in r.get_data(as_text=True)
    from datetime import date
    assert live(flow)[date.today().isoformat()] == "closed"
    steps = client.get(f"/demo/steps?entity_id={EID}").get_data(as_text=True)
    assert "owner: closed" in steps
    r = client.post("/demo/reply", data={"entity_id": EID, "button_id": "UNDO",
                                         "card_id": "em:" + flow["db"].emergencies.find_one({})["id"]})
    assert r.status_code == 200 and date.today().isoformat() not in live(flow)
    r = client.post("/demo/autopilot", data={"entity_id": "FI-D0012", "engine": "ml", "target": "mock"})
    import re
    m = re.search(r"Autopilot: applied (\d+) date\(s\)", r.get_data(as_text=True))
    assert m and int(m.group(1)) >= 1
    from cs.domain import Permission
    assert {p.source for p in dbmod.find(Permission, {"entity_id": "FI-D0012"})} == {"CRM_2025"}   # no time machine
    nc = client.post("/demo/send", data={"entity_id": "FI-D0013", "engine": "baseline", "target": "mock"})
    assert "stay quiet" in nc.get_data(as_text=True)
    ops = client.get("/ops").get_data(as_text=True)
    assert "Cards" in ops and EID in ops
    assert "chain verified" in client.get("/ops/verify").get_data(as_text=True)


@pytest.mark.mongo
def test_demo_script_end_to_end_on_the_mock(flow, capsys, monkeypatch):
    import importlib.util

    from cs.config import settings
    spec = importlib.util.spec_from_file_location("demo_script", settings.root / "scripts" / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    rc = demo.main([EID, "--target", "mock", "--mock-url", flow["mock_url"], "--engine", "baseline", "--no-question"])
    out = capsys.readouterr().out
    assert rc == 0, out
    for step in ("1 DETECT", "2 SUGGEST", "3 CONTACT", "4 DECIDE", "5 APPLY + CONFIRM"):
        assert step in out
    assert "read-back OK" in out and "where it shows" in out and "cleanup: mock reset" in out
    assert live(flow) == {}                       # the mock was reset at the end
    from cs import audit
    assert audit.verify_chain().ok
