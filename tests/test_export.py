"""cs.export: the CSV scores with the official evaluate.py; our points() mirror it exactly."""
from __future__ import annotations

import csv

import pytest

from cs import data, export
from cs.suggest import engine_adapter

FONECTA_BASELINE_2026 = 54.49
FONECTA_BASELINE_2025 = 53.23


@pytest.fixture(scope="module")
def baseline_2026(tmp_path_factory):
    sugs = export.build(2026, "baseline")
    path = tmp_path_factory.mktemp("export") / "predictions_baseline_2026.csv"
    n = export.write_csv(sugs, path)
    return sugs, path, n


def _read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_baseline_export_scores_at_least_fonecta(baseline_2026):
    sugs, path, n = baseline_2026
    res = export.score_csv(path, 2026)
    print(f"\nbaseline 2026 dev score {res['score_0_100']} harmful {res['harmful_suggestions']}")
    assert res["score_0_100"] >= FONECTA_BASELINE_2026
    assert res["harmful_suggestions"] <= 224
    assert res["ignored_prediction_rows_not_in_labels"] > 0   # test/demo rows are kept in the file


def test_csv_has_every_suggest_and_flag_row(baseline_2026):
    sugs, path, n = baseline_2026
    rows = _read(path)
    assert list(rows[0].keys()) == list(export.CSV_COLUMNS)
    want = {(s.entity_id, s.date) for s in sugs if s.action in ("SUGGEST", "FLAG")}
    assert len(rows) == n == len(want) and {(r["entity_id"], r["date"]) for r in rows} == want
    # Opt-outs only stop messages; their rows stay in the CSV.
    opted = [s for s in sugs if s.action == "SUGGEST" and not s.message_ok]
    assert opted and all((s.entity_id, s.date) in want for s in opted)
    assert all(r["hours"] == "" for r in rows if r["action"] == "FLAG")


def test_points_mirror_the_official_scorer(baseline_2026):
    sugs, path, _ = baseline_2026
    assert export.score_in_process(sugs, 2026) == export.score_csv(path, 2026)["score_0_100"]


def test_backtest_2025_scores_at_least_fonecta(tmp_path):
    sugs = export.build(2025, "baseline")
    path = tmp_path / "bt.csv"
    export.write_csv(sugs, path)
    assert export.score_csv(path, 2025)["score_0_100"] >= FONECTA_BASELINE_2025


def test_write_csv_rejects_duplicates(tmp_path):
    sugs = export.build(2026, "baseline", entity_ids=["FI-D0011"])
    with pytest.raises(ValueError):
        export.write_csv(sugs + sugs[:1], tmp_path / "dup.csv")


@pytest.mark.skipif(not engine_adapter.available(2026), reason="engine_2026.parquet not present")
def test_ml_export_scores_and_passes_format_checks(tmp_path):
    sugs = export.build(2026, "ml")
    path = tmp_path / "predictions_ml_2026.csv"
    export.write_csv(sugs, path)
    res = export.score_csv(path, 2026)          # raises if evaluate.py rejects the format
    print(f"\nml 2026 dev score {res['score_0_100']} harmful {res['harmful_suggestions']}")
    assert 0 <= res["score_0_100"] <= 100 and res["rows"] == 11_100
    assert export.score_in_process(sugs, 2026) == res["score_0_100"]


@pytest.mark.mongo
def test_save_to_mongo_upserts_and_skips_unchanged(mongo_test_db):
    mongo_test_db.suggestions.delete_many({})   # other tests in this session may have saved some
    sugs = export.build(2026, "baseline", entity_ids=["FI-D0001", "FI-D0011"])
    first = export.save_to_mongo(sugs, db=mongo_test_db)
    assert first["written"] == len(sugs) and first["upserted"] == len(sugs)
    again = export.save_to_mongo(export.build(2026, "baseline", entity_ids=["FI-D0001", "FI-D0011"]),
                                 db=mongo_test_db)
    assert again["written"] == 0 and again["unchanged"] == len(sugs)
    sugs[0].guardrail_notes.append("test: changed")
    third = export.save_to_mongo(sugs, db=mongo_test_db)
    assert third["written"] == 1 and third["modified"] == 1
    coll = mongo_test_db.suggestions
    assert coll.count_documents({"model_version": "baseline-v0"}) == len(sugs)
    doc = coll.find_one({"entity_id": "FI-D0011"}, {"_id": 0})
    assert doc["action"] == "FLAG" and doc["content_hash"]


@pytest.mark.mongo
def test_run_writes_csv_saves_and_audits(mongo_test_db, tmp_path):
    from cs import audit

    res = export.run(2026, "baseline", out=tmp_path / "p.csv", db=mongo_test_db,
                     entity_ids=["FI-D0001", "FI-D0011", "FI-00002"])
    assert res.csv_rows > 0 and res.saved["total"] == res.rows and res.score["rows"] == 11_100
    assert audit.verify_chain(db=mongo_test_db).ok
    assert audit.events(event_type="EXPORT", db=mongo_test_db)[0]["payload"]["engine"] == "baseline"
