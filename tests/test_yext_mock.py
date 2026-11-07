"""cs.yext against the dataset's mock Yext API (started by scripts/run_mock.py on a free port,
with a throw-away state file). The WriteRecord / audit test uses the ``_test`` MongoDB database."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

from cs import audit
from cs.hours import REGULAR, week_from_yext
from cs.yext import (EV_OWNER_WINS, EV_REMOVE, EV_REOPEN, EV_SKIPPED, EV_WRITE, EV_WRITE_REGULAR, YextClient,
                     holiday_map, merge_holiday_hours, regular_hours_body, reopen_body)

ROOT = Path(__file__).resolve().parents[1]
EID = "FI-D0001"
TODAY = date(2026, 11, 7)
TEST_KEY = "mock-test-key-4f1c9a"  # the mock accepts any key; distinctive so a leak is detectable


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def mock_url():
    port = _free_port()
    env = {**os.environ, "MOCK_QUIET": "1"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "run_mock.py"), "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"mock exited early: {proc.stdout.read()}")
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("mock did not start within 60 s")
    yield url
    proc.send_signal(signal.SIGINT)  # lets run_mock.py delete its temporary state file
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def client(mock_url):
    httpx.post(f"{mock_url}/mock/reset", timeout=10).raise_for_status()
    c = YextClient("mock", base_url=mock_url, api_key=TEST_KEY, persist=False)
    yield c
    c.close()


def _raw_put_holidays(mock_url: str, items: list[dict]) -> None:
    """Simulate the owner (or another tool) editing the listing directly."""
    r = httpx.put(f"{mock_url}/v2/accounts/me/entities/{EID}", params={"v": "20261106"},
                  headers={"api-key": TEST_KEY}, json={"hours": {"holidayHours": items}}, timeout=10)
    r.raise_for_status()


# ---------------------------------------------------------------------------
def test_merge_keeps_other_dates(client):
    client.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00"}, today=TODAY)
    rec = client.set_holiday_hours(EID, {"2026-12-31": "10:00-14:00"}, today=TODAY)
    assert client.get_holiday_hours(EID) == {"2026-12-24": "07:30-13:00", "2026-12-31": "10:00-14:00"}
    assert [i["date"] for i in rec.request["body"]["hours"]["holidayHours"]] == ["2026-12-24", "2026-12-31"]
    # Overwriting a date keeps one entry for it.
    client.set_holiday_hours(EID, {"2026-12-24": "closed"}, today=TODAY)
    assert client.get_holiday_hours(EID) == {"2026-12-24": "closed", "2026-12-31": "10:00-14:00"}


def test_past_dates_are_dropped(client, mock_url):
    # The mock (unlike the sandbox) keeps past dates, so seed one directly.
    _raw_put_holidays(mock_url, [{"date": "2026-11-01", "isClosed": True},
                                 {"date": "2026-12-31", "isClosed": True}])
    rec = client.set_holiday_hours(EID, {"2025-12-24": "closed", "2026-12-24": "07:30-13:00"}, today=TODAY)
    sent = [i["date"] for i in rec.request["body"]["hours"]["holidayHours"]]
    assert sent == ["2026-12-24", "2026-12-31"]
    assert rec.request["dropped_past"] == ["2025-12-24"]
    assert rec.request["applied"] == {"2026-12-24": "07:30-13:00"}
    assert client.get_holiday_hours(EID) == {"2026-12-24": "07:30-13:00", "2026-12-31": "closed"}


def test_only_past_dates_writes_nothing(client):
    rec = client.set_holiday_hours(EID, {"2025-12-24": "closed"}, today=TODAY)
    assert rec.response_status is None and rec.request["body"] is None
    assert client.get_holiday_hours(EID) == {}


def test_weekday_hours_untouched(client):
    before = client.get_entity(EID)["hours"]
    rec = client.set_holiday_hours(EID, {"2026-12-24": "closed"}, today=TODAY)
    assert set(rec.request["body"]) == {"hours"} and set(rec.request["body"]["hours"]) == {"holidayHours"}
    after = client.get_entity(EID)["hours"]
    assert week_from_yext(after) == week_from_yext(before) == [
        "07:30-18:00"] * 5 + ["09:00-18:00", "10:00-16:00"]
    assert rec.request["weekdays_unchanged"] is True


def test_owner_wins_cancels_a_changed_date(client, mock_url):
    snapshot = client.get_holiday_hours(EID)          # taken when the suggestion was made: empty
    _raw_put_holidays(mock_url, [{"date": "2026-12-24", "openIntervals": [{"start": "10:00", "end": "14:00"}]}])
    expected = {d: snapshot.get(d) for d in ("2026-12-24", "2026-12-31")}   # both None
    rec = client.set_holiday_hours(EID, {"2026-12-24": "closed", "2026-12-31": "closed"},
                                   expected_current=expected, decision_id="dec-test", today=TODAY)
    assert rec.request["cancelled"] == {"2026-12-24": {"expected": None, "live": "10:00-14:00",
                                                       "wanted": "closed"}}
    assert rec.request["applied"] == {"2026-12-31": "closed"}
    assert rec.readback_ok is True
    # The owner's value is kept, ours is written next to it.
    assert client.get_holiday_hours(EID) == {"2026-12-24": "10:00-14:00", "2026-12-31": "closed"}


def test_owner_wins_all_cancelled_sends_nothing(client, mock_url):
    _raw_put_holidays(mock_url, [{"date": "2026-12-24", "isClosed": True}])
    rec = client.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00"},
                                   expected_current={"2026-12-24": None}, today=TODAY)
    assert rec.response_status is None and "2026-12-24" in rec.request["cancelled"]
    assert client.get_holiday_hours(EID) == {"2026-12-24": "closed"}


def test_owner_already_set_the_same_value_is_not_a_conflict(client, mock_url):
    _raw_put_holidays(mock_url, [{"date": "2026-12-24", "openIntervals": [{"start": "07:30", "end": "13:00"}]}])
    rec = client.set_holiday_hours(EID, {"2026-12-24": "7.30-13"}, expected_current={"2026-12-24": None},
                                   today=TODAY)
    assert rec.request["cancelled"] == {} and rec.request["applied"] == {"2026-12-24": "07:30-13:00"}


def test_expected_matches_live_is_written(client, mock_url):
    _raw_put_holidays(mock_url, [{"date": "2026-12-24", "isClosed": True}])
    rec = client.set_holiday_hours(EID, {"2026-12-24": "09:00-12:00"},
                                   expected_current={"2026-12-24": "closed"}, today=TODAY)
    assert rec.request["applied"] == {"2026-12-24": "09:00-12:00"} and rec.readback_ok


def test_regular_maps_to_is_regular_hours(client):
    rec = client.set_holiday_hours(EID, {"2026-12-26": "regular"}, today=TODAY)
    assert rec.request["applied"] == {"2026-12-26": REGULAR}
    items = client.get_entity(EID)["hours"]["holidayHours"]
    assert items == [{"date": "2026-12-26", "isRegularHours": True}]
    assert client.get_holiday_hours(EID) == {"2026-12-26": REGULAR}


def test_readback_matches(client):
    rec = client.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00", "2026-12-25": "closed",
                                         "2026-12-31": "10:00-14:00,17:00-02:00"}, today=TODAY)
    assert rec.response_status == 200 and rec.readback_ok is True
    assert rec.request["readback"] == {"2026-12-24": "07:30-13:00", "2026-12-25": "closed",
                                       "2026-12-31": "10:00-14:00,17:00-02:00"}
    assert rec.target == "MOCK" and rec.entity_id == EID and "READ-BACK" not in rec.note


def test_clear_sends_an_empty_list(client):
    client.set_holiday_hours(EID, {"2026-12-24": "closed"}, today=TODAY)
    rec = client.clear_holiday_hours(EID)
    assert rec.request["body"] == {"hours": {"holidayHours": []}}
    assert rec.request["removed"] == {"2026-12-24": "closed"}
    assert rec.readback_ok is True and client.get_holiday_hours(EID) == {}


def test_bad_input_fails_before_any_call(client):
    with pytest.raises(ValueError):
        client.set_holiday_hours(EID, {"2026-12-24": "9-9"}, today=TODAY)
    with pytest.raises(ValueError):
        client.set_holiday_hours(EID, {"2026-13-24": "closed"}, today=TODAY)
    with pytest.raises(ValueError):
        client.set_holiday_hours(EID, {}, today=TODAY)
    assert client.get_holiday_hours(EID) == {}


def test_sandbox_mapping_applies_only_to_the_sandbox():
    mock = YextClient("mock", base_url="http://127.0.0.1:1", api_key=TEST_KEY, id_map={EID: "SB-1"},
                      persist=False)
    sbx = YextClient("sandbox", base_url="http://127.0.0.1:1", api_key=TEST_KEY, id_map={EID: "SB-1"},
                     persist=False)
    assert mock.yext_id(EID) == EID
    assert sbx.yext_id(EID) == "SB-1" and sbx.yext_id("SB-1") == "SB-1"
    assert TEST_KEY not in repr(mock) + repr(sbx)


def test_merge_helper_is_pure():
    existing = [{"date": "2026-12-31", "isClosed": True}, {"date": "2026-01-01", "isClosed": True},
                {"date": "2026-12-24", "isClosed": True}]
    out = merge_holiday_hours(existing, {"2026-12-24": "07:30-13:00", "2026-12-26": REGULAR}, TODAY)
    assert out == [{"date": "2026-12-24", "openIntervals": [{"start": "07:30", "end": "13:00"}]},
                   {"date": "2026-12-26", "isRegularHours": True},
                   {"date": "2026-12-31", "isClosed": True}]
    assert holiday_map({"hours": {"holidayHours": out}}) == {
        "2026-12-24": "07:30-13:00", "2026-12-26": REGULAR, "2026-12-31": "closed"}


@pytest.mark.mongo
def test_write_record_and_audit_event_are_saved(mock_url, audit_db):
    httpx.post(f"{mock_url}/mock/reset", timeout=10).raise_for_status()
    with YextClient("mock", base_url=mock_url, api_key=TEST_KEY, db=audit_db) as c:
        _raw_put_holidays(mock_url, [{"date": "2026-12-25", "isClosed": True}])
        rec = c.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00", "2026-12-25": "10:00-14:00"},
                                  expected_current={"2026-12-24": None, "2026-12-25": None},
                                  decision_id="dec-abc", today=TODAY)
        skipped = c.set_holiday_hours(EID, {"2025-12-24": "closed"}, today=TODAY)

    doc = audit_db.writes.find_one({"id": rec.id}, {"_id": 0})
    assert doc and doc["target"] == "MOCK" and doc["entity_id"] == EID and doc["readback_ok"] is True
    assert doc["request"]["decision_id"] == "dec-abc"
    assert doc["request"]["applied"] == {"2026-12-24": "07:30-13:00"}
    assert audit_db.writes.find_one({"id": skipped.id})["response_status"] is None

    evs = audit.events(entity_id=EID, db=audit_db)
    by_type = {e["event_type"]: e for e in evs}
    assert by_type[EV_WRITE]["payload"]["write_id"] == rec.id
    assert by_type[EV_WRITE]["payload"]["readback_ok"] is True
    assert by_type[EV_OWNER_WINS]["payload"]["cancelled"]["2026-12-25"]["live"] == "closed"
    assert by_type[EV_SKIPPED]["payload"]["dropped_past"] == ["2025-12-24"]
    assert audit.verify_chain(audit_db).ok

    stored = json.dumps([doc, *evs], default=str)
    assert TEST_KEY not in stored and "api-key" not in stored and "api_key" not in stored


# ---------------------------------------------------------------------------
# remove_holiday_dates
# ---------------------------------------------------------------------------
def test_remove_one_date_keeps_the_others(client):
    client.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00", "2026-12-25": "closed",
                                   "2026-12-26": "regular"}, today=TODAY)
    before_week = client.get_week(EID)
    rec = client.remove_holiday_dates(EID, ["2026-12-25"], today=TODAY)
    assert rec.request["removed"] == {"2026-12-25": "closed"} and rec.readback_ok is True
    assert client.get_holiday_hours(EID) == {"2026-12-24": "07:30-13:00", "2026-12-26": REGULAR}
    assert [i["date"] for i in rec.request["body"]["hours"]["holidayHours"]] == ["2026-12-24", "2026-12-26"]
    assert client.get_week(EID) == before_week and rec.request["weekdays_unchanged"] is True


def test_remove_the_last_dates_sends_an_empty_list(client):
    client.set_holiday_hours(EID, {"2026-12-24": "closed", "2026-12-31": "closed"}, today=TODAY)
    rec = client.remove_holiday_dates(EID, ["2026-12-24", date(2026, 12, 31)], today=TODAY)
    assert rec.request["body"] == {"hours": {"holidayHours": []}}
    assert rec.readback_ok is True and client.get_holiday_hours(EID) == {}


def test_remove_owner_wins_and_missing_dates(client, mock_url):
    client.set_holiday_hours(EID, {"2026-12-24": "07:30-13:00", "2026-12-31": "closed"}, today=TODAY)
    # The owner edits 24.12 after we wrote it; undo of our value must not remove theirs.
    _raw_put_holidays(mock_url, [{"date": "2026-12-24", "isClosed": True},
                                 {"date": "2026-12-31", "isClosed": True}])
    rec = client.remove_holiday_dates(EID, ["2026-12-24", "2026-12-31", "2027-01-06"],
                                      expected_current={"2026-12-24": "07:30-13:00", "2026-12-31": "closed"},
                                      decision_id="undo-1", today=TODAY)
    assert rec.request["cancelled"] == {"2026-12-24": {"expected": "07:30-13:00", "live": "closed",
                                                       "wanted": None}}
    assert rec.request["removed"] == {"2026-12-31": "closed"}
    assert rec.request["not_present"] == ["2027-01-06"]
    assert client.get_holiday_hours(EID) == {"2026-12-24": "closed"}


def test_remove_nothing_to_remove_sends_nothing(client):
    rec = client.remove_holiday_dates(EID, ["2026-12-24"], today=TODAY)
    assert rec.response_status is None and rec.request["not_present"] == ["2026-12-24"]


# ---------------------------------------------------------------------------
# set_regular_hours (weekly drift, e.g. SE-D0016)
# ---------------------------------------------------------------------------
DRIFT = "SE-D0016"  # Restaurang Fyren: Mon-Sat 11-23, Sun 12-21


def test_regular_hours_change_one_day_and_keep_the_rest(client, mock_url):
    client.set_holiday_hours(DRIFT, {"2026-12-24": "closed"}, today=TODAY)
    before = client.get_week(DRIFT)
    rec = client.set_regular_hours(DRIFT, {"sat": "11:00-01:00", "sun": "12-21"},
                                   expected_current_week={"saturday": "11:00-23:00", "sunday": "12:00-21:00"},
                                   decision_id="drift-1")
    assert rec.request["applied"] == {"saturday": "11:00-01:00"}
    assert rec.request["unchanged"] == ["sunday"] and rec.request["cancelled"] == {}
    assert rec.readback_ok is True and rec.request["others_unchanged"] is True
    after = client.get_week(DRIFT)
    assert after == {**before, "saturday": "11:00-01:00"}
    assert client.get_holiday_hours(DRIFT) == {"2026-12-24": "closed"}   # holidayHours untouched
    # The mock wants all seven days, so the client sent the full object (holidayHours as it was).
    assert rec.request["strategy"] == "full"
    assert set(rec.request["body"]["hours"]) >= set(before) | {"holidayHours"}


def test_regular_hours_owner_wins(client, mock_url):
    ent = client.get_entity(DRIFT)
    snapshot = client.get_week(DRIFT)
    hours = dict(ent["hours"])
    hours["monday"] = {"isClosed": True}                     # the owner closes on Mondays meanwhile
    httpx.put(f"{mock_url}/v2/accounts/me/entities/{DRIFT}", params={"v": "20261106"},
              headers={"api-key": TEST_KEY}, json={"hours": hours}, timeout=10).raise_for_status()
    rec = client.set_regular_hours(DRIFT, {"monday": "10:00-22:00", "tuesday": "10:00-22:00"},
                                   expected_current_week=snapshot)
    assert rec.request["cancelled"] == {"monday": {"expected": "11:00-23:00", "live": "closed",
                                                   "wanted": "10:00-22:00"}}
    assert rec.request["applied"] == {"tuesday": "10:00-22:00"}
    assert client.get_week(DRIFT)["monday"] == "closed"


def test_regular_hours_bad_input():
    c = YextClient("mock", base_url="http://127.0.0.1:1", api_key=TEST_KEY, persist=False)
    with pytest.raises(ValueError):
        c.set_regular_hours(DRIFT, {"funday": "10:00-12:00"})
    with pytest.raises(ValueError):
        c.set_regular_hours(DRIFT, {"monday": "REGULAR"})
    with pytest.raises(ValueError):
        c.set_regular_hours(DRIFT, {})


def test_regular_hours_body_patch_vs_full():
    before = {"monday": {"openIntervals": [{"start": "09:00", "end": "17:00"}]},
              "holidayHours": [{"date": "2026-12-24", "isClosed": True}], "reopenDate": "2026-12-01"}
    patch = regular_hours_body(before, {"saturday": "closed", "monday": "10:00-16:00"}, patch=True)
    assert patch == {"hours": {"monday": {"openIntervals": [{"start": "10:00", "end": "16:00"}]},
                               "saturday": {"isClosed": True}}}            # no holidayHours key
    full = regular_hours_body(before, {"saturday": "closed"}, patch=False)["hours"]
    assert full["saturday"] == {"isClosed": True} and full["tuesday"] == {"isClosed": True}
    assert full["holidayHours"] == before["holidayHours"] and full["reopenDate"] == "2026-12-01"


# ---------------------------------------------------------------------------
# set_reopen_date
# ---------------------------------------------------------------------------
def test_reopen_date_set_and_clear(client):
    client.set_holiday_hours(EID, {"2026-12-24": "closed"}, today=TODAY)
    week = client.get_week(EID)
    rec = client.set_reopen_date(EID, "2026-12-01", decision_id="emergency-1", today=TODAY)
    assert rec.readback_ok is True and rec.request["readback"] == {"reopenDate": "2026-12-01"}
    assert client.get_entity(EID)["hours"]["reopenDate"] == "2026-12-01"
    assert client.get_week(EID) == week and client.get_holiday_hours(EID) == {"2026-12-24": "closed"}
    rec2 = client.set_reopen_date(EID, None, today=TODAY)
    assert rec2.readback_ok is True and rec2.request["previous"] == "2026-12-01"
    assert not client.get_entity(EID)["hours"].get("reopenDate")
    assert client.get_holiday_hours(EID) == {"2026-12-24": "closed"}
    # Holiday writes keep the reopen date.
    client.set_reopen_date(EID, "2026-12-01", today=TODAY)
    client.set_holiday_hours(EID, {"2026-12-31": "closed"}, today=TODAY)
    assert client.get_entity(EID)["hours"]["reopenDate"] == "2026-12-01"


def test_reopen_date_in_the_past_is_refused(client):
    with pytest.raises(ValueError):
        client.set_reopen_date(EID, "2026-11-01", today=TODAY)


def test_reopen_body_patch_vs_full():
    before = {"monday": {"isClosed": True}, "holidayHours": [], "reopenDate": "2026-12-01"}
    assert reopen_body(before, None, patch=True) == {"hours": {"reopenDate": None}}
    assert "reopenDate" not in reopen_body(before, None, patch=False)["hours"]
    assert reopen_body(before, "2027-01-01", patch=False)["hours"]["reopenDate"] == "2027-01-01"


@pytest.mark.mongo
def test_new_ops_write_records_and_audit_events(mock_url, audit_db):
    httpx.post(f"{mock_url}/mock/reset", timeout=10).raise_for_status()
    with YextClient("mock", base_url=mock_url, api_key=TEST_KEY, db=audit_db) as c:
        c.set_holiday_hours(EID, {"2026-12-24": "closed"}, today=TODAY)
        rm = c.remove_holiday_dates(EID, ["2026-12-24"], decision_id="undo-9", today=TODAY)
        reg = c.set_regular_hours(DRIFT, {"saturday": "12:00-22:00"}, decision_id="drift-9")
        reo = c.set_reopen_date(EID, "2026-12-01", today=TODAY)
        c.set_reopen_date(EID, None, today=TODAY)
    for rec in (rm, reg, reo):
        doc = audit_db.writes.find_one({"id": rec.id}, {"_id": 0})
        assert doc and doc["readback_ok"] is True
    types = {e["event_type"]: e for e in audit.events(db=audit_db)}
    assert types[EV_REMOVE]["payload"]["removed"] == {"2026-12-24": "closed"}
    assert types[EV_WRITE_REGULAR]["payload"]["applied"] == {"saturday": "12:00-22:00"}
    assert types[EV_WRITE_REGULAR]["entity_id"] == DRIFT
    reopen_events = audit.events(event_type=EV_REOPEN, db=audit_db)
    assert reo.id in {e["payload"]["write_id"] for e in reopen_events} and len(reopen_events) == 2
    assert audit.verify_chain(audit_db).ok
