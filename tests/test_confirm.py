"""cs.confirm: where the change shows (ordering, status texts, times) and the approve-by deadline."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from cs import confirm
from cs.config import settings


def _row(pub, status, secs, eid="X-1"):
    return {"entityId": eid, "publisherId": pub, "publisherName": pub.title(), "status": status,
            "typicalUpdateSpeedSeconds": secs, "listingUrl": f"https://example.com/{pub}"}


@pytest.fixture
def fake_listings(monkeypatch):
    def install(rows):
        idx = {}
        for r in rows:
            idx.setdefault(r["entityId"], []).append(r)
        monkeypatch.setattr(confirm, "_listings_index", lambda: {k: tuple(v) for k, v in idx.items()})
    return install


# --- where_it_shows -----------------------------------------------------------
def test_cafe_rows_fastest_first_live_before_not_live():
    rows = confirm.where_it_shows("FI-D0001", "2026-12-01T10:00:00Z")
    assert [r["publisher_id"] for r in rows][:3] == ["GOOGLEMYBUSINESS", "FONECTA_FINDER", "FONECTA_CALLER"]
    live = [r for r in rows if r["status"] == "LIVE"]
    assert [r["typical_seconds"] for r in live] == sorted(r["typical_seconds"] for r in live)
    first_not_live = next(i for i, r in enumerate(rows) if r["status"] != "LIVE")
    assert all(r["status"] != "LIVE" for r in rows[first_not_live:])
    fb = next(r for r in rows if r["publisher_id"] == "FACEBOOK")
    assert fb["status"] == "OPTED_OUT" and fb["eta_text"] == "not connected" and fb["expected_live_at"] is None
    assert rows[0]["publisher"] == "Google (Search & Maps)" and rows[0]["eta_text"] == "~2 min"


def test_status_texts_and_order(fake_listings):
    fake_listings([_row("SLOW", "LIVE", 604800), _row("WAIT", "WAITING_ON_PUBLISHER", 120),
                   _row("OPT", "OPTED_OUT", 60), _row("FAST", "LIVE", 120), _row("MID", "LIVE", 3600)])
    rows = confirm.where_it_shows("X-1", datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc))
    assert [r["publisher_id"] for r in rows] == ["FAST", "MID", "SLOW", "OPT", "WAIT"]
    assert [r["eta_text"] for r in rows] == ["~2 min", "~1 h", "~7 days", "not connected",
                                             "pending at the publisher"]


def test_expected_live_at(fake_listings):
    fake_listings([_row("FAST", "LIVE", 120), _row("WAIT", "WAITING_ON_PUBLISHER", 120)])
    at = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    rows = confirm.where_it_shows("X-1", at)
    assert rows[0]["expected_live_at"] == at + timedelta(seconds=120)
    assert rows[1]["expected_live_at"] is None
    # ISO strings with Z and naive datetimes are read as UTC.
    assert confirm.where_it_shows("X-1", "2026-12-01T10:00:00Z")[0]["expected_live_at"] == rows[0]["expected_live_at"]
    assert confirm.where_it_shows("X-1", datetime(2026, 12, 1, 10, 0))[0]["expected_live_at"] == rows[0]["expected_live_at"]


@pytest.mark.parametrize("secs,text", [(30, "~1 min"), (120, "~2 min"), (600, "~10 min"), (3600, "~1 h"),
                                       (5400, "~2 h"), (86400, "~1 day"), (172800, "~2 days"),
                                       (259200, "~3 days"), (604800, "~7 days")])
def test_eta_text(secs, text):
    assert confirm.eta_text(secs) == text


def test_every_dataset_speed_has_a_short_text():
    speeds = {r["typicalUpdateSpeedSeconds"] for rows in confirm._listings_index().values() for r in rows}
    texts = {confirm.eta_text(s) for s in speeds}
    assert texts <= {"~2 min", "~10 min", "~1 h", "~1 day", "~2 days", "~3 days", "~7 days"}


def test_sandbox_source_names_the_dataset_business():
    rows = confirm.where_it_shows("FI-D0001", target="sandbox")
    assert rows and all("FI-D0001" in r["source"] and "sandbox" in r["source"] for r in rows)
    assert "sandbox" not in confirm.where_it_shows("FI-D0001")[0]["source"]
    if settings.yext_sandbox_entity_id:
        via_sandbox_id = confirm.where_it_shows(settings.yext_sandbox_entity_id)
        assert [r["publisher_id"] for r in via_sandbox_id] == [r["publisher_id"] for r in rows]
        assert "FI-D0001" in via_sandbox_id[0]["source"]


def test_unknown_business_has_no_rows():
    assert confirm.where_it_shows("NO-SUCH-ID") == []


# --- deadlines --------------------------------------------------------------
def test_cafe_deadline_is_seven_days_before():
    # FI-D0001's slowest LIVE publishers (Foursquare, AI assistants) take ~7 days.
    assert confirm.deadline_for("FI-D0001", "2026-12-24") == date(2026, 12, 17)
    assert confirm.deadline_for("FI-D0001", date(2027, 1, 1)) == date(2026, 12, 25)


def test_deadline_ignores_publishers_that_are_not_live(fake_listings):
    fake_listings([_row("FAST", "LIVE", 120), _row("MID", "LIVE", 3600),
                   _row("OPT", "OPTED_OUT", 604800), _row("WAIT", "WAITING_ON_PUBLISHER", 604800)])
    # 24.12 00:00 minus 1 h = 23.12 23:00, rounded down to the day.
    assert confirm.deadline_for("X-1", "2026-12-24") == date(2026, 12, 23)
    per_pub = confirm.deadline_by_publisher("X-1", "2026-12-24")
    assert per_pub == {"FAST": "2026-12-23T23:58:00", "MID": "2026-12-23T23:00:00"}


@pytest.mark.parametrize("secs,want", [(172800, date(2026, 12, 22)), (259200, date(2026, 12, 21)),
                                       (86400, date(2026, 12, 23)), (90000, date(2026, 12, 22))])
def test_deadline_rounds_down_to_a_day(fake_listings, secs, want):
    fake_listings([_row("A", "LIVE", 120), _row("B", "LIVE", secs)])
    assert confirm.deadline_for("X-1", "2026-12-24") == want


def test_deadline_without_live_publishers_is_the_day_itself(fake_listings):
    fake_listings([_row("OPT", "OPTED_OUT", 604800)])
    assert confirm.deadline_for("X-1", "2026-12-24") == date(2026, 12, 24)
    assert confirm.deadline_for("NO-SUCH-ID", "2026-12-24") == date(2026, 12, 24)


def test_slowest_live():
    s = confirm.slowest_live("FI-D0001")
    assert s["status"] == "LIVE" and s["typical_seconds"] == 604800
