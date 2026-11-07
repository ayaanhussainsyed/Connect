"""E-mail channel: SMTP when ``SMTP_URL`` is set (``smtp://user:pass@host:587`` with STARTTLS, or
``smtps://user:pass@host:465``), otherwise inactive (the router falls back to the outbox).

The sender is ``SMTP_FROM`` (env) or the SMTP user. Buttons become a numbered list (and links
when a button carries a ``url``). Credentials are never logged.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from cs.channels.base import FAILED, SENT, Channel, ChannelNotConfigured, DeliveryRecord, as_outgoing
from cs.config import settings

DEFAULT_SUBJECT = {"fi": "Fonecta Connect: aukioloajat", "sv": "Connect: öppettider",
                   "nb": "Connect: åpningstider", "en": "Fonecta Connect: your opening hours"}


def is_configured() -> bool:
    return settings.has("smtp_url")


def render_body(text: str, buttons: list[dict]) -> str:
    lines = [text, ""] if buttons else [text]
    for b in buttons:
        url = b.get("url")
        lines.append(f"[{b['title']}] {url}" if url else f"{b.get('digit', '•')} = {b['title']}")
    return "\n".join(lines)


class EmailChannel(Channel):
    name = "EMAIL"

    def is_configured(self) -> bool:
        return is_configured()

    def send(self, message) -> DeliveryRecord:
        message = as_outgoing(message)
        if not self.is_configured():
            raise ChannelNotConfigured("E-mail is off: set SMTP_URL in .env")
        if not message.to:
            raise ValueError("e-mail needs a recipient address (message.to)")
        u = urlparse(settings.smtp_url)
        user, pwd = unquote(u.username or ""), unquote(u.password or "")
        sender = os.environ.get("SMTP_FROM") or user
        em = EmailMessage()
        em["Subject"] = message.subject or DEFAULT_SUBJECT.get(message.lang, DEFAULT_SUBJECT["en"])
        em["From"] = sender
        em["To"] = message.to
        em.set_content(render_body(message.text, message.buttons))
        try:
            if u.scheme == "smtps" or u.port == 465:
                server = smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=20)
            else:
                server = smtplib.SMTP(u.hostname, u.port or 587, timeout=20)
                server.starttls()
            with server:
                if user:
                    server.login(user, pwd)
                server.send_message(em)
        except (smtplib.SMTPException, OSError) as e:
            return DeliveryRecord(message.id, message.entity_id, message.channel, "EMAIL", FAILED,
                                  detail=f"SMTP error: {type(e).__name__}")
        return DeliveryRecord(message.id, message.entity_id, message.channel, "EMAIL", SENT, detail="sent by SMTP")
