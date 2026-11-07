"""cs.yext against the real Yext sandbox (entity ``YEXT_SANDBOX_ENTITY_ID``, shown as FI-D0001).

Every test restores the café's state: ``holidayHours: []``, no ``reopenDate`` and FI-D0001's
weekly hours from ``entities.json``. Opt-in only, so a plain ``pytest`` never touches the shared
sandbox:

    .venv/bin/pytest -m sandbox tests/test_yext_sandbox.py      # or CS_SANDBOX_TESTS=1
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from cs import data
from cs.config import settings
from cs.yext import YextClient, holiday_map, reopen_date_of, week_map

pytestmark = pytest.mark.sandbox

DEMO_ID = "FI-D0001"  # mapped to the sandbox entity by cs.yext.SANDBOX_MAP


def _opted_in(config) -> bool:
    if os.environ.get("CS_SANDBOX_TESTS") == "1":
        return True
    expr = config.getoption("markexpr") or ""
    return "sandbox" in expr and "not sandbox" not in expr


def _future_date(month: int = 12, day: int = 24) -> str:
    today = date.today()
    d = date(today.year, month, day)
    return (d if (d - today).days >= 14 else date(today.year + 1, month, day)).isoformat()


def _original_week() -> dict[str, str]:
    return week_map(data.entity(DEMO_ID))   # Mon-Fri 07:30-18, Sat 09-18, Sun 10-16


@pytest.fixture
def sandbox(request):
    if not _opted_in(request.config):
        pytest.skip("sandbox tests are opt-in: run with -m sandbox or CS_SANDBOX_TESTS=1")
    if not (settings.has("yext_sandbox_key") and settings.yext_sandbox_entity_id):
        pytest.skip("YEXT_SANDBOX_KEY / YEXT_SANDBOX_ENTITY_ID not set")
    with YextClient("sandbox", persist=False) as c:
        ent = c.get_entity(DEMO_ID)
        if holiday_map(ent) or reopen_date_of(ent) or week_map(ent) != _original_week():
            pytest.skip("the sandbox is not in its resting state (someone may be demoing); not touching it")
        yield c


def _assert_resting(c: YextClient) -> None:
    ent = c.get_entity(DEMO_ID)
    assert holiday_map(ent) == {}
    assert reopen_date_of(ent) is None
    assert week_map(ent) == _original_week()


def test_sandbox_write_readback_restore(sandbox):
    assert sandbox.yext_id(DEMO_ID) == settings.yext_sandbox_entity_id
    day = _future_date()
    try:
        rec = sandbox.set_holiday_hours(DEMO_ID, {day: "07:30-13:00"}, expected_current={day: None},
                                        decision_id="test-sandbox")
        assert rec.target == "SANDBOX" and rec.response_status == 200
        assert rec.readback_ok is True, rec.note
        assert rec.request["readback"] == {day: "07:30-13:00"}
        assert week_map(sandbox.get_entity(DEMO_ID)) == _original_week()
    finally:
        restore = sandbox.clear_holiday_hours(DEMO_ID)
    assert restore.readback_ok is True, restore.note
    _assert_resting(sandbox)


def test_sandbox_remove_holiday_dates(sandbox):
    d1, d2 = _future_date(12, 24), _future_date(12, 31)
    try:
        sandbox.set_holiday_hours(DEMO_ID, {d1: "07:30-13:00", d2: "closed"})
        rec = sandbox.remove_holiday_dates(DEMO_ID, [d1], expected_current={d1: "07:30-13:00"})
        assert rec.readback_ok is True, rec.note
        assert sandbox.get_holiday_hours(DEMO_ID) == {d2: "closed"}
        last = sandbox.remove_holiday_dates(DEMO_ID, [d2])
        assert last.request["body"] == {"hours": {"holidayHours": []}} and last.readback_ok is True
        assert sandbox.get_holiday_hours(DEMO_ID) == {}
    finally:
        sandbox.clear_holiday_hours(DEMO_ID)
    _assert_resting(sandbox)


def test_sandbox_regular_hours_patch_and_restore(sandbox):
    original = _original_week()
    day = _future_date()
    try:
        sandbox.set_holiday_hours(DEMO_ID, {day: "closed"})
        rec = sandbox.set_regular_hours(DEMO_ID, {"saturday": "10:00-16:00"},
                                        expected_current_week=original, decision_id="test-drift")
        # Only the changed weekday is sent; the sandbox merges hours sub-fields.
        assert rec.request["strategy"] == "patch"
        assert rec.request["body"] == {"hours": {"saturday": {"openIntervals": [{"start": "10:00",
                                                                                 "end": "16:00"}]}}}
        assert rec.readback_ok is True, rec.note
        assert rec.request["readback"] == {**original, "saturday": "10:00-16:00"}
        assert rec.request["others_unchanged"] is True
        assert sandbox.get_holiday_hours(DEMO_ID) == {day: "closed"}       # holidayHours untouched
    finally:
        try:
            restore = sandbox.set_regular_hours(DEMO_ID, original)          # back to entities.json
        finally:
            sandbox.clear_holiday_hours(DEMO_ID)
    assert restore.readback_ok is True, restore.note
    _assert_resting(sandbox)


def test_sandbox_reopen_date_set_and_clear(sandbox):
    reopen = (date.today() + timedelta(days=30)).isoformat()
    try:
        rec = sandbox.set_reopen_date(DEMO_ID, reopen, decision_id="test-emergency")
        assert rec.request["body"] == {"hours": {"reopenDate": reopen}}
        assert rec.readback_ok is True, rec.note
        assert reopen_date_of(sandbox.get_entity(DEMO_ID)) == reopen
    finally:
        cleared = sandbox.set_reopen_date(DEMO_ID, None)
    assert cleared.request["body"] == {"hours": {"reopenDate": None}}
    assert cleared.readback_ok is True, cleared.note
    _assert_resting(sandbox)
