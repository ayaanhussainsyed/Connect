"""The outbox: every message in MongoDB, for the on-screen phone, the inbox and /ops. Always on.

Collection ``outbox`` holds the thread (both directions) per business::

    {id, entity_id, direction: out|in, kind, channel, delivered_via, status, detail, lang, text,
     buttons, card_id, token, to_masked, subject, provider_message_id, button_payload,
     context_message_id, at}

Outgoing messages are also upserted into ``messages`` (the ``cs.domain.Message`` shape plus
``entity_id``) so cards can find their messages. Raw texts expire after 30 days (TTL index on
``at``; GDPR minimisation, PLAN Part F). Addresses are stored masked only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure

from cs.channels.base import STORED, Channel, DeliveryRecord, OutgoingMessage, as_outgoing, mask_address
from cs.domain import new_id, utcnow

log = logging.getLogger(__name__)
COLLECTION = "outbox"
RETENTION = timedelta(days=30)
_indexed: set[str] = set()


def _db(db: Database | None) -> Database:
    if db is not None:
        return db
    from cs.db import get_db
    return get_db()


def ensure_indexes(db: Database | None = None) -> None:
    db = _db(db)
    if db.name in _indexed:
        return
    coll = db[COLLECTION]
    coll.create_index([("id", ASCENDING)], unique=True, name="id_unique")
    coll.create_index([("entity_id", ASCENDING), ("at", ASCENDING)], name="thread")
    coll.create_index([("card_id", ASCENDING)], name="card")
    try:
        coll.create_index([("at", ASCENDING)], expireAfterSeconds=int(RETENTION.total_seconds()), name="ttl_30d")
    except OperationFailure:  # an older index with other options: keep going
        pass
    _indexed.add(db.name)


def store_outgoing(message: OutgoingMessage, record: DeliveryRecord, db: Database | None = None) -> dict:
    """Write one outgoing message (and how it was delivered) to the thread."""
    db = _db(db)
    ensure_indexes(db)
    doc = {
        "id": message.id, "entity_id": message.entity_id, "direction": "out", "kind": message.kind,
        "channel": message.channel, "delivered_via": record.delivered_via, "status": record.status,
        "detail": record.detail, "lang": message.lang, "text": message.text, "buttons": message.buttons,
        "card_id": message.card_id, "token": message.token, "to_masked": mask_address(message.to),
        "subject": message.subject, "provider_message_id": record.provider_message_id,
        "button_payload": None, "context_message_id": None, "at": record.at,
    }
    db[COLLECTION].replace_one({"id": message.id}, doc, upsert=True)
    # The cs.domain.Message view, for card -> messages lookups. Never overwrite one the app saved.
    mdoc = {"id": message.id, "card_id": message.card_id, "channel": message.channel, "language": message.lang,
            "text": message.text, "buttons": message.buttons, "token": message.token, "sent_at": record.at,
            "entity_id": message.entity_id, "kind": message.kind, "delivered_via": record.delivered_via}
    try:
        db["messages"].update_one({"id": message.id}, {"$setOnInsert": mdoc}, upsert=True)
    except DuplicateKeyError:  # same token saved by the app under another id: the outbox copy is enough
        log.info("messages: token already stored for another message; outbox row kept")
    return doc


def record_inbound(entity_id: str, text: str | None = None, *, channel: str = "WHATSAPP",
                   button_payload: str | None = None, card_id: str | None = None,
                   context_message_id: str | None = None, provider_message_id: str | None = None,
                   kind: str = "REPLY", lang: str | None = None, at: datetime | None = None,
                   db: Database | None = None) -> dict:
    """Store an owner's reply in the thread (button tap or free text). No sender address is kept."""
    db = _db(db)
    ensure_indexes(db)
    doc = {"id": new_id("in"), "entity_id": entity_id, "direction": "in", "kind": kind, "channel": channel,
           "delivered_via": channel, "status": "RECEIVED", "detail": "", "lang": lang, "text": text,
           "buttons": [], "card_id": card_id, "token": None, "to_masked": None, "subject": None,
           "provider_message_id": provider_message_id, "button_payload": button_payload,
           "context_message_id": context_message_id, "at": at or utcnow()}
    db[COLLECTION].insert_one(dict(doc))
    return doc


def thread(entity_id: str, limit: int = 200, db: Database | None = None) -> list[dict]:
    """The conversation with one business, oldest first (for the on-screen phone)."""
    db = _db(db)
    cur = db[COLLECTION].find({"entity_id": entity_id}, {"_id": 0}).sort("at", DESCENDING).limit(limit)
    return list(reversed(list(cur)))


def recent(limit: int = 100, direction: str | None = None, db: Database | None = None) -> list[dict]:
    """Latest messages across all businesses, newest first (the ops inbox)."""
    q = {"direction": direction} if direction else {}
    return list(_db(db)[COLLECTION].find(q, {"_id": 0}).sort("at", DESCENDING).limit(limit))


def last_inbound_at(entity_id: str, channel: str | None = None, db: Database | None = None) -> datetime | None:
    q: dict = {"entity_id": entity_id, "direction": "in"}
    if channel:
        q["channel"] = channel
    doc = _db(db)[COLLECTION].find_one(q, {"_id": 0, "at": 1}, sort=[("at", DESCENDING)])
    return doc["at"] if doc else None


def clear_thread(entity_id: str, db: Database | None = None) -> int:
    """Delete one business's thread (demo reset). Returns the number of rows removed."""
    return _db(db)[COLLECTION].delete_many({"entity_id": entity_id}).deleted_count


class OutboxChannel(Channel):
    """Stores the message; that's the whole delivery. Used directly for APP_PUSH (the Connect app
    inbox) and as the fallback for every channel that isn't configured."""
    name = "OUTBOX"

    def __init__(self, db: Database | None = None):
        self._db = db

    def send(self, message, *, delivered_via: str = "OUTBOX", status: str = STORED, detail: str = "",
             provider_message_id: str | None = None) -> DeliveryRecord:
        message = as_outgoing(message)
        rec = DeliveryRecord(message_id=message.id, entity_id=message.entity_id, channel=message.channel,
                             delivered_via=delivered_via, status=status, detail=detail,
                             provider_message_id=provider_message_id)
        store_outgoing(message, rec, self._db)
        return rec
