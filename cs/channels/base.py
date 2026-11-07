"""The channel interface: ``Channel.send(message) -> DeliveryRecord`` (BUILD_PLAN_A "Channels").

``OutgoingMessage`` is what a channel sends: the composed text and buttons (``cs.compose``) plus
who it is for. ``DeliveryRecord`` says what happened. The recipient address (``to``) is only
held in memory: it is never logged, and the outbox stores a masked copy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from cs.domain import new_id, utcnow

# Message kinds. Proactive kinds are skipped for opted-out owners; replies to the owner's own
# action (confirmation, follow-up, emergency) still go out.
PROACTIVE_KINDS = frozenset({"CARD", "REMINDER", "QUESTION", "AUTOPILOT", "SUGGESTION", "REVIEW_REPLY", "DRIFT"})
REPLY_KINDS = frozenset({"CONFIRMATION", "FOLLOWUP", "EMERGENCY", "READBACK"})

# Delivery statuses
SENT = "SENT"          # handed to a real provider (WhatsApp, SMTP)
STORED = "STORED"      # in the outbox only (on-screen phone / inbox)
SKIPPED = "SKIPPED"    # deliberately not sent (opted out)
FAILED = "FAILED"


class ChannelNotConfigured(RuntimeError):
    """The channel has no credentials in .env; the router then falls back to the outbox."""


@dataclass
class OutgoingMessage:
    entity_id: str
    text: str
    lang: str = "fi"
    channel: str = "OUTBOX"            # the channel the text was composed for (owner's preferred)
    buttons: list[dict] = field(default_factory=list)
    kind: str = "CARD"
    card_id: str | None = None
    token: str | None = None
    to: str | None = None              # phone / e-mail: never logged, masked in the outbox
    subject: str | None = None
    template: dict | None = None       # WhatsApp template: {"name", "params": [...]} (optional)
    parts: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("msg"))

    @classmethod
    def from_composed(cls, entity_id: str, composed: dict, *, kind: str = "CARD", card_id: str | None = None,
                      token: str | None = None, lang: str | None = None, channel: str | None = None,
                      to: str | None = None) -> "OutgoingMessage":
        """Wrap the ``{"text", "buttons", "parts"}`` that ``cs.compose`` returns."""
        parts = dict(composed.get("parts") or {})
        return cls(entity_id=entity_id, text=composed["text"], buttons=list(composed.get("buttons") or []),
                   lang=lang or parts.get("lang") or "fi", channel=(channel or parts.get("channel") or "OUTBOX").upper(),
                   kind=kind, card_id=card_id, token=token, to=to, subject=parts.get("subject"), parts=parts)


def as_outgoing(message: Any, entity_id: str | None = None) -> OutgoingMessage:
    """Accept an ``OutgoingMessage``, a ``cs.domain.Message`` (needs ``entity_id``) or a plain dict."""
    if isinstance(message, OutgoingMessage):
        return message
    if isinstance(message, dict):
        d = dict(message)
        if "language" in d and "lang" not in d:
            d["lang"] = d.pop("language")
        if entity_id and not d.get("entity_id"):
            d["entity_id"] = entity_id
        names = OutgoingMessage.__dataclass_fields__.keys()
        return OutgoingMessage(**{k: v for k, v in d.items() if k in names})
    # cs.domain.Message
    if not entity_id:
        raise ValueError("a cs.domain.Message needs entity_id=...")
    return OutgoingMessage(entity_id=entity_id, text=message.text, lang=message.language,
                           channel=(message.channel or "OUTBOX").upper(), buttons=list(message.buttons or []),
                           card_id=message.card_id, token=message.token, id=message.id)


@dataclass
class DeliveryRecord:
    message_id: str
    entity_id: str
    channel: str                       # the channel asked for (owner's preferred)
    delivered_via: str                 # OUTBOX | WHATSAPP | EMAIL | SMS_STUB | NONE
    status: str                        # SENT | STORED | SKIPPED | FAILED
    detail: str = ""
    provider_message_id: str | None = None
    at: datetime = field(default_factory=utcnow)

    @property
    def ok(self) -> bool:
        return self.status in (SENT, STORED)

    def to_doc(self) -> dict:
        return asdict(self)


class Channel(ABC):
    name: str = "BASE"

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def send(self, message: OutgoingMessage) -> DeliveryRecord:
        """Deliver one message. Raises ``ChannelNotConfigured`` if the channel has no credentials."""


def mask_address(to: str | None) -> str | None:
    """'+3580001448732' -> '+358…732'; 'owner@example.com' -> 'o…@example.com'."""
    if not to:
        return None
    to = str(to).strip()
    if "@" in to:
        local, _, domain = to.partition("@")
        return f"{local[:1]}…@{domain}"
    return f"{to[:4]}…{to[-3:]}" if len(to) > 7 else "…"
