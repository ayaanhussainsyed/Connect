"""cs.config (secret redaction), cs.domain (round trips, state machines) and cs.db (guards, repository)."""
from __future__ import annotations

import pytest

from cs import config, db, domain
from cs.domain import (Card, Decision, Evidence, LearnedFact, Message, Permission, Suggestion,
                       WriteRecord)


# --- config -----------------------------------------------------------------
def test_settings_repr_never_contains_secret_values():
    s = config.settings
    text = repr(s) + str(s)
    leaked = [name for name in config.SECRET_FIELDS
              if getattr(s, name) and len(getattr(s, name)) >= 4 and getattr(s, name) in text]
    assert leaked == [], f"secret fields visible in repr: {leaked}"   # names only, never values
    for name in config.SECRET_FIELDS:
        if getattr(s, name):
            assert f"{name}='***'" in text


def test_settings_paths_and_defaults():
    s = config.settings
    assert s.dataset_dir.is_dir()
    assert (s.dataset_dir / "START_HERE.md").exists()
    assert s.mongodb_db  # a name, not a secret
    assert s.engine in ("baseline", "ml")


# --- domain -----------------------------------------------------------------
def _samples():
    sug = Suggestion(entity_id="FI-D0001", kind="HOLIDAY_HOURS", date="2026-12-24", day_key="XMAS_EVE",
                     shown="07:30-18:00", action="SUGGEST", value="07:30-13:00", probability=0.82,
                     basis="OWN_LAST_YEAR",
                     evidence=[Evidence("OWN_LAST_YEAR", "2025-12-24 you entered 07:30-13:00", "hol:1", 1.0)])
    card = Card(entity_id="FI-D0001", season=2026, suggestion_ids=[sug.id])
    return [
        sug, card,
        Message(card_id=card.id, channel="OUTBOX", language="fi", text="Hei!", buttons=[{"id": "ok"}]),
        Decision(card_id=card.id, suggestion_id=sug.id, decision="APPROVE", final_value="07:30-13:00"),
        WriteRecord(entity_id="FI-D0001", target="MOCK", request={"hours": {"holidayHours": []}}),
        Permission(entity_id="FI-D0001", scope={"day_keys": ["XMAS_EVE"], "values": {"XMAS_EVE": "07:30-13:00"}}),
        LearnedFact(entity_id="FI-D0001", day_key="XMAS_EVE", date="2026-12-24", value="07:30-13:00", season=2026),
    ]


@pytest.mark.parametrize("obj", _samples(), ids=lambda o: type(o).__name__)
def test_domain_round_trip(obj):
    doc = obj.to_doc()
    assert doc["id"] == obj.id
    back = type(obj).from_doc({**doc, "_id": "ignored"})
    assert back == obj


def test_suggestion_evidence_is_rebuilt():
    sug = _samples()[0]
    back = Suggestion.from_doc(sug.to_doc())
    assert isinstance(back.evidence[0], Evidence) and back.evidence[0].source == "OWN_LAST_YEAR"


def test_state_machines():
    T = domain.can_transition
    assert T(domain.CARD_TRANSITIONS, "DRAFT", "SENT")
    assert T(domain.CARD_TRANSITIONS, "APPLIED", "UNDONE")
    assert not T(domain.CARD_TRANSITIONS, "EXPIRED", "SENT")
    assert not T(domain.CARD_TRANSITIONS, "DRAFT", "APPLIED")
    assert set(domain.CARD_TRANSITIONS) == set(domain.CARD_STATES)
    assert T(domain.PERMISSION_TRANSITIONS, "GRANTED", "REVOKED")
    assert not T(domain.PERMISSION_TRANSITIONS, "REVOKED", "GRANTED")


# --- db ---------------------------------------------------------------------
def test_get_db_refuses_other_databases():
    app = db.app_db_name()
    for name in ("admin", "local", "config", f"{app}_other", "test", f"{app}_testx", f"{app}_test-1",
                 f"{app}_test_a.b", f"x{app}_test_1"):
        with pytest.raises(db.DatabaseNotAllowed):
            db.get_db(name)
    assert db.test_db_name() == f"{app}_test"
    assert db.test_db_name("123_ab12") == f"{app}_test_123_ab12"
    assert db.is_test_db_name(f"{app}_test_123_ab12") and not db.is_test_db_name(app)
    with pytest.raises(ValueError):
        db.test_db_name("../evil")
    for name in (app, "admin", f"{app}_other"):
        with pytest.raises(db.DatabaseNotAllowed):
            db.drop_test_db(name)


@pytest.mark.mongo
def test_session_uses_its_own_test_database(mongo_test_db):
    assert db.is_test_db_name(mongo_test_db.name)
    assert mongo_test_db.name not in (db.app_db_name(), db.test_db_name())


@pytest.mark.mongo
def test_repository_round_trip(mongo_test_db):
    names = set(mongo_test_db.list_collection_names())
    assert set(db.COLLECTIONS) <= names  # ensure_indexes created them all
    for obj in _samples():
        db.save(obj, db=mongo_test_db)
        got = db.get(type(obj), obj.id, db=mongo_test_db)
        assert got == obj
    sug = _samples()[0]
    db.save(sug, db=mongo_test_db)
    sug.action = "NO_ACTION"
    db.save(sug, db=mongo_test_db)  # replace, not duplicate
    found = db.find(Suggestion, {"id": sug.id}, db=mongo_test_db)
    assert len(found) == 1 and found[0].action == "NO_ACTION"
