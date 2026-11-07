"""A1 – cs.display must reproduce Fonecta's displayed hours exactly.

- 100% of dev_labels_2026 (as_of 2026-11-06) and dev_labels_2025_backtest (as_of 2025-11-06).
- dev_truth_history ``displayed_hours_that_day`` (as_of = the day itself): every row matches except
  rows of the five ``closed: true`` dev businesses after they closed, because the date the closed
  flag was published is not in the public data (the flag is applied from the entities snapshot on).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from cs import data, display
from cs.hours import REGULAR, week_from_yext


@pytest.mark.parametrize("season, n_rows", [(2026, 11_100), (2025, 11_118)])
def test_matches_dev_labels_100_percent(season, n_rows):
    lab = data.labels(season)
    as_of = data.SNAPSHOT[season]
    assert len(lab) == n_rows
    mismatches = []
    for eid, d, want in zip(lab["entity_id"], lab["date"], lab["displayed_hours_at_snapshot"]):
        got = display.shown_hours(eid, d, as_of)
        if got != want:
            mismatches.append((eid, d, got, want))
    assert mismatches == [], f"{len(mismatches)} mismatches, first: {mismatches[:5]}"


def test_matches_displayed_that_day_in_truth_history():
    th = data._dev_truth_history_all()
    closed_for_good = {eid for eid, e in data.entities().items() if e.get("closed")}
    mismatches = []
    for eid, d, want in zip(th["entity_id"], th["date"], th["displayed_hours_that_day"]):
        got = display.shown_hours(eid, d, as_of=d)
        if got != want:
            mismatches.append((eid, d, got, want))
    assert len(th) > 58_000
    # Only closed-for-good businesses after their (unpublished) closing date may differ.
    unexplained = [m for m in mismatches if m[0] not in closed_for_good or m[3] != "closed"]
    assert unexplained == []
    assert len(mismatches) <= 33


def test_regular_hours_rule_reproduces_entities_json_for_every_entity():
    as_of = data.DEFAULT_AS_OF.isoformat()
    diff = [eid for eid, e in data.entities().items()
            if display.resolve_regular(eid, as_of).week != tuple(week_from_yext(e["hours"]))]
    assert diff == []


def test_entities_json_holiday_hours_are_the_latest_entries():
    as_of = data.DEFAULT_AS_OF
    n = 0
    for eid, e in data.entities().items():
        for item in (e.get("hours") or {}).get("holidayHours") or []:
            entries = data.holiday_entries(eid, item["date"], as_of)
            assert entries and entries[-1]["holidayHours"] == item, (eid, item)
            ex = display.explain_shown(eid, item["date"], as_of)
            if not ex["closed_for_good"] and not item.get("isRegularHours"):
                assert ex["source"] == display.HOLIDAY_ENTRY and ex["entry"]["holidayHours"] == item
            n += 1
    assert n > 200


def test_seasonal_reopening_restores_pre_closure_hours():
    ex = display.explain_shown("FI-00330", "2026-12-21", "2026-11-06")
    assert ex["shown"] == "10:00-17:00"
    assert ex["source"] == display.REGULAR_HOURS
    r = ex["regular"]
    assert r["version_effective_from"] == "2026-11-01"
    assert r["listed_hours"] == "10:00-18:00"
    assert r["seasonal_reopening"] and r["closure_from"] == "2026-05-01"
    assert r["restored_from"] == "2025-04-16"
    # Same in the back-test, and during the summer closure everything is closed.
    assert display.shown_hours("FI-00330", "2025-12-22", "2025-11-06") == "10:00-17:00"
    assert display.shown_hours("FI-00330", "2025-06-02", "2025-06-02") == "closed"


def test_version_effective_on_the_snapshot_day_is_shown():
    ex = display.explain_shown("FI-00848", "2025-11-28", "2025-11-06")
    assert ex["regular"]["version_effective_from"] == "2025-11-06"
    assert ex["shown"] == "11:00-13:00"
    # The day before, the older version was in effect.
    assert display.shown_hours("FI-00848", "2025-11-28", "2025-11-05") == "11:00-14:00"


def test_closed_for_good_only_from_the_entities_snapshot():
    ex = display.explain_shown("FI-01794", "2026-12-24", "2026-11-06")
    assert ex["shown"] == "closed" and ex["source"] == display.CLOSED_FOR_GOOD
    ex25 = display.explain_shown("FI-01794", "2025-12-23", "2025-11-06")
    assert ex25["source"] == display.REGULAR_HOURS and ex25["shown"] == "10:00-19:00"


def test_temporarily_closed_with_reopen_date():
    ex = display.explain_shown("FI-00026", "2026-12-24", "2026-11-06")
    assert ex["reopen_date"] == "2027-05-01"
    assert ex["shown"] == "closed" and ex["source"] == display.TEMPORARILY_CLOSED


def _first_label_row(season, pred):
    as_of = data.SNAPSHOT[season]
    for r in data.labels(season).to_dict("records"):
        entries = data.holiday_entries(r["entity_id"], r["date"], as_of)
        if pred(r, entries):
            return r, entries
    pytest.fail("no example row found")


def test_is_regular_hours_entry_falls_back_to_regular():
    r, entries = _first_label_row(2026, lambda r, e: e and e[-1]["hours"] == REGULAR
                                  and not data.entity(r["entity_id"]).get("closed"))
    ex = display.explain_shown(r["entity_id"], r["date"], data.SNAPSHOT[2026])
    assert ex["source"] == display.REGULAR_HOURS
    assert ex["entry"]["is_regular_hours"] is True
    assert ex["shown"] == ex["regular"]["hours"] == r["displayed_hours_at_snapshot"]


def test_latest_entry_wins():
    """Data dictionary: several rows for one date = later row wins. Only 9 dates in the dataset have
    two rows (all test split, same value twice), so labels cannot show it; check it directly."""
    # FI-00154 2025-12-26: OWNER_UI on 12-17, CONNECT_SUGGESTION on 12-20.
    mid = display.explain_shown("FI-00154", "2025-12-26", "2025-12-18")
    assert mid["entry"]["source"] == "OWNER_UI" and mid["earlier_entries"] == 0
    assert mid["entries_after_as_of"] == 1
    late = display.explain_shown("FI-00154", "2025-12-26", "2025-12-21")
    assert late["entry"]["source"] == "CONNECT_SUGGESTION" and late["earlier_entries"] == 1
    assert late["entry"]["entered_at"] == "2025-12-20T16:33:00" and late["shown"] == "closed"
    assert "replaced 1 earlier entry" in late["summary"]


def test_entries_on_the_snapshot_day_are_not_visible():
    """12 entries were made on 2025-11-06; at the 2025 snapshot they must not count."""
    rows = [r for r in data._holiday_rows() if r["entered_at"].startswith("2025-11-06")]
    assert rows
    for r in rows:
        ex = display.explain_shown(r["entity_id"], r["date"], "2025-11-06")
        assert ex["entries_after_as_of"] >= 1
        assert ex["entry"] is None or ex["entry"]["entered_at"] < "2025-11-06"
        # One day later the entry is visible (unless overridden by a later one or closure).
        later = display.explain_shown(r["entity_id"], r["date"], "2025-11-07")
        assert later["entry"] is not None and later["entry"]["entered_at"] >= r["entered_at"]


def test_no_future_entry_leaks_into_an_earlier_snapshot():
    r = next(x for x in data._holiday_rows() if x["entity_id"] == "FI-D0001" and x["hours"] != REGULAR)
    entered = date.fromisoformat(r["entered_at"][:10])
    before = display.explain_shown("FI-D0001", r["date"], entered)
    after = display.explain_shown("FI-D0001", r["date"], entered + timedelta(days=1))
    assert before["entry"] is None or before["entry"]["entered_at"] < r["entered_at"]
    assert after["entry"]["entered_at"] >= r["entered_at"]


def test_unknown_entity_and_bad_date():
    with pytest.raises(KeyError):
        display.shown_hours("XX-NOPE", "2026-12-24")
    with pytest.raises(ValueError):
        display.shown_hours("FI-D0001", "2026-02-30")


def test_explanation_is_json_ready():
    import json

    ex = display.explain_shown("FI-D0001", "2026-12-24")
    json.dumps(ex)
    assert ex["shown"] == "07:30-18:00" and ex["weekday"] == "thursday"
    assert set(ex) >= {"shown", "source", "summary", "regular", "entry", "as_of", "date"}


def test_label_for_only_at_label_snapshots():
    assert display.label_for("FI-00330", "2026-12-21", "2026-11-06")["displayed_hours_at_snapshot"] == "10:00-17:00"
    assert display.label_for("FI-00330", "2026-12-21", "2026-11-05") is None
    assert display.label_for("FI-D0001", "2026-12-24", "2026-11-06") is None  # demo, not dev


def test_shown_table_covers_every_target_row():
    df = display.shown_table(2026)
    assert len(df) == len(data.targets(2026)) == 41_755
    assert df["shown"].notna().all() and (df["shown"] != "").all()
    assert set(df["shown_source"]) <= {display.REGULAR_HOURS, display.HOLIDAY_ENTRY,
                                       display.CLOSED_FOR_GOOD, display.TEMPORARILY_CLOSED}


def test_cli(capsys):
    assert display.main(["FI-00330", "2026-12-21"]) == 0
    out = capsys.readouterr().out
    assert "Google shows: 10:00-17:00" in out and "✓" in out
    assert display.main(["NOPE", "2026-12-21"]) == 2
