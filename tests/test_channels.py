"""cs.channels: outbox write/read (test DB), router opt-out skip and fallback, WhatsApp payloads,
webhook parsing and verification, SMS stub."""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from cs import compose
from cs.channels import base, outbox, router, sms, whatsapp
from cs.channels.base import OutgoingMessage


def opted_out_entity() -> str:
    from cs import data
    c = data.contacts()
    return c[c["opted_out_of_suggestions"] == 1]["entity_id"].iloc[0]


def card_message(entity_id="FI-D0003", channel="WHATSAPP", kind="CARD") -> OutgoingMessage:
    composed = compose.compose_card_message(
        {"name": "Kukkakauppa Kide"},
        [{"date": "2026-12-24", "value": "09:00-13:00", "shown": "09:00-18:00", "basis": "OWN_LAST_YEAR"}],
        "fi", channel, True, None, None, None)
    return OutgoingMessage.from_composed(entity_id, composed, kind=kind, card_id="card-test")


# ---------------------------------------------------------------------------
# No database needed
# ---------------------------------------------------------------------------
def test_router_skips_opted_out_owner():
    eid = opted_out_entity()
    assert router.opted_out(eid)
    rec = router.send(OutgoingMessage(entity_id=eid, text="Hei!", kind="CARD"), audit=False)
    assert rec.status == base.SKIPPED and rec.detail == "skipped: opted out"
    assert rec.delivered_via == "NONE"
    for kind in ("REMINDER", "QUESTION", "AUTOPILOT"):
        assert router.send(OutgoingMessage(entity_id=eid, text="x", kind=kind), audit=False).status == base.SKIPPED


def test_router_reads_the_contacts_csv():
    assert router.channel_for("FI-D0003") == "WHATSAPP"
    assert router.channel_for("FI-D0001") == "SMS"
    assert router.channel_for("SE-D0008") == "EMAIL"
    assert router.language_for("FI-D0010") == "sv"
    assert router.standing_permission("FI-D0012")
    assert router.channel_for("NO-SUCH-ID") == "OUTBOX"


SAMPLE_WEBHOOK = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550000000", "phone_number_id": "123"},
        "contacts": [{"profile": {"name": "Test"}, "wa_id": "358401234567"}],
        "messages": [
            {"from": "358401234567", "id": "wamid.A", "timestamp": "1767000000", "type": "button",
             "context": {"from": "15550000000", "id": "wamid.CARD"},
             "button": {"payload": "APPROVE_EVERY_YEAR|card-1", "text": "Hyväksy + joka vuosi"}},
            {"from": "358401234567", "id": "wamid.B", "timestamp": "1767000001", "type": "interactive",
             "context": {"id": "wamid.CARD2"},
             "interactive": {"type": "button_reply", "button_reply": {"id": "EDIT|card-1", "title": "Muokkaa"}}},
            {"from": "358401234567", "id": "wamid.C", "timestamp": "1767000002", "type": "text",
             "text": {"body": "kiinni tapaninpäivänä, muuten ok"}},
        ]}}]},
        {"id": "WABA", "changes": [{"field": "messages", "value": {
            "statuses": [{"id": "wamid.CARD", "status": "delivered"}]}}]}],
}


def test_parse_webhook_sample_payload():
    items = whatsapp.parse_webhook(SAMPLE_WEBHOOK)
    assert len(items) == 3  # the status update is ignored
    a, b, c = items
    assert a["from"] == "358401234567" and a["button_payload"] == "APPROVE_EVERY_YEAR|card-1"
    assert a["context_message_id"] == "wamid.CARD" and a["button_id"] == "APPROVE_EVERY_YEAR"
    assert a["card_id"] == "card-1"
    assert b["button_payload"] == "EDIT|card-1" and b["text"] == "Muokkaa" and b["context_message_id"] == "wamid.CARD2"
    assert c["text"] == "kiinni tapaninpäivänä, muuten ok" and c["button_payload"] is None
    assert whatsapp.parse_webhook({}) == []


def test_verify_webhook_and_signature(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "s3cret-verify")
    assert whatsapp.verify_webhook("subscribe", "s3cret-verify", "12345") == "12345"
    assert whatsapp.verify_webhook("subscribe", "wrong", "12345") is None
    assert whatsapp.verify_webhook("unsubscribe", "s3cret-verify", "12345") is None
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN")
    assert whatsapp.verify_webhook("subscribe", "", "12345") is None  # no token configured: refuse
    body = json.dumps(SAMPLE_WEBHOOK).encode()
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(body, sig)
    assert not whatsapp.verify_signature(body + b" ", sig)
    assert not whatsapp.verify_signature(body, None)


def test_whatsapp_payloads():
    msg = card_message()
    inter = whatsapp.build_interactive("358401234567", msg.text, msg.buttons, "card-1")
    btns = inter["interactive"]["action"]["buttons"]
    assert len(btns) == 3 and btns[1]["reply"] == {"id": "APPROVE_EVERY_YEAR|card-1", "title": "Hyväksy + joka vuosi"}
    tpl = whatsapp.build_template("358401234567", "fi", [msg.text], msg.buttons, "card-1")
    assert tpl["template"]["name"] == "connect_season_card" and tpl["template"]["language"]["code"] == "fi"
    param = tpl["template"]["components"][0]["parameters"][0]["text"]
    assert "\n" not in param and "    " not in param
    assert [c["parameters"][0]["payload"] for c in tpl["template"]["components"][1:]] == \
        ["APPROVE|card-1", "APPROVE_EVERY_YEAR|card-1", "EDIT|card-1"]
    with pytest.raises(ValueError):
        whatsapp.build_interactive("1", "x", [{"id": "A", "title": "x"}] * 4)
    with pytest.raises(ValueError):
        whatsapp.build_interactive("1", "x", [{"id": "A", "title": "a" * 21}])
    with pytest.raises(ValueError):
        whatsapp.build_interactive("1", "x" * 1025, [{"id": "A", "title": "ok"}])


def test_whatsapp_inactive_without_credentials(monkeypatch):
    monkeypatch.setattr(whatsapp, "is_configured", lambda: False)
    with pytest.raises(base.ChannelNotConfigured):
        whatsapp.WhatsAppChannel().send(card_message())


def test_whatsapp_send_with_mock_transport(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})

    class Configured(whatsapp.WhatsAppChannel):
        def is_configured(self):
            return True

        def in_session(self, entity_id, now=None):
            return False

    ch = Configured(client=httpx.Client(transport=httpx.MockTransport(handler)))
    msg = card_message()
    msg.to = "358401234567"
    rec = ch.send(msg)
    assert rec.status == base.SENT and rec.provider_message_id == "wamid.SENT"
    assert seen["body"]["type"] == "template"  # no 24 h session -> template
    assert seen["auth"].startswith("Bearer")


def test_mask_and_sms_rules():
    assert base.mask_address("+3580001448732") == "+358…732"
    assert base.mask_address("owner@example.com") == "o…@example.com"
    assert sms.is_finnish("+358 40 123") and not sms.real_sms_allowed("+358401234567")
    assert sms.real_sms_allowed("+46701234567")


# ---------------------------------------------------------------------------
# MongoDB (test database only)
# ---------------------------------------------------------------------------
@pytest.fixture
def outbox_db(mongo_test_db):
    mongo_test_db[outbox.COLLECTION].delete_many({})
    mongo_test_db["messages"].delete_many({})
    outbox._indexed.discard(mongo_test_db.name)
    yield mongo_test_db
    mongo_test_db[outbox.COLLECTION].delete_many({})
    mongo_test_db["messages"].delete_many({})


@pytest.mark.mongo
def test_outbox_write_and_read(outbox_db):
    msg = card_message(entity_id="FI-D0003")
    msg.to = "+3580003510028"
    rec = outbox.OutboxChannel(outbox_db).send(msg)
    assert rec.status == base.STORED and rec.delivered_via == "OUTBOX"
    outbox.record_inbound("FI-D0003", "kiinni tapaninpäivänä, muuten ok", channel="WHATSAPP",
                          card_id="card-test", db=outbox_db)
    th = outbox.thread("FI-D0003", db=outbox_db)
    assert [m["direction"] for m in th] == ["out", "in"]
    assert th[0]["text"] == msg.text and th[0]["buttons"] == msg.buttons and th[0]["kind"] == "CARD"
    assert th[0]["to_masked"] == "+358…028" and "3510028" not in json.dumps(th, default=str)
    assert th[1]["text"] == "kiinni tapaninpäivänä, muuten ok"
    assert outbox_db["messages"].find_one({"id": msg.id})["card_id"] == "card-test"
    assert outbox.last_inbound_at("FI-D0003", channel="WHATSAPP", db=outbox_db) is not None
    assert outbox.thread("FI-D0001", db=outbox_db) == []
    assert outbox.recent(db=outbox_db)[0]["direction"] == "in"
    assert "ttl_30d" in outbox_db[outbox.COLLECTION].index_information()


@pytest.mark.mongo
def test_router_falls_back_to_outbox(outbox_db, monkeypatch):
    monkeypatch.setattr(whatsapp, "is_configured", lambda: False)
    rec = router.send(card_message(entity_id="FI-D0003"), db=outbox_db)
    assert rec.status == base.STORED and rec.delivered_via == "OUTBOX"
    assert rec.channel == "WHATSAPP" and "not configured" in rec.detail
    sms_rec = router.send(card_message(entity_id="FI-D0001", channel="SMS"), db=outbox_db)
    assert sms_rec.delivered_via == "SMS_STUB" and "Traficom" in sms_rec.detail  # FI-D0001 has a +358 number
    push = router.send(OutgoingMessage(entity_id="FI-D0014", text="Hei!", kind="CARD"), db=outbox_db)
    assert push.channel == "APP_PUSH" and push.delivered_via == "OUTBOX"
    assert len(outbox.thread("FI-D0003", db=outbox_db)) == 1
    ev = outbox_db["audit_events"].find_one({"event_type": "MESSAGE_DELIVERY", "entity_id": "FI-D0003"})
    assert ev and "text" not in ev["payload"]


@pytest.mark.mongo
def test_router_sends_replies_to_opted_out_owner(outbox_db):
    eid = opted_out_entity()
    rec = router.send(OutgoingMessage(entity_id=eid, text="✓ Tallennettu", kind="EMERGENCY"),
                      db=outbox_db, audit=False)
    assert rec.status == base.STORED


# ---------------------------------------------------------------------------
# First contact: the template carries a one-line {{1}}; the full card follows in the session
# ---------------------------------------------------------------------------
NASTY = "Rivi 1\nRivi 2\r\n\tsarkain     viisi välilyöntiä\u2028loppu  "


def test_template_bodies_follow_meta_rules():
    for lang, body in whatsapp.TEMPLATE_BODIES.items():
        assert body.count("{{1}}") == 1
        assert not body.startswith("{{") and not body.endswith("}}")
        assert "\n" not in body and "Connect" in body
    from cs.compose import BUTTONS
    for lang in whatsapp.TEMPLATE_BODIES:
        for bid in ("APPROVE", "APPROVE_EVERY_YEAR", "EDIT"):
            assert len(BUTTONS[lang][bid]) <= 20


def test_every_template_param_is_sanitised():
    msg = card_message()
    tpl = whatsapp.build_template("358401234567", "fi", [NASTY, "", "a" * 2000], msg.buttons, "card-1")
    params = [p["text"] for p in tpl["template"]["components"][0]["parameters"]]
    for p in params:
        assert "\n" not in p and "\r" not in p and "\t" not in p and "\u2028" not in p
        assert "     " not in p and "  " not in p
        assert 0 < len(p) <= whatsapp.MAX_TEMPLATE_PARAM
    assert params[0] == "Rivi 1 · Rivi 2 · sarkain viisi välilyöntiä · loppu"
    assert params[1] == "-"
    assert params[2].endswith("…")


def test_first_contact_uses_one_line_template_param_then_full_card_in_session():
    msg = card_message()
    msg.to = "358401234567"
    param = msg.parts["template_params"][0]
    assert "\n" not in param and "Hyväksy" not in param
    ch = whatsapp.WhatsAppChannel()
    out = ch.build_payload(msg, in_session=False)
    assert out["type"] == "template"
    sent_param = out["template"]["components"][0]["parameters"][0]["text"]
    assert sent_param == param and sent_param.startswith("to 24.12. 9–13")
    shown = whatsapp.render_template("fi", [sent_param])
    assert ".." not in shown and shown.endswith("Vastaa alla olevilla painikkeilla.")
    inside = ch.build_payload(msg, in_session=True)
    assert inside["type"] == "interactive"
    assert inside["interactive"]["body"]["text"] == msg.text and "\n" in msg.text  # the full card
    assert whatsapp.is_template_reply(whatsapp.parse_webhook(SAMPLE_WEBHOOK)[0])
    assert not whatsapp.is_template_reply(whatsapp.parse_webhook(SAMPLE_WEBHOOK)[1])


@pytest.mark.mongo
def test_session_opens_after_first_tap(outbox_db):
    ch = whatsapp.WhatsAppChannel(db=outbox_db)
    msg = card_message(entity_id="FI-D0003")
    msg.to = "358401234567"
    assert ch.build_payload(msg)["type"] == "template"
    outbox.record_inbound("FI-D0003", "Hyväksy", channel="WHATSAPP", button_payload="APPROVE|card-test",
                          db=outbox_db)
    assert ch.build_payload(msg)["type"] == "interactive"
