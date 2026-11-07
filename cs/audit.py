"""Append-only, hash-chained audit log in MongoDB ``audit_events``.

Each event stores ``seq`` (1, 2, 3 …), ``prev_hash`` and
``hash = sha256(prev_hash + canonical_json(body))`` where ``body`` is
``{"seq", "event_type", "at", "entity_id", "payload"}``. Hashing the whole body (not only the
payload) means a changed event type, time, entity or order is detected too.

The payload is stored exactly as it was hashed (normalised through ``canonical_json``), so a
MongoDB round trip cannot break verification. ``verify_chain`` recomputes every hash, checks
the links and that ``seq`` has no gaps. Deleting events from the *end* of the chain can only be
detected against an external anchor: keep ``head()`` somewhere else (e.g. the export or README).

There is deliberately no update or delete function in this module.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError

GENESIS = "0" * 64
COLLECTION = "audit_events"


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    raise TypeError(f"not JSON serialisable for the audit log: {type(o).__name__}")


def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, no spaces, UTF-8 kept, no NaN/Infinity."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=_default)


def compute_hash(prev_hash: str, body: dict) -> str:
    return hashlib.sha256((prev_hash + canonical_json(body)).encode("utf-8")).hexdigest()


def _body(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("seq", "event_type", "at", "entity_id", "payload")}


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    count: int                  # events checked
    head: str                   # hash of the last good event (GENESIS if none)
    bad_seq: int | None = None  # first event that failed
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def verify_events(events: Iterable[dict]) -> VerifyResult:
    """Check a sequence of events (in ``seq`` order). Pure: no database needed."""
    prev, expected, n = GENESIS, 1, 0
    for ev in events:
        seq = ev.get("seq")
        if seq != expected:
            return VerifyResult(False, n, prev, seq, f"expected seq {expected}, found {seq}")
        if ev.get("prev_hash") != prev:
            return VerifyResult(False, n, prev, seq, "prev_hash does not link to the previous event")
        if compute_hash(prev, _body(ev)) != ev.get("hash"):
            return VerifyResult(False, n, prev, seq, "hash does not match the event's content")
        prev, expected, n = ev["hash"], expected + 1, n + 1
    return VerifyResult(True, n, prev)


def _coll(db: Database | None):
    if db is None:
        from cs.db import get_db
        db = get_db()
    return db[COLLECTION]


_buffer = threading.local()


@contextmanager
def batch(db: Database | None = None):
    """Buffer ``log`` calls in this thread and append them with one ``insert_many`` at the end
    (same chain, same order; much faster on a remote MongoDB). Nested batches flush once."""
    depth = getattr(_buffer, "depth", 0)
    if depth == 0:
        _buffer.events = []
        _buffer.db = db
    _buffer.depth = depth + 1
    try:
        yield
    finally:
        _buffer.depth -= 1
        if _buffer.depth == 0:
            events, _buffer.events = _buffer.events, []
            _flush(events)


def batched(fn):
    """Decorator: run ``fn`` inside ``batch()`` (its audit events are written in one go)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with batch():
            return fn(*args, **kwargs)
    return wrapper


def _flush(events: list[tuple]) -> None:
    """events: [(coll, event_type, payload, entity_id, at)] -> chained inserts, retried on a race."""
    by_coll: dict[int, tuple] = {}
    for ev in events:
        by_coll.setdefault(id(ev[0]), (ev[0], []))[1].append(ev)
    for coll, evs in by_coll.values():
        pending = list(evs)
        for _ in range(20):
            if not pending:
                break
            last = coll.find_one({}, {"_id": 0, "seq": 1, "hash": 1}, sort=[("seq", DESCENDING)])
            seq, prev = ((last["seq"], last["hash"]) if last else (0, GENESIS))
            docs = []
            for _c, event_type, payload, entity_id, at in pending:
                seq += 1
                body = {"seq": seq, "event_type": event_type, "at": at, "entity_id": entity_id, "payload": payload}
                doc = {**body, "prev_hash": prev, "hash": compute_hash(prev, body)}
                prev = doc["hash"]
                docs.append(doc)
            try:
                coll.insert_many(docs, ordered=True)
                pending = []
            except BulkWriteError as e:
                done = e.details.get("nInserted", 0)
                pending = pending[done:]
        if pending:
            raise RuntimeError("audit log: could not append a batch after several retries")


def log(event_type: str, payload: dict, entity_id: str | None = None,
        db: Database | None = None, max_retries: int = 20) -> dict:
    """Append one event and return it. Safe with concurrent writers: ``seq`` is unique, so a
    race makes one insert fail and that writer retries on the new head. Inside ``batch()`` the
    event is buffered (the returned dict then has no ``seq``/``hash`` yet)."""
    coll = _coll(db)
    stored_payload = json.loads(canonical_json(payload))  # what we store == what we hash
    if getattr(_buffer, "depth", 0) > 0:
        at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        _buffer.events.append((coll, event_type, stored_payload, entity_id, at))
        return {"event_type": event_type, "at": at, "entity_id": entity_id, "payload": stored_payload}
    for _ in range(max_retries):
        last = coll.find_one({}, {"_id": 0, "seq": 1, "hash": 1}, sort=[("seq", DESCENDING)])
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else GENESIS
        body = {
            "seq": seq,
            "event_type": event_type,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "entity_id": entity_id,
            "payload": stored_payload,
        }
        doc = {**body, "prev_hash": prev, "hash": compute_hash(prev, body)}
        try:
            coll.insert_one(doc)
        except DuplicateKeyError:
            continue
        doc.pop("_id", None)
        return doc
    raise RuntimeError("audit log: could not append after several retries (heavy contention?)")


def verify_chain(db: Database | None = None) -> VerifyResult:
    """Recompute every hash in the stored chain."""
    return verify_events(_coll(db).find({}, {"_id": 0}).sort("seq", ASCENDING))


def head(db: Database | None = None) -> str:
    last = _coll(db).find_one({}, {"_id": 0, "hash": 1}, sort=[("seq", DESCENDING)])
    return last["hash"] if last else GENESIS


def events(entity_id: str | None = None, event_type: str | None = None, limit: int = 100,
           db: Database | None = None) -> list[dict]:
    """Latest events first, optionally filtered (for the ops screen and "why?")."""
    q: dict = {}
    if entity_id:
        q["entity_id"] = entity_id
    if event_type:
        q["event_type"] = event_type
    return list(_coll(db).find(q, {"_id": 0}).sort("seq", DESCENDING).limit(limit))
