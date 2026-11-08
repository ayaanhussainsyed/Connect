"""WhatsApp end to end against a fake Graph API (nothing leaves the machine): the send path through
the router (template outside the 24 h window, the full card inside it, fallbacks when Meta refuses)
and the webhook (handshake, delivery receipts, taps that quote a message, typed replies)."""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from cs.channels import base, outbox, router, whatsapp
from cs.channels.base import OutgoingMessage
from cs import compose

EID = "FI-D0003"          # a WhatsApp owner in the dataset
PHONE = "+358401234567"


class FakeMeta:
    """A Graph API double: records every request, answers with ``status`` / ``body``."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self.status, self.body, self.calls, self.n = status, body, [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"url": str(request.url), "json": json.loads(request.content),
                           "auth": request.headers.get("authorization", "")})
        self.n += 1
        return httpx.Response(self.status, json=self.body if self.body is not None
                              else {"messages": [{"id": f"wamid.{self.n}"}]})


def channel(meta: FakeMeta, db) -> whatsapp.WhatsAppChannel:
    class Live(whatsapp.WhatsAppChannel):
        def is_configured(self):
            return True
    return Live(db=db, client=httpx.Client(transport=httpx.MockTransport(meta)))


def card(card_id: str = "card-wa") -> OutgoingMessage:
    composed = compose.compose_card_message(
        {"name": "Kukkakauppa Kide"},
        [{"date": "2026-12-24", "value": "09:00-13:00", "shown": "09:00-18:00", "basis": "OWN_LAST_YEAR"}],
        "fi", "WHATSAPP", True, None, None, None)
    msg = OutgoingMessage.from_composed(EID, composed, kind="CARD", card_id=card_id)
    msg.to = PHONE
    return msg


@pytest.fixture
def wa_db(mongo_test_db):
    for c in (outbox.COLLECTION, "messages"):
        mongo_test_db[c].delete_many({})
    outbox._indexed.discard(mongo_test_db.name)
    yield mongo_test_db
    for c in (outbox.COLLECTION, "messages"):
        mongo_test_db[c].delete_many({})


# --- sending -------------------------------------------------------------------------------------
@pytest.mark.mongo
def test_first_message_is_the_template_then_the_full_card(wa_db):
    meta = FakeMeta()
    ch = channel(meta, wa_db)
    rec = router.send(card(), db=wa_db, whatsapp=ch, audit=False)
    assert rec.status == base.SENT and rec.delivered_via == "WHATSAPP" and rec.provider_message_id == "wamid.1"
    sent = meta.calls[0]
    assert sent["url"].endswith(f"/{whatsapp.API_VERSION}/{whatsapp.settings.whatsapp_phone_number_id}/messages")
    assert sent["auth"].startswith("Bearer ")
    tpl = sent["json"]["template"]
    assert sent["json"]["type"] == "template" and tpl["name"] == whatsapp.TEMPLATE_NAME and tpl["language"]["code"] == "fi"
    payloads = [c["parameters"][0]["payload"] for c in tpl["components"] if c["type"] == "button"]
    assert payloads and all(p.endswith("|card-wa") for p in payloads)        # a tap finds its card
    row = wa_db[outbox.COLLECTION].find_one({"provider_message_id": "wamid.1"})
    assert row and row["card_id"] == "card-wa" and row["to_masked"] == base.mask_address(PHONE)
    assert PHONE not in json.dumps(row, default=str)                            # stored masked only

    outbox.record_inbound(EID, "Hyväksy", channel="WHATSAPP", button_payload="APPROVE|card-wa", db=wa_db)
    router.send(card(), db=wa_db, whatsapp=ch, audit=False)
    inside = meta.calls[1]["json"]
    assert inside["type"] == "interactive" and "\n" in inside["interactive"]["body"]["text"]
    assert len(inside["interactive"]["action"]["buttons"]) <= whatsapp.MAX_BUTTONS


@pytest.mark.mongo
@pytest.mark.parametrize("meta, why", [
    (FakeMeta(400, {"error": {"message": "(#132001) Template name does not exist in the translation"}}), "132001"),
    (FakeMeta(401, {"error": {"message": "Error validating access token: Session has expired"}}), "expired"),
])
def test_meta_refusal_falls_back_to_the_on_screen_phone(wa_db, meta, why):
    rec = router.send(card(), db=wa_db, whatsapp=channel(meta, wa_db), audit=False)
    assert rec.status == base.STORED and rec.delivered_via == "OUTBOX"
    assert "WHATSAPP failed" in rec.detail and why in rec.detail
    assert len(outbox.thread(EID, db=wa_db)) == 1                               # the demo phone still shows it


@pytest.mark.mongo
def test_network_error_falls_back(wa_db):
    def down(request):
        raise httpx.ConnectError("no route to graph.facebook.com")
    class Live(whatsapp.WhatsAppChannel):
        def is_configured(self):
            return True
    ch = Live(db=wa_db, client=httpx.Client(transport=httpx.MockTransport(down)))
    rec = router.send(card(), db=wa_db, whatsapp=ch, audit=False)
    assert rec.status == base.STORED and "network error" in rec.detail


def test_tests_never_reach_the_real_api(monkeypatch):
    """conftest's ``no_real_messages`` keeps a .env with real keys from sending during tests."""
    assert not whatsapp.is_configured()
    assert not whatsapp.WhatsAppChannel().is_configured()


# --- who a typed reply is from ---------------------------------------------------------------------
@pytest.mark.mongo
def test_typed_reply_finds_the_business_last_messaged_on_whatsapp(wa_db):
    ch = channel(FakeMeta(), wa_db)
    assert whatsapp.entity_for_sender("358401234567", wa_db) is None
    router.send(card(), db=wa_db, whatsapp=ch, audit=False)
    later = card("card-2")
    later.entity_id = "FI-D0005"
    router.send(later, db=wa_db, whatsapp=ch, audit=False)
    assert whatsapp.entity_for_sender("358401234567", wa_db) == "FI-D0005"   # Meta sends digits, no "+"
    assert whatsapp.entity_for_sender("+358 40 123 4567", wa_db) == "FI-D0005"
    assert whatsapp.entity_for_sender("358409999999", wa_db) is None
    assert whatsapp.entity_for_sender(None, wa_db) is None

    failed = card("card-3")
    failed.entity_id = "FI-D0007"
    router.send(failed, db=wa_db, whatsapp=channel(FakeMeta(400, {"error": {"message": "x"}}), wa_db), audit=False)
    assert whatsapp.entity_for_sender("358401234567", wa_db) == "FI-D0005"   # only messages that went out


# --- webhook ---------------------------------------------------------------------------------------
def signed(body: dict, secret: str = "test-secret") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"Content-Type": "application/json", "X-Hub-Signature-256": sig}


@pytest.fixture
def app_client(flow, monkeypatch):
    from app.app import app
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.mark.mongo
def test_handshake_answers_the_challenge_only_with_the_token(app_client):
    ok = app_client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345")
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "12345"
    assert app_client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1").status_code == 403
    assert app_client.get("/webhooks/whatsapp?hub.mode=unsubscribe&hub.verify_token=verify-me&hub.challenge=1").status_code == 403


@pytest.mark.mongo
def test_delivery_receipts_are_acknowledged_and_ignored(app_client):
    raw, hdr = signed({"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.1", "status": "delivered", "recipient_id": "358401234567", "timestamp": "1"}]}}]}]})
    r = app_client.post("/webhooks/whatsapp", data=raw, headers=hdr)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "results": []}


@pytest.mark.mongo
def test_template_tap_with_payload_decides_the_card(app_client, flow):
    from cs import season
    from tests.test_season import item, make_card
    c = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    raw, hdr = signed({"entry": [{"changes": [{"value": {"messages": [{
        "from": "358000", "id": "wamid.in1", "timestamp": "1", "type": "button",
        "button": {"payload": f"APPROVE|{c.id}", "text": "Hyväksy"}}]}}]}]})
    r = app_client.post("/webhooks/whatsapp", data=raw, headers=hdr)
    assert r.status_code == 200 and r.get_json()["results"][0]["handled"] is True
    assert season.load_card(c.id).status in ("APPLIED", "CONFIRMED")
    with flow["client"]() as y:
        assert y.get_holiday_hours("FI-D0001") == {"2026-12-24": "07:30-13:00"}


@pytest.mark.mongo
def test_swipe_reply_finds_the_card_from_the_quoted_message(app_client, flow):
    from tests.test_season import item, make_card
    c = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    flow["db"][outbox.COLLECTION].insert_one({"id": "out-1", "entity_id": "FI-D0001", "direction": "out",
                                              "card_id": c.id, "provider_message_id": "wamid.card"})
    raw, hdr = signed({"entry": [{"changes": [{"value": {"messages": [{
        "from": "358000", "id": "wamid.in2", "timestamp": "1", "type": "text", "context": {"id": "wamid.card"},
        "text": {"body": "ei kiitos"}}]}}]}]})
    r = app_client.post("/webhooks/whatsapp", data=raw, headers=hdr)
    res = r.get_json()["results"][0]
    assert r.status_code == 200 and res["handled"] is True and res["action"] == "DECLINE"


@pytest.mark.mongo
def test_typed_reply_without_quote_reaches_the_card(app_client, flow, monkeypatch):
    from tests.test_season import item, make_card
    c = make_card(flow["db"], [item("2026-12-24", "07:30-13:00")])
    msg = OutgoingMessage(entity_id="FI-D0001", text="Joulun aukioloajat?", kind="CARD", card_id=c.id,
                          channel="WHATSAPP")
    msg.to = PHONE
    router.send(msg, db=flow["db"], whatsapp=channel(FakeMeta(), flow["db"]), audit=False)
    raw, hdr = signed({"entry": [{"changes": [{"value": {"messages": [{
        "from": "358401234567", "id": "wamid.in3", "timestamp": "1", "type": "text",
        "text": {"body": "ei kiitos"}}]}}]}]})
    r = app_client.post("/webhooks/whatsapp", data=raw, headers=hdr)
    res = r.get_json()["results"][0]
    assert r.status_code == 200 and res["handled"] is True and res["action"] == "DECLINE"


@pytest.mark.mongo
def test_forged_webhook_is_refused(app_client):
    raw, hdr = signed({"entry": []}, secret="someone-else")
    assert app_client.post("/webhooks/whatsapp", data=raw, headers=hdr).status_code == 403
