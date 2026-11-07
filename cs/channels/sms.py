"""SMS channel: a stub that stores the message in the outbox.

Real SMS would only ever go to non-Finnish numbers (Traficom's rules block unregistered
alphanumeric/A2P senders to Finnish numbers), so for the demo the stub is enough: the text
(with "1 = …, 2 = …" digits from ``cs.compose``) shows on the on-screen phone.
"""
from __future__ import annotations

from cs.channels.base import STORED, Channel, DeliveryRecord, as_outgoing
from cs.channels.outbox import OutboxChannel


def is_finnish(number: str | None) -> bool:
    n = (number or "").replace(" ", "")
    return n.startswith("+358") or n.startswith("00358")


def real_sms_allowed(number: str | None) -> bool:
    """Only non-Finnish numbers could ever get a real SMS from us."""
    return bool(number) and not is_finnish(number)


class SmsChannel(Channel):
    name = "SMS"

    def __init__(self, db=None):
        self._outbox = OutboxChannel(db)

    def is_configured(self) -> bool:
        return True  # the stub is always available

    def send(self, message) -> DeliveryRecord:
        message = as_outgoing(message)
        why = ("stub: Finnish numbers get no real SMS (Traficom)" if is_finnish(message.to)
               else "stub: no SMS provider configured")
        return self._outbox.send(message, delivered_via="SMS_STUB", status=STORED, detail=why)
