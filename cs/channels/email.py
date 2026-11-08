"""E-mail channel: SMTP when ``SMTP_URL`` is set (``smtp://user:pass@host:587`` with STARTTLS, or
``smtps://user:pass@host:465``), otherwise inactive (the router falls back to the outbox).

The sender is ``SMTP_FROM`` (env) or the SMTP user. Buttons become a numbered list (and links
when a button carries a ``url``). Credentials are never logged.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from cs.channels.base import FAILED, SENT, Channel, ChannelNotConfigured, DeliveryRecord, as_outgoing
from cs.config import settings

def _tls_context() -> ssl.SSLContext:
    """Certificates for the TLS handshake. Python builds on macOS often lack the system ones, which makes
    every secure mail connection fail verification; certifi's bundle is reliable everywhere."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi missing: the system certificates
        return ssl.create_default_context()


def _open(u, ctx: ssl.SSLContext):
    """A connected, encrypted SMTP session. SSL on 465 when asked for; otherwise STARTTLS on 587, and if that
    port is blocked (some venue networks drop it), SSL on 465, which the big providers also accept."""
    if u.scheme == "smtps" or u.port == 465:
        return smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=8, context=ctx)
    server = None
    try:
        server = smtplib.SMTP(u.hostname, u.port or 587, timeout=6)
        server.starttls(context=ctx)
        return server
    except (smtplib.SMTPException, OSError):
        if server is not None:
            try:
                server.close()
            except Exception:  # noqa: BLE001
                pass
        return smtplib.SMTP_SSL(u.hostname, 465, timeout=8, context=ctx)


DEFAULT_SUBJECT = {"fi": "Connect Suggests: aukioloajat", "sv": "Connect Suggests: öppettider",
                   "nb": "Connect Suggests: åpningstider", "en": "Connect Suggests: your opening hours"}


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
            # 8 s: a blocked mail port (some venue networks) must not stall the owner's reply for long;
            # on failure the router falls back to the outbox (the on-screen phone).
            server = _open(u, _tls_context())
            with server:
                if user:
                    server.login(user, pwd)
                server.send_message(em)
        except (smtplib.SMTPException, OSError) as e:
            return DeliveryRecord(message.id, message.entity_id, message.channel, "EMAIL", FAILED,
                                  detail=f"SMTP error: {type(e).__name__}")
        return DeliveryRecord(message.id, message.entity_id, message.channel, "EMAIL", SENT, detail="sent by SMTP")
