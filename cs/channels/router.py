"""Pick the owner's channel, honour opt-outs, fall back to the outbox (BUILD_PLAN_A "Channels").

    ch = router.channel_for("FI-D0001")                     # compose for this channel ...
    msg = compose.compose_card_message(biz, sugs, router.language_for(eid), ch, ...)
    rec = router.send(OutgoingMessage.from_composed(eid, msg, kind="CARD", card_id=card.id))

Rules:
- the preferred channel and language come from ``customer_contacts.csv``;
- an owner with ``opted_out_of_suggestions = 1`` gets no proactive message (card, reminder,
  question, autopilot note …): ``send`` returns a ``SKIPPED`` record "skipped: opted out".
  Replies to the owner's own action (confirmation, follow-up, emergency) still go out;
- WhatsApp / e-mail are used only when configured **and** a test recipient is set
  (``WHATSAPP_TEST_TO`` / ``SMTP_TEST_TO``; the dataset's addresses are fictional). Otherwise,
  or if the provider fails, the message goes to the outbox with the reason in ``detail``;
- APP_PUSH means the Connect app inbox, which *is* the outbox; SMS is the stub (outbox);
- every real send is mirrored into the outbox, so the on-screen phone always shows it;
- each delivery leaves an audit event (no text, no address).
"""
from __future__ import annotations

import logging
import os

from cs.channels.base import (FAILED, PROACTIVE_KINDS, SENT, SKIPPED, DeliveryRecord, OutgoingMessage,
                              as_outgoing)
from cs.channels.email import EmailChannel
from cs.channels.outbox import OutboxChannel
from cs.channels.sms import SmsChannel
from cs.channels.whatsapp import WhatsAppChannel

log = logging.getLogger(__name__)
KNOWN = ("WHATSAPP", "SMS", "EMAIL", "APP_PUSH", "OUTBOX")
TEST_TO_ENV = {"WHATSAPP": "WHATSAPP_TEST_TO", "EMAIL": "SMTP_TEST_TO"}


def _dataset_id(entity_id: str) -> str:
    try:
        from cs.confirm import dataset_id  # SinceAi04 (sandbox) -> FI-D0001
        return dataset_id(entity_id)
    except Exception:  # noqa: BLE001
        return entity_id


def contact(entity_id: str) -> dict | None:
    """The owner's row in customer_contacts.csv (or None). Contains personal data: don't log it."""
    from cs import data
    c = data.contacts()
    row = c[c["entity_id"] == _dataset_id(entity_id)]
    return None if row.empty else row.iloc[0].to_dict()


def opted_out(entity_id: str) -> bool:
    c = contact(entity_id)
    return bool(c and int(c.get("opted_out_of_suggestions") or 0))


def channel_for(entity_id: str) -> str:
    """The channel to compose for: the owner's preferred channel (OUTBOX if unknown)."""
    c = contact(entity_id)
    ch = str((c or {}).get("preferred_channel") or "OUTBOX").upper()
    return ch if ch in KNOWN else "OUTBOX"


def language_for(entity_id: str) -> str:
    c = contact(entity_id)
    return str((c or {}).get("preferred_language") or "fi").lower()


def standing_permission(entity_id: str) -> bool:
    c = contact(entity_id)
    return bool(c and int(c.get("standing_permission_holidays") or 0))


def _recipient(channel: str, c: dict | None) -> str | None:
    """Test recipient from .env first; the dataset address only if ALLOW_REAL_RECIPIENTS=1."""
    test = os.environ.get(TEST_TO_ENV.get(channel, ""), "").strip()
    if test:
        return test
    if os.environ.get("ALLOW_REAL_RECIPIENTS") == "1" and c:
        return c.get("mobile") if channel == "WHATSAPP" else c.get("email")
    return None


def _audit(rec: DeliveryRecord, kind: str, db) -> None:
    try:
        from cs import audit
        audit.log("MESSAGE_DELIVERY", {"message_id": rec.message_id, "kind": kind, "channel": rec.channel,
                                       "delivered_via": rec.delivered_via, "status": rec.status,
                                       "detail": rec.detail}, entity_id=rec.entity_id, db=db)
    except Exception as e:  # noqa: BLE001 - delivery must not fail because of the audit log
        log.warning("audit log failed for a delivery (%s)", type(e).__name__)


def send(message, *, db=None, audit: bool = True, whatsapp: WhatsAppChannel | None = None,
         email: EmailChannel | None = None, whatsapp_session: bool | None = None) -> DeliveryRecord:
    """Deliver one message on the owner's channel (see the module doc for the rules).

    ``whatsapp_session``: None = look at the 24 h window (template outside it, interactive full
    card inside it); True = force the interactive full card (after the first template tap)."""
    msg: OutgoingMessage = as_outgoing(message)
    c = contact(msg.entity_id)
    if c and int(c.get("opted_out_of_suggestions") or 0) and msg.kind.upper() in PROACTIVE_KINDS:
        rec = DeliveryRecord(msg.id, msg.entity_id, msg.channel, "NONE", SKIPPED, detail="skipped: opted out")
        if audit:
            _audit(rec, msg.kind, db)
        return rec
    channel = msg.channel if msg.channel and msg.channel != "OUTBOX" else channel_for(msg.entity_id)
    msg.channel = channel
    outbox = OutboxChannel(db)
    rec: DeliveryRecord
    if channel in ("WHATSAPP", "EMAIL"):
        real = (whatsapp or WhatsAppChannel(db)) if channel == "WHATSAPP" else (email or EmailChannel())
        to = msg.to or _recipient(channel, c)
        if not real.is_configured():
            rec = outbox.send(msg, detail=f"fallback: {channel} not configured")
        elif not to:
            rec = outbox.send(msg, detail=f"fallback: no test recipient ({TEST_TO_ENV[channel]} not set)")
        else:
            msg.to = to
            try:
                sent = (real.send(msg, in_session=whatsapp_session) if channel == "WHATSAPP"
                        else real.send(msg))
            except Exception as e:  # noqa: BLE001
                sent = DeliveryRecord(msg.id, msg.entity_id, channel, channel, FAILED, detail=type(e).__name__)
            if sent.status == SENT:
                rec = outbox.send(msg, delivered_via=sent.delivered_via, status=SENT, detail=sent.detail,
                                  provider_message_id=sent.provider_message_id)
            else:
                rec = outbox.send(msg, detail=f"fallback: {channel} failed ({sent.detail})")
    elif channel == "SMS":
        msg.to = msg.to or (c or {}).get("mobile")  # stored masked only; decides the Traficom note
        rec = SmsChannel(db).send(msg)
    else:  # APP_PUSH / OUTBOX: the Connect app inbox
        rec = outbox.send(msg)
    if audit:
        _audit(rec, msg.kind, db)
    return rec
