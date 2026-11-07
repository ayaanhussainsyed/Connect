"""cs.hours: the simulator's primitives (copied) plus our normalise / is_valid / Yext helpers."""
from __future__ import annotations

from datetime import date

import pytest

from cs import data
from cs import hours as H

CANONICAL = [
    "closed",
    "09:00-17:00",
    "10:00-14:00,17:00-22:00",
    "18:00-02:00",      # past midnight
    "18:00-00:00",      # closes at midnight
    "00:00-23:59",      # 24 hours (Yext convention)
    "22:00-02:00,10:00-14:00",
]


def test_copied_part_is_verbatim():
    src = data.settings.dataset_dir / "mock_api" / "hours.py"
    if not src.exists():
        pytest.skip("dataset not present")
    ours = (data.settings.root / "cs" / "hours.py").read_text(encoding="utf-8")
    assert "Connect Suggests simulator, Since AI ry 2026, MIT" in ours
    assert src.read_text(encoding="utf-8") in ours


@pytest.mark.parametrize("s", CANONICAL)
def test_round_trip_yext_day(s):
    assert H.is_valid(s)
    assert H.from_yext_day(H.to_yext_day(s)) == s
    item = H.to_yext_day(s, date(2026, 12, 24))
    assert item["date"] == "2026-12-24"
    assert H.from_yext_day(item) == s


def test_yext_day_shapes():
    assert H.to_yext_day("closed") == {"isClosed": True}
    assert H.to_yext_day("00:00-23:59") == {"openIntervals": [{"start": "00:00", "end": "23:59"}]}
    assert H.to_yext_day("18:00-02:00", "2026-12-31") == {
        "date": "2026-12-31", "openIntervals": [{"start": "18:00", "end": "02:00"}]}
    assert H.to_yext_day(H.REGULAR, "2026-12-24") == {"date": "2026-12-24", "isRegularHours": True}
    assert H.from_yext_day({"date": "2026-12-24", "isRegularHours": True}) == H.REGULAR
    assert H.from_yext_day(None) == "closed"
    assert H.from_yext_day({"openIntervals": []}) == "closed"


def test_to_yext_day_rejects_bad_input():
    with pytest.raises(ValueError):
        H.to_yext_day("9-17")
    with pytest.raises(ValueError):
        H.to_yext_day("09:00-17:00", "2026-13-01")


@pytest.mark.parametrize("s", [
    "", " ", None, 17, "Closed", " 09:00-17:00", "09:00-17:00 ", "9:00-17:00", "09:00 - 17:00",
    "09:00-09:00",                       # start == end
    "24:00-02:00", "09:60-10:00", "25:00-26:00",
    "09:00-14:00,13:00-18:00",           # overlap
    "18:00-02:00,20:00-23:00",           # overlap past midnight
    "09:00-17:00,", "open", "REGULAR",
])
def test_is_valid_rejects(s):
    assert not H.is_valid(s)


def test_past_midnight_intervals():
    assert H.str_to_ivs("18:00-02:00") == [(18 * 60, 26 * 60)]
    assert H.str_to_ivs("00:00-23:59") == [(0, 1439)]
    assert H.boundary_diff("18:00-02:00", "18:00-03:00") == 60
    assert H.boundary_diff("closed", "closed") == 0
    assert H.boundary_diff("closed", "09:00-17:00") == 9999
    assert H.same_state("closed", "closed") and not H.same_state("closed", "09:00-17:00")


@pytest.mark.parametrize("raw, want", [
    ("09:00-17:00", "09:00-17:00"),
    (" 9-17 ", "09:00-17:00"),
    ("9.30–13", "09:30-13:00"),
    ("10-14; 17-22", "10:00-14:00,17:00-22:00"),
    ("17:00-22:00,10:00-14:00", "10:00-14:00,17:00-22:00"),
    ("0-24", "00:00-23:59"),
    ("00:00-23:59", "00:00-23:59"),
    ("18-24", "18:00-00:00"),
    ("22-2", "22:00-02:00"),
    ("CLOSED", "closed"),
    ("Suljettu", "closed"),
    ("stängt", "closed"),
    ("stengt", "closed"),
])
def test_normalise(raw, want):
    assert H.normalise(raw) == want
    assert H.is_valid(want)


@pytest.mark.parametrize("raw", ["", "open", "9", "9-", "-17", "9-17-18", "25-26", "9:75-10", "24-02",
                                 "9-9", "9-14,13-18", None])
def test_normalise_rejects(raw):
    with pytest.raises(ValueError):
        H.normalise(raw)


def test_week_helpers():
    ent = data.entity("FI-D0001")
    week = H.week_from_yext(ent["hours"])
    assert len(week) == 7 and all(H.is_valid(h) for h in week)
    back = H.week_strs_to_yext(week)
    assert H.week_from_yext(back) == week
    assert H.weekday_key(date(2026, 12, 24)) == "thursday"


def test_every_hours_string_in_the_dataset_is_valid_and_round_trips():
    n = 0
    for r in data._holiday_rows():
        if r["hours"] != H.REGULAR:
            assert H.is_valid(r["hours"]), r
            assert H.from_yext_day(H.to_yext_day(r["hours"])) == r["hours"]
            n += 1
    for versions in data._regular_index().values():
        for v in versions:
            for h in H.week_from_yext(v["hours"]):
                assert H.is_valid(h), v["entity_id"]
                n += 1
    for season in (2026, 2025):
        for col in ("truth_hours", "displayed_hours_at_snapshot"):
            bad = [h for h in data.labels(season)[col] if not H.is_valid(h)]
            assert not bad, (season, col, bad[:5])
    assert n > 70_000
