"""cs.data: loaders work and history accessors never show anything stamped at/after as_of."""
from __future__ import annotations

from datetime import date, datetime

from cs import data

AS_OFS = [date(2025, 11, 6), date(2026, 11, 6)]


def test_entities_and_index():
    ents = data.entities()
    assert len(ents) == 3032
    idx = data.entity_index()
    assert len(idx) == 3032 and idx["entity_id"].is_unique
    aurora = idx.set_index("entity_id").loc["FI-D0001"]
    assert aurora["name"] == "Kahvila Aurora" and aurora["split"] == "demo"
    assert data.entity_snapshot_at() == datetime(2026, 11, 5, 21, 0)
    assert data.entity_state_known("2026-11-06") and not data.entity_state_known("2025-11-06")


def test_season_and_cutoffs():
    assert data.season_of("2026-11-06") == 2026 and data.season_of("2025-11-06") == 2025
    assert data.season_of("2026-01-05") == 2025
    assert "2025-11-06T00:00:01" > data.cutoff_str("2025-11-06") > "2025-11-05T23:59:59"


def test_holiday_history_respects_as_of():
    for as_of in AS_OFS:
        df = data.holiday_history(as_of)
        assert len(df) > 0 and (df["entered_at"] < as_of.isoformat()).all()
    assert len(data.holiday_history("2025-11-06")) < len(data.holiday_history("2026-11-06"))


def test_regular_versions_in_effect():
    v = data.regular_versions("FI-00848", "2025-11-06")
    assert [x["effective_from"] for x in v] == ["before-2024", "2025-11-06"]
    v = data.regular_versions("FI-00848", "2025-11-05")
    assert [x["effective_from"] for x in v] == ["before-2024"]
    for as_of in AS_OFS:
        df = data.regular_history(as_of)
        eff = df.loc[df["effective_from"] != data.BEFORE_2024, "effective_from"]
        assert (eff <= as_of.isoformat()).all()


def test_signals_respect_as_of():
    for as_of in AS_OFS:
        cut_ms = data.cutoff_epoch_ms(as_of)
        revs = data.reviews(as_of)
        assert revs and all(r["publisherDate"] < cut_ms for r in revs)
        assert all(c["publisherDate"] < cut_ms for r in revs for c in r["comments"] if "publisherDate" in c)
        ps = data.publisher_suggestions(as_of)
        assert ps and all(s["dateCreated"] < as_of.isoformat() for s in ps)
        assert all(not s.get("dateResolved") or s["dateResolved"] < as_of.isoformat() for s in ps)
        th = data.dev_truth_history(as_of)
        assert (th["date"] < as_of.isoformat()).all()
    assert len(data.pilot_log("2025-11-06")) == 0           # the pilot ran after that snapshot
    assert len(data.pilot_log("2026-11-06")) == 3031


def test_weather_forecast_only_when_published():
    w25 = data.weather_daily("2025-11-06")
    assert (w25["is_forecast"] == 0).all() and (w25["date"] < "2025-11-06").all()
    w26 = data.weather_daily("2026-11-06")
    assert (w26["is_forecast"] == 1).any()
    assert (w26.loc[w26["is_forecast"] == 0, "date"] < "2026-11-06").all()


def test_targets_and_labels():
    assert len(data.targets(2026)) == 41_755
    assert len(data.targets(2025)) == 11_118
    lab = data.labels(2026)
    assert len(lab) == 11_100 and set(lab["expected_action"]) <= {"SUGGEST", "NO_ACTION", "FLAG"}
    assert data.label_index(2026)[("FI-00330", "2026-12-21")]["truth_hours"] == "10:00-18:00"


def test_static_lookups_load():
    assert len(data.calendar()) > 0 and len(data.contacts()) > 0 and len(data.listings()) > 0
    assert len(data.peers()) > 0 and len(data.cities()) > 0 and len(data.categories()) > 0
    assert len(data.school_breaks()) > 0 and len(data.local_events()) > 0 and len(data.weather_normals()) > 0
    assert len(data.dev_regular_truth()) == 802
