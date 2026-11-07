"""Live calendar (bonus live source). Offline: uses the `holidays` package only."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("holidays")

from cs.live import calendar as LC  # noqa: E402


def test_rules_for_known_dates():
    assert LC._easter(2026) == date(2026, 4, 5)
    assert LC.rule_days("FI", 2026)["BLACK_FRIDAY"] == [date(2026, 11, 27)]
    assert LC.rule_days("SE", 2026)["FATHERS_DAY"] == [date(2026, 11, 8)]
    assert LC.rule_days("FI", 2026)["MOTHERS_DAY"] == [date(2026, 5, 10)]
    assert LC.rule_days("SE", 2026)["MOTHERS_DAY"] == [date(2026, 5, 31)]
    assert LC.rule_days("NO", 2026)["MOTHERS_DAY"] == [date(2026, 2, 8)]
    assert LC.rule_days("SE", 2025)["INTERDAY"] == [date(2025, 12, 27), date(2025, 12, 29), date(2025, 12, 30)]
    assert "LUCIA" in LC.rule_days("SE", 2026) and "LUCIA" not in LC.rule_days("FI", 2026)


def test_live_calendar_matches_dataset_calendar():
    summ, diffs = LC.agreement("2024-01-01", "2027-01-06", source="package")
    assert summ.missing.sum() == 0 and summ.extra.sum() == 0, diffs
    assert (summ.official_flag_agrees == 1.0).all()


def test_live_calendar_rebuilds_the_2026_targets():
    rep = LC.targets_agreement(2026, source="package")
    assert rep["missing_from_live"] == 0 and rep["extra_in_live"] == 0


def test_official_flags_follow_each_country():
    fi = LC.special_days("FI", "2026-12-20", "2026-12-31", source="package").set_index("day_key")
    se = LC.special_days("SE", "2026-12-20", "2026-12-31", source="package").set_index("day_key")
    assert fi.loc["XMAS_EVE", "official_public_holiday"] == 1      # FI counts Christmas Eve
    assert se.loc["XMAS_EVE", "official_public_holiday"] == 0      # SE does not
    assert set(fi.loc["INTERDAY", "date"]) == {"2026-12-28", "2026-12-29", "2026-12-30"}
