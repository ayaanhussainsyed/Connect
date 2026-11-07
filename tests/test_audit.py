"""cs.audit: the chain verifies, and tampering of any kind is detected.

Pure tests run offline; ``@pytest.mark.mongo`` tests use the throwaway ``<MONGODB_DB>_test``
database (dropped at the end of the session).
"""
from __future__ import annotations

import hashlib
import threading
from datetime import date, datetime, timezone

import pytest

from cs import audit


def _chain(n: int) -> list[dict]:
    prev, out = audit.GENESIS, []
    for i in range(1, n + 1):
        body = {"seq": i, "event_type": "TEST", "at": f"2026-11-07T00:00:0{i}.000+00:00",
                "entity_id": "FI-D0001", "payload": {"i": i, "hours": "09:00-17:00"}}
        ev = {**body, "prev_hash": prev, "hash": audit.compute_hash(prev, body)}
        out.append(ev)
        prev = ev["hash"]
    return out


def test_canonical_json_is_deterministic():
    a = audit.canonical_json({"b": 1, "a": [1, 2], "ä": "ö"})
    b = audit.canonical_json({"ä": "ö", "a": [1, 2], "b": 1})
    assert a == b == '{"a":[1,2],"b":1,"ä":"ö"}'
    assert audit.canonical_json({"d": date(2026, 12, 24)}) == '{"d":"2026-12-24"}'
    with pytest.raises(ValueError):
        audit.canonical_json({"x": float("nan")})
    with pytest.raises(TypeError):
        audit.canonical_json({"x": object()})


def test_hash_formula():
    body = {"seq": 1, "event_type": "T", "at": "x", "entity_id": None, "payload": {"a": 1}}
    want = hashlib.sha256((audit.GENESIS + audit.canonical_json(body)).encode()).hexdigest()
    assert audit.compute_hash(audit.GENESIS, body) == want


def test_verify_events_ok():
    chain = _chain(5)
    res = audit.verify_events(chain)
    assert res.ok and res.count == 5 and res.head == chain[-1]["hash"]
    assert audit.verify_events([]).ok


@pytest.mark.parametrize("tamper", ["payload", "event_type", "entity_id", "at", "hash", "prev_hash",
                                    "delete_middle", "swap", "insert"])
def test_verify_events_detects_tampering(tamper):
    chain = _chain(5)
    if tamper == "payload":
        chain[2]["payload"]["hours"] = "closed"
    elif tamper in ("event_type", "entity_id", "at"):
        chain[2][tamper] = "forged"
    elif tamper == "hash":
        chain[2]["hash"] = "f" * 64
    elif tamper == "prev_hash":
        chain[2]["prev_hash"] = "f" * 64
    elif tamper == "delete_middle":
        del chain[2]
    elif tamper == "swap":
        chain[1], chain[2] = chain[2], chain[1]
    elif tamper == "insert":
        chain.insert(2, dict(chain[1]))
    res = audit.verify_events(chain)
    assert not res.ok and res.bad_seq is not None and res.reason


@pytest.mark.mongo
def test_log_and_verify_chain(audit_db):
    assert audit.head(db=audit_db) == audit.GENESIS
    evs = [audit.log("SUGGESTION_CREATED", {"n": i, "when": datetime(2026, 11, 7, tzinfo=timezone.utc),
                                            "hours": "07:30-13:00", "nested": {"b": [1, 2], "a": None}},
                     entity_id="FI-D0001", db=audit_db) for i in range(5)]
    assert [e["seq"] for e in evs] == [1, 2, 3, 4, 5]
    assert evs[0]["prev_hash"] == audit.GENESIS
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(evs, evs[1:]))
    res = audit.verify_chain(db=audit_db)
    assert res.ok and res.count == 5 and res.head == evs[-1]["hash"] == audit.head(db=audit_db)
    assert [e["seq"] for e in audit.events(entity_id="FI-D0001", db=audit_db)] == [5, 4, 3, 2, 1]


@pytest.mark.mongo
def test_tampering_in_the_database_is_detected(audit_db):
    for i in range(4):
        audit.log("WRITE", {"i": i, "hours": "09:00-17:00"}, entity_id="FI-D0001", db=audit_db)
    assert audit.verify_chain(db=audit_db).ok

    coll = audit_db.audit_events
    coll.update_one({"seq": 2}, {"$set": {"payload.hours": "closed"}})
    res = audit.verify_chain(db=audit_db)
    assert not res.ok and res.bad_seq == 2

    coll.update_one({"seq": 2}, {"$set": {"payload.hours": "09:00-17:00"}})
    assert audit.verify_chain(db=audit_db).ok

    coll.update_one({"seq": 3}, {"$set": {"event_type": "FORGED"}})
    assert audit.verify_chain(db=audit_db).bad_seq == 3
    coll.update_one({"seq": 3}, {"$set": {"event_type": "WRITE"}})

    coll.delete_one({"seq": 2})
    res = audit.verify_chain(db=audit_db)
    assert not res.ok and res.bad_seq == 3


@pytest.mark.mongo
def test_concurrent_appends_keep_one_chain(audit_db):
    errors = []

    def worker(k):
        try:
            for i in range(4):
                audit.log("CONCURRENT", {"worker": k, "i": i}, db=audit_db)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    res = audit.verify_chain(db=audit_db)
    assert res.ok and res.count == 16


@pytest.mark.mongo
def test_batch_writes_one_chain_even_with_a_concurrent_writer(audit_db):
    audit.log("BEFORE", {"i": 0}, db=audit_db)
    with audit.batch():
        for i in range(5):
            ev = audit.log("BATCHED", {"i": i}, db=audit_db)
            assert "seq" not in ev                                   # buffered, not written yet
        t = threading.Thread(target=lambda: audit.log("OTHER_THREAD", {"x": 1}, db=audit_db))
        t.start()
        t.join()                                                     # takes seq 2 while we are buffering
        assert audit_db.audit_events.count_documents({}) == 2
    res = audit.verify_chain(db=audit_db)
    assert res.ok and res.count == 7
    types = [e["event_type"] for e in audit_db.audit_events.find({}, {"_id": 0}).sort("seq", 1)]
    assert types == ["BEFORE", "OTHER_THREAD"] + ["BATCHED"] * 5


@pytest.mark.mongo
def test_batched_decorator_flushes_on_error(audit_db):
    @audit.batched
    def boom():
        audit.log("STEP", {"n": 1}, db=audit_db)
        raise RuntimeError("fail midway")

    with pytest.raises(RuntimeError):
        boom()
    assert audit.verify_chain(db=audit_db).ok and audit_db.audit_events.count_documents({"event_type": "STEP"}) == 1
