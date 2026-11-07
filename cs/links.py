"""A8 – signed, expiring, single-use links for the no-login season card.

    token = sign(card_id, owner_id)                 # put it in the message: /c/<token>
    p = verify(token)                               # every request (GET too): signature + expiry
    p = consume(token, "decide", db)                # state-changing POSTs only: once per action

Token = ``<payload>.<signature>``, both base64url without padding. The payload is the canonical
JSON of ``{"c": card_id, "o": owner_id, "e": expiry (unix s), "n": nonce}``; the signature is
HMAC-SHA256 over the payload text with ``LINK_SECRET``.

Single use: ``consume`` inserts ``{key: "<nonce>:<action>"}`` into ``link_nonces`` (unique index;
a TTL index removes it after expiry). A second insert fails, so a replayed POST is rejected. The
actions are ``decide``, ``undo`` and ``emergency``: one link can decide once and undo once.
Reading the card (GET) only calls ``verify`` and never changes anything, so link scanners and
previews cannot approve.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from cs.config import settings

DEFAULT_TTL = timedelta(days=30)
ACTIONS = ("decide", "undo", "emergency")


class TokenError(ValueError):
    """``kind``: malformed | bad_signature | expired | used | wrong_card."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class TokenPayload:
    card_id: str
    owner_id: str
    expires: datetime
    nonce: str


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret(secret: str | None) -> bytes:
    s = secret if secret is not None else settings.link_secret
    if not s:
        raise RuntimeError("LINK_SECRET is not set in .env")
    return s.encode("utf-8")


def _mac(body: str, secret: str | None) -> str:
    return _b64e(hmac.new(_secret(secret), body.encode("ascii"), hashlib.sha256).digest())


def sign(card_id: str, owner_id: str, *, ttl: timedelta = DEFAULT_TTL, now: datetime | None = None,
         nonce: str | None = None, secret: str | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    payload = {"c": card_id, "o": owner_id, "e": int((now + ttl).timestamp()),
               "n": nonce or secrets.token_urlsafe(12)}
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_mac(body, secret)}"


def verify(token: str, *, now: datetime | None = None, secret: str | None = None) -> TokenPayload:
    """Check format, signature (constant time) and expiry. Raises ``TokenError``."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise TokenError("malformed", "This link is not valid.")
    body, sig = token.split(".")
    if not body or not sig:
        raise TokenError("malformed", "This link is not valid.")
    if not hmac.compare_digest(_mac(body, secret), sig):
        raise TokenError("bad_signature", "This link is not valid.")
    try:
        p = json.loads(_b64d(body))
        payload = TokenPayload(card_id=str(p["c"]), owner_id=str(p["o"]),
                               expires=datetime.fromtimestamp(int(p["e"]), tz=timezone.utc), nonce=str(p["n"]))
    except (ValueError, KeyError, TypeError):
        raise TokenError("malformed", "This link is not valid.") from None
    if (now or datetime.now(timezone.utc)) >= payload.expires:
        raise TokenError("expired", "This link has expired.")
    return payload


def consume(token: str, action: str, db: Database | None = None, *, now: datetime | None = None,
            secret: str | None = None) -> TokenPayload:
    """``verify`` + mark (nonce, action) as used. A second call raises ``TokenError("used")``."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    p = verify(token, now=now, secret=secret)
    if db is None:
        from cs.db import get_db
        db = get_db()
    try:
        db["link_nonces"].insert_one({"key": f"{p.nonce}:{action}", "card_id": p.card_id, "action": action,
                                      "used_at": now or datetime.now(timezone.utc), "expires_at": p.expires})
    except DuplicateKeyError:
        raise TokenError("used", "This link has already been used for that.") from None
    return p


def is_used(token: str, action: str, db: Database | None = None) -> bool:
    """Read-only check (for rendering): has this action been done with this link already?"""
    p = verify(token)
    if db is None:
        from cs.db import get_db
        db = get_db()
    return db["link_nonces"].count_documents({"key": f"{p.nonce}:{action}"}, limit=1) > 0


def release(token: str, action: str, db: Database | None = None) -> None:
    """Undo a ``consume`` when the POST decided nothing (e.g. a missing tick), so the owner can retry."""
    p = verify(token)
    if db is None:
        from cs.db import get_db
        db = get_db()
    db["link_nonces"].delete_one({"key": f"{p.nonce}:{action}"})


def link_for(token: str, base_url: str | None = None) -> str:
    """Absolute card link. ``base_url`` (e.g. the web request's host) wins over PUBLIC_BASE_URL."""
    return f"{(base_url or settings.public_base_url).rstrip('/')}/c/{token}"
