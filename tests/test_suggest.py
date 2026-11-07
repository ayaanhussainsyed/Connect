"""cs.suggest: the baseline reproduces Fonecta's rules; the engine adapter reads the ML parquet."""
from __future__ import annotations

import csv
from collections import Counter

import pytest

from cs import data, guardrails
from cs.domain import Evidence
from cs.hours import is_valid
from cs.suggest import baseline, engine_adapter, get_suggestions


@pytest.fixture(scope="module")
def base26():
    return baseline.suggestions(2026)


@pytest.fixture(scope="module")
def base25():
    return baseline.suggestions(2025)


def _fonecta_file(season: int) -> dict:
    p = data.dataset_path("outputs", f"baseline_predictions_{season}.csv")
    with open(p, encoding="utf-8") as f:
        return {(r["entity_id"], r["date"]): (r["action"], r["hours"]) for r in csv.DictReader(f)}


def _actions(sugs) -> dict:
    return {(s.entity_id, s.date): (s.action, s.value or "") for s in sugs if s.action != "NO_ACTION"}


def test_one_suggestion_per_target_row(base26, base25):
    for sugs, season in ((base26, 2026), (base25, 2025)):
        t = data.targets(season)
        assert len(sugs) == len(t)
        assert len({(s.entity_id, s.date) for s in sugs}) == len(t)
        assert len({s.id for s in sugs}) == len(t)
        assert all(s.model_version == baseline.MODEL_VERSION and s.season == season for s in sugs)


def test_ids_are_stable():
    a = baseline.suggestions(2026, ["FI-D0001"])
    b = baseline.suggestions(2026, ["FI-D0001"])
    assert [s.id for s in a] == [s.id for s in b]
    assert a[0].id == f"sug:baseline-v0:HOLIDAY_HOURS:FI-D0001:{a[0].date}"


def test_backtest_identical_to_fonecta_baseline(base25):
    """In 2025 our display equals Baseline.displayed on every row, so the outputs must be identical."""
    assert _actions(base25) == _fonecta_file(2025)


def test_2026_differs_from_fonecta_only_on_closed_for_good_listings(base26):
    ours, theirs = _actions(base26), _fonecta_file(2026)
    diff = {k for k in set(ours) | set(theirs) if ours.get(k) != theirs.get(k)}
    assert diff, "expected the display fix to change some rows"
    assert all(data.entity(e).get("closed") for e, _ in diff)


def test_suggest_rows_carry_evidence(base26):
    for s in base26:
        if s.action == "SUGGEST":
            assert is_valid(s.value) and s.value != s.shown
            assert s.basis in ("OWN_LAST_YEAR", "MALL", "PEERS")
            assert s.evidence and isinstance(s.evidence[0], Evidence) and s.evidence[0].source == s.basis
            assert s.evidence[0].ref and s.evidence[0].detail
        elif s.action == "FLAG":
            assert s.basis == "CLOSED_SIGNALS" and s.value is None
            assert s.evidence and {e.source for e in s.evidence} <= {"REVIEW", "USER_EDIT"}
    assert Counter(s.basis for s in base26 if s.action == "SUGGEST").keys() == {"OWN_LAST_YEAR", "MALL", "PEERS"}


def test_regular_hours_entry_last_year_means_this_years_regular(base26):
    """'isRegularHours' last season -> the candidate is this year's regular hours (so usually no change)."""
    from cs import display

    rows = [s for s in base26 if s.basis == "OWN_LAST_YEAR" and "regular hours" in s.evidence[0].detail]
    assert rows
    for s in rows:
        regular = display.explain_shown(s.entity_id, s.date, data.SNAPSHOT[2026])["regular"]["hours"]
        if s.action == "SUGGEST":            # only when this year's shown hours differ from regular
            assert s.value == regular != s.shown
        else:
            assert s.shown == regular


def test_demo_storylines(base26):
    by_entity = lambda eid: [s for s in base26 if s.entity_id == eid]  # noqa: E731
    ctx = guardrails.make_context(data.SNAPSHOT[2026])
    d11 = [guardrails.apply_guardrails(s, ctx) for s in by_entity("FI-D0011")]
    assert d11 and all(s.action == "FLAG" for s in d11) and not any(s.message_ok for s in d11)
    for eid in ("FI-D0013", "NO-D0015"):
        rows = [guardrails.apply_guardrails(s, ctx) for s in by_entity(eid)]
        assert rows and not any(s.action == "SUGGEST" for s in rows), eid


def test_get_suggestions_dispatch():
    a = get_suggestions(2026, "baseline", ["FI-D0001"])
    assert a and all(s.model_version == "baseline-v0" for s in a)
    with pytest.raises(ValueError):
        get_suggestions(2026, "nope", ["FI-D0001"])


# --- engine adapter ---------------------------------------------------------------
needs_parquet = pytest.mark.skipif(not engine_adapter.available(2026), reason="engine_2026.parquet not present")


@needs_parquet
def test_engine_adapter_covers_targets_and_converts_fields():
    sugs = engine_adapter.suggestions(2026)
    assert len(sugs) == len(data.targets(2026))
    assert all(s.model_version == engine_adapter.MODEL_VERSION for s in sugs)
    sug_rows = [s for s in sugs if s.action == "SUGGEST"]
    assert sug_rows and all(is_valid(s.value) for s in sug_rows)
    assert all(s.value is None for s in sugs if s.action != "SUGGEST")
    with_ev = [s for s in sugs if s.evidence]
    assert with_ev and all(isinstance(e, Evidence) for s in with_ev for e in s.evidence)
    assert any(s.candidates for s in sugs)
    assert all(s.probability is None or 0.0 <= s.probability <= 1.0 for s in sugs)
    assert {s.action for s in sugs} <= {"SUGGEST", "NO_ACTION", "FLAG"}


@needs_parquet
def test_engine_demo_storylines():
    ctx = guardrails.make_context(data.SNAPSHOT[2026])
    rows = [guardrails.apply_guardrails(s, ctx) for s in engine_adapter.suggestions(2026, ["FI-D0011", "FI-D0013", "NO-D0015"])]
    assert any(s.action == "FLAG" for s in rows if s.entity_id == "FI-D0011")
    assert not any(s.action == "SUGGEST" for s in rows if s.entity_id in ("FI-D0013", "NO-D0015"))


def test_engine_adapter_falls_back_to_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_adapter, "parquet_path", lambda season=2026: tmp_path / "missing.parquet")
    sugs = engine_adapter.suggestions(2026, ["FI-D0001"])
    assert sugs and all(s.model_version == baseline.MODEL_VERSION for s in sugs)
