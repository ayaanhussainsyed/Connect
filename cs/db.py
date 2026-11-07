"""MongoDB access: one client, the app's collections, indexes and tiny repository helpers.

Safety: only the app database ``settings.mongodb_db`` and throwaway test databases whose name
starts with ``settings.mongodb_db + "_test"`` are ever opened; anything else raises. Each pytest
session uses its own ``<db>_test_<pid>`` (so parallel runs by several agents can't clobber each
other) and drops it afterwards. Only test databases can be dropped.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import TypeVar

import certifi
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from cs.config import settings
from cs.domain import DOMAIN_TYPES

COLLECTIONS = ("suggestions", "cards", "messages", "decisions", "writes", "permissions",
               "learned_facts", "audit_events", "link_nonces", "outbox", "emergencies", "schedules", "reply_context")

# Tests set this (or pass name=...) to use the throwaway database.
DB_ENV_OVERRIDE = "CS_MONGODB_DB"

T = TypeVar("T")


class DatabaseNotAllowed(RuntimeError):
    pass


def app_db_name() -> str:
    return settings.mongodb_db


def test_db_name(suffix: str | int | None = None) -> str:
    """``<db>_test`` or ``<db>_test_<suffix>`` (suffix: letters, digits, _ only)."""
    base = f"{settings.mongodb_db}_test"
    if suffix is None or suffix == "":
        return base
    suffix = str(suffix)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", suffix):
        raise ValueError(f"bad test database suffix {suffix!r}")
    return f"{base}_{suffix}"


def is_test_db_name(name: str) -> bool:
    base = f"{settings.mongodb_db}_test"
    return name == base or bool(re.fullmatch(re.escape(base) + r"_[A-Za-z0-9_]{1,40}", name))


def is_allowed_db_name(name: str) -> bool:
    return name == app_db_name() or is_test_db_name(name)


def allowed_db_names() -> tuple[str, str]:
    """The app database and the base test name (suffixed test names are allowed too)."""
    return (app_db_name(), test_db_name())


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    if not settings.has("mongodb_uri"):
        raise RuntimeError("MONGODB_URI is not set in .env")
    return MongoClient(
        settings.mongodb_uri,
        tlsCAFile=certifi.where(),
        tz_aware=True,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=10000,
        socketTimeoutMS=60000,   # never wait forever on a dropped Atlas connection (the default is no timeout)
        appname="connect-suggests",
    )


def get_db(name: str | None = None) -> Database:
    """The app database, or the test database when ``name``/``CS_MONGODB_DB`` says so."""
    name = name or os.environ.get(DB_ENV_OVERRIDE) or app_db_name()
    if not is_allowed_db_name(name):
        raise DatabaseNotAllowed(f"database {name!r} is not {app_db_name()!r} or a {test_db_name()}* database")
    return get_client()[name]


def ping(timeout_ms: int = 5000) -> bool:
    try:
        get_client().admin.command("ping", maxTimeMS=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


def ensure_indexes(db: Database | None = None) -> None:
    db = db if db is not None else get_db()
    for c in ("suggestions", "cards", "messages", "decisions", "writes", "permissions", "learned_facts"):
        db[c].create_index([("id", ASCENDING)], unique=True, name="id_unique")
    db.suggestions.create_index([("entity_id", ASCENDING), ("date", ASCENDING), ("kind", ASCENDING)])
    db.cards.create_index([("entity_id", ASCENDING), ("season", ASCENDING)])
    db.cards.create_index([("status", ASCENDING)])
    db.messages.create_index([("card_id", ASCENDING)])
    db.messages.create_index([("entity_id", ASCENDING), ("sent_at", ASCENDING)])
    # Partial (not sparse): sparse still indexes explicit nulls, and most messages have token=None.
    db.messages.create_index([("token", ASCENDING)], unique=True, name="token_unique",
                             partialFilterExpression={"token": {"$type": "string"}})
    db.decisions.create_index([("card_id", ASCENDING)])
    db.decisions.create_index([("suggestion_id", ASCENDING)])
    db.writes.create_index([("entity_id", ASCENDING), ("at", DESCENDING)])
    db.permissions.create_index([("entity_id", ASCENDING), ("status", ASCENDING)])
    db.learned_facts.create_index([("entity_id", ASCENDING), ("day_key", ASCENDING), ("season", DESCENDING)])
    db.audit_events.create_index([("seq", ASCENDING)], unique=True, name="seq_unique")
    # Single-use link tokens: one doc per (nonce, action); expired ones are removed by a TTL index.
    db.link_nonces.create_index([("key", ASCENDING)], unique=True, name="key_unique")
    db.link_nonces.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl")
    db.decisions.create_index([("batch_id", ASCENDING)])
    db.emergencies.create_index([("id", ASCENDING)], unique=True, name="id_unique")
    db.emergencies.create_index([("entity_id", ASCENDING), ("created_at", DESCENDING)])
    db.schedules.create_index([("id", ASCENDING)], unique=True, name="id_unique")
    db.schedules.create_index([("active", ASCENDING), ("next_run", ASCENDING)])
    db.reply_context.create_index([("entity_id", ASCENDING), ("at", DESCENDING)])
    try:  # the message helper's outbox owns its indexes (incl. the 30-day TTL on texts)
        from cs.channels import outbox
        outbox._indexed.discard(db.name)
        outbox.ensure_indexes(db)
    except ImportError:
        db.outbox.create_index([("entity_id", ASCENDING), ("at", ASCENDING)])
    db.learned_facts.create_index([("batch_id", ASCENDING)])
    db.audit_events.create_index([("event_type", ASCENDING)])
    db.audit_events.create_index([("entity_id", ASCENDING)])


def drop_test_db(name: str | None = None) -> None:
    """Drop a test database (``<db>_test`` by default). Refuses any other name."""
    name = name or test_db_name()
    if not is_test_db_name(name) or name == app_db_name():
        raise DatabaseNotAllowed(f"refusing to drop {name!r}: not a test database")
    get_client().drop_database(name)


# ---------------------------------------------------------------------------
# Repository helpers (no ORM)
# ---------------------------------------------------------------------------
def _collection_for(obj_or_cls) -> str:
    cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)
    if cls not in DOMAIN_TYPES:
        raise TypeError(f"{cls.__name__} is not a stored domain type")
    return cls.collection


def save(obj, db: Database | None = None):
    """Insert or replace a domain object by its ``id``."""
    db = db if db is not None else get_db()
    db[_collection_for(obj)].replace_one({"id": obj.id}, obj.to_doc(), upsert=True)
    return obj


def get(cls: type[T], obj_id: str, db: Database | None = None) -> T | None:
    db = db if db is not None else get_db()
    doc = db[_collection_for(cls)].find_one({"id": obj_id}, {"_id": 0})
    return cls.from_doc(doc) if doc else None  # type: ignore[attr-defined]


def find(cls: type[T], query: dict, db: Database | None = None, sort=None, limit: int = 0) -> list[T]:
    db = db if db is not None else get_db()
    cur = db[_collection_for(cls)].find(query, {"_id": 0})
    if sort:
        cur = cur.sort(sort)
    if limit:
        cur = cur.limit(limit)
    return [cls.from_doc(d) for d in cur]  # type: ignore[attr-defined]
