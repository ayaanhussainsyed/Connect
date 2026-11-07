"""Inbound owner replies.

    GET  /webhooks/whatsapp     Meta's verify handshake (hub.mode / hub.verify_token / hub.challenge)
    POST /webhooks/whatsapp     signature check -> cs.channels.whatsapp.parse_webhook -> handle_reply
    POST /webhooks/sms          {entity_id | card_id, text} (form or JSON) -> parse_reply -> handle_reply

The button payload is "BUTTON_ID|card_id" (set by the message helper), so a tap finds its card even
when the owner has several. Without it we use the context message, then the owner's latest card.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, request

from cs import decide_flow

bp = Blueprint("webhooks", __name__)
log = logging.getLogger(__name__)


@bp.get("/webhooks/whatsapp")
def whatsapp_verify():
    from cs.channels import whatsapp
    challenge = whatsapp.verify_webhook(request.args.get("hub.mode"), request.args.get("hub.verify_token"),
                                        request.args.get("hub.challenge"))
    if challenge is None:
        abort(403)
    return challenge, 200, {"Content-Type": "text/plain"}


def _card_for_context(context_message_id: str | None) -> str | None:
    if not context_message_id:
        return None
    from cs.db import get_db
    doc = get_db()["outbox"].find_one({"provider_message_id": context_message_id}, {"_id": 0, "card_id": 1})
    return (doc or {}).get("card_id")


@bp.post("/webhooks/whatsapp")
def whatsapp_inbound():
    from cs.channels import whatsapp
    raw = request.get_data()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        abort(403)
    results = []
    for item in whatsapp.parse_webhook(request.get_json(silent=True) or {}):
        card_id = item.get("card_id") or _card_for_context(item.get("context_message_id"))
        if not card_id and not item.get("text"):
            results.append({"handled": False, "reason": "no card for this reply"})
            continue
        res = decide_flow.handle_reply(card_id=card_id, button_id=item.get("button_id"),
                                       text=None if item.get("button_id") else item.get("text"), channel="WHATSAPP",
                                       sender=item.get("from"), base_url=request.host_url)
        results.append({k: res.get(k) for k in ("handled", "action", "reason")})
    return jsonify(ok=True, results=results)  # always 200, or Meta retries


@bp.post("/webhooks/sms")
def sms_inbound():
    body = request.get_json(silent=True) or request.form
    text = (body.get("text") or body.get("Body") or "").strip()
    if not text:
        return jsonify(error="text is required"), 400
    res = decide_flow.handle_reply(body.get("entity_id"), card_id=body.get("card_id"), text=text, channel="SMS",
                                   sender=body.get("from") or body.get("From"), base_url=request.host_url)
    return jsonify({k: res.get(k) for k in ("handled", "action", "reason")})
