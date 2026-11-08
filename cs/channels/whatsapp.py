"""WhatsApp Cloud API channel (BUILD_PLAN_A "Channels", APP_DESIGN O1).

- **Outside the 24-hour session** (no owner message in the last 24 h) only a pre-approved
  template may be sent: ``connect_season_card`` (utility), body ``TEMPLATE_BODIES[lang]`` with one
  variable ``{{1}}`` = a one-line summary (``cs.compose.compose_template_params``, put in
  ``parts["template_params"]`` by ``compose_card_message``) and 3 quick replies (Approve /
  Approve + every year / Edit). Template params may not contain newlines, tabs or more than
  4 spaces in a row, and a body can't start or end with a variable: every param goes through
  ``sanitize_template_param``. Exact texts for Meta: docs/WHATSAPP_TEMPLATE.md.
- **Inside the session** (the owner's first tap opens it): an interactive message with the
  full multi-line card and up to 3 reply buttons (titles ≤ 20 characters, body ≤ 1,024), or
  plain text when there are no buttons. Flow: template tap -> ``outbox.record_inbound(...)``
  (opens the session) -> ``router.send(<full card>)`` -> sent as interactive.
- **Inactive** when ``WHATSAPP_TOKEN`` / ``WHATSAPP_PHONE_NUMBER_ID`` are empty: ``send`` raises
  ``ChannelNotConfigured`` and the router falls back to the outbox.

Button payloads are ``"<BUTTON_ID>|<card_id>"`` (see ``split_payload``). Webhooks: ``verify_webhook``
answers Meta's GET handshake with ``WHATSAPP_VERIFY_TOKEN``; ``verify_signature`` checks
``X-Hub-Signature-256`` when ``WHATSAPP_APP_SECRET`` is set; ``parse_webhook`` turns a POST body into
``[{from, button_payload, text, context_message_id, message_id, timestamp, type}]``.
The token is only read from ``cs.config.settings`` and never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import httpx

from cs.channels.base import (FAILED, SENT, Channel, ChannelNotConfigured, DeliveryRecord,
                              OutgoingMessage, as_outgoing)
from cs.compose import TEMPLATE_PARAM_LIMIT, sanitize_template_param
from cs.config import settings

GRAPH_URL = "https://graph.facebook.com"
API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
TEMPLATE_NAME = os.environ.get("WHATSAPP_TEMPLATE", "connect_season_card")
TEMPLATE_LANG = {"fi": "fi", "sv": "sv", "nb": "nb", "en": "en"}
SESSION_WINDOW = timedelta(hours=24)
MAX_BUTTONS = 3
MAX_TITLE = 20
MAX_BODY = 1024
MAX_TEMPLATE_PARAM = TEMPLATE_PARAM_LIMIT

# The template bodies to create in Meta (category: utility), one per language. They carry the AI
# Act Art. 50 disclosure, because the template is the first message an owner gets. {{1}} is in
# the middle (a body can't start or end with a variable). Buttons: cs.compose.BUTTONS
# APPROVE / APPROVE_EVERY_YEAR / EDIT for the same language.
TEMPLATE_BODIES = {
    "fi": "Hei! Fonecta Connectin automaattinen avustaja ehdottaa aukioloaikoja: {{1}}. Vastaa alla olevilla painikkeilla.",
    "sv": "Hej! Fonecta Connects automatiska assistent föreslår öppettider: {{1}}. Svara med knapparna nedan.",
    "nb": "Hei! Fonecta Connects automatiske assistent foreslår åpningstider: {{1}}. Svar med knappene nedenfor.",
    "en": "Hi! Fonecta Connect's automated assistant suggests opening hours: {{1}}. Reply with the buttons below.",
}


def render_template(lang: str, params: list[str]) -> str:
    """What the owner sees for a template send (for the outbox / on-screen phone and tests)."""
    body = TEMPLATE_BODIES.get(lang, TEMPLATE_BODIES["en"])
    for i, p in enumerate(params, 1):
        body = body.replace("{{%d}}" % i, sanitize_template_param(p))
    return body


def is_configured() -> bool:
    return settings.has("whatsapp_token") and settings.has("whatsapp_phone_number_id")


def _verify_token() -> str:
    return getattr(settings, "whatsapp_verify_token", "") or os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


def make_payload(button_id: str, card_id: str | None) -> str:
    return f"{button_id}|{card_id or ''}"[:256]


def split_payload(payload: str | None) -> tuple[str | None, str | None]:
    """'APPROVE|card-123' -> ('APPROVE', 'card-123'); 'APPROVE' -> ('APPROVE', None)."""
    if not payload:
        return None, None
    bid, _, card = str(payload).partition("|")
    return bid or None, card or None


def build_interactive(to: str, text: str, buttons: list[dict], card_id: str | None = None) -> dict:
    """Interactive reply-button message (inside the 24 h session)."""
    if not buttons:
        raise ValueError("an interactive message needs at least one button")
    if len(buttons) > MAX_BUTTONS:
        raise ValueError(f"WhatsApp allows at most {MAX_BUTTONS} reply buttons")
    for b in buttons:
        if len(b["title"]) > MAX_TITLE:
            raise ValueError(f"button title over {MAX_TITLE} characters: {b['title']!r}")
    if len(text) > MAX_BODY:
        raise ValueError(f"body over {MAX_BODY} characters ({len(text)})")
    return {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "interactive",
        "interactive": {"type": "button", "body": {"text": text},
                        "action": {"buttons": [{"type": "reply",
                                                "reply": {"id": make_payload(b["id"], card_id), "title": b["title"]}}
                                               for b in buttons]}},
    }


def build_text(to: str, text: str) -> dict:
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
            "text": {"body": text[:4096], "preview_url": True}}


def build_template(to: str, lang: str, params: list[str], buttons: list[dict], card_id: str | None = None,
                   name: str = TEMPLATE_NAME) -> dict:
    """Template message (outside the session) with quick-reply payloads."""
    comps: list[dict] = []
    if params:
        comps.append({"type": "body", "parameters": [{"type": "text", "text": sanitize_template_param(p)}
                                                     for p in params]})
    for i, b in enumerate(buttons[:MAX_BUTTONS]):
        comps.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                      "parameters": [{"type": "payload", "payload": make_payload(b["id"], card_id)}]})
    return {"messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": TEMPLATE_LANG.get(lang, "en")}, "components": comps}}


class WhatsAppChannel(Channel):
    name = "WHATSAPP"

    def __init__(self, db=None, client: httpx.Client | None = None):
        self._db = db
        self._client = client

    def is_configured(self) -> bool:
        return is_configured()

    def in_session(self, entity_id: str, now: datetime | None = None) -> bool:
        """True if the owner wrote to us on WhatsApp in the last 24 hours (free-form allowed)."""
        try:
            from cs.channels.outbox import last_inbound_at
            last = last_inbound_at(entity_id, channel="WHATSAPP", db=self._db)
        except Exception:  # noqa: BLE001 - no DB: assume no session (template is always allowed)
            return False
        if last is None:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) - last < SESSION_WINDOW

    def build_payload(self, message: OutgoingMessage, in_session: bool | None = None) -> dict:
        if not message.to:
            raise ValueError("WhatsApp needs a recipient phone number (message.to)")
        session = self.in_session(message.entity_id) if in_session is None else in_session
        buttons = message.buttons[:MAX_BUTTONS]
        if session:
            return (build_interactive(message.to, message.text, buttons, message.card_id) if buttons
                    else build_text(message.to, message.text))
        tpl = message.template or {}
        params = tpl.get("params") or message.parts.get("template_params") or [message.text]
        return build_template(message.to, message.lang, params, buttons, message.card_id,
                              name=tpl.get("name") or TEMPLATE_NAME)

    def send(self, message, *, in_session: bool | None = None) -> DeliveryRecord:
        """Send one message. ``in_session=None`` checks the 24 h window in the outbox; True forces
        the interactive full card (e.g. right after the owner's first template tap)."""
        message = as_outgoing(message)
        if not self.is_configured():
            raise ChannelNotConfigured("WhatsApp is off: set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env")
        payload = self.build_payload(message, in_session)
        url = f"{GRAPH_URL}/{API_VERSION}/{settings.whatsapp_phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {settings.whatsapp_token}", "Content-Type": "application/json"}
        client = self._client or httpx.Client(timeout=15)
        try:
            r = client.post(url, headers=headers, json=payload)
            body = r.json() if r.content else {}
        except (httpx.HTTPError, ValueError) as e:
            return DeliveryRecord(message.id, message.entity_id, message.channel, "WHATSAPP", FAILED,
                                  detail=f"network error: {type(e).__name__}")
        finally:
            if self._client is None:
                client.close()
        if r.status_code >= 400:
            err = (body.get("error") or {}) if isinstance(body, dict) else {}
            return DeliveryRecord(message.id, message.entity_id, message.channel, "WHATSAPP", FAILED,
                                  detail=f"HTTP {r.status_code}: {str(err.get('message', ''))[:200]}")
        wamid = ((body.get("messages") or [{}])[0] or {}).get("id")
        kind = payload["type"]
        return DeliveryRecord(message.id, message.entity_id, message.channel, "WHATSAPP", SENT,
                              detail=f"sent as {kind}", provider_message_id=wamid)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def verify_webhook(mode: str | None, token: str | None, challenge: str | None) -> str | None:
    """Meta's GET handshake: return ``challenge`` if the verify token matches, else None."""
    expected = _verify_token()
    if mode == "subscribe" and expected and token and hmac.compare_digest(str(token), expected):
        return challenge
    return None


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Check ``X-Hub-Signature-256`` against ``WHATSAPP_APP_SECRET``. True when no secret is set
    (nothing to check against in the hackathon setup)."""
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[7:], digest)


def entity_for_sender(sender: str | None, db=None) -> str | None:
    """The business a typed reply is about when it has no button payload and quotes no message:
    the newest message we actually sent on WhatsApp to that number. In the demo every business
    shares the one test phone, so this is the business the owner was messaged about last."""
    from cs.channels.base import mask_address
    digits = "".join(ch for ch in str(sender or "") if ch.isdigit())
    if not digits:
        return None
    if db is None:
        from cs.db import get_db
        db = get_db()
    doc = db["outbox"].find_one(
        {"direction": "out", "delivered_via": "WHATSAPP", "status": SENT,
         "to_masked": {"$in": [mask_address("+" + digits), mask_address(digits)]}},
        {"_id": 0, "entity_id": 1}, sort=[("at", -1)])
    return (doc or {}).get("entity_id")


def is_template_reply(item: dict) -> bool:
    """True for a quick-reply tap on a template (the tap that opens the 24 h session)."""
    return item.get("type") == "button"


def parse_webhook(payload: dict) -> list[dict]:
    """POST body -> one dict per incoming message (status updates are ignored)::

        {"from", "button_payload", "text", "context_message_id", "message_id", "timestamp", "type",
         "button_id", "card_id"}
    """
    out: list[dict] = []
    for entry in (payload or {}).get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for m in value.get("messages", []) or []:
                t = m.get("type")
                item = {"from": m.get("from"), "message_id": m.get("id"), "timestamp": m.get("timestamp"),
                        "type": t, "context_message_id": (m.get("context") or {}).get("id"),
                        "button_payload": None, "text": None}
                if t == "button":  # quick reply on a template
                    item["button_payload"] = (m.get("button") or {}).get("payload")
                    item["text"] = (m.get("button") or {}).get("text")
                elif t == "interactive":
                    ir = m.get("interactive") or {}
                    reply = ir.get("button_reply") or ir.get("list_reply") or {}
                    item["button_payload"] = reply.get("id")
                    item["text"] = reply.get("title")
                elif t == "text":
                    item["text"] = (m.get("text") or {}).get("body")
                item["button_id"], item["card_id"] = split_payload(item["button_payload"])
                out.append(item)
    return out
