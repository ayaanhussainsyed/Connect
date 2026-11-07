"""Quick tests for the score engine (run: .venv/bin/pytest tests/engine -q)."""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cs.engine import data as D  # noqa: E402
from cs.engine import decide as DC  # noqa: E402
from cs.engine import features as F  # noqa: E402
from cs.engine.habits import HabitModel, truth_observations  # noqa: E402


def _evaluate_module():
    spec = importlib.util.spec_from_file_location("fonecta_evaluate", D.DS / "evaluation" / "evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- 1. the points table
SYNTH = [
    # expected, truth, shown, action, hours
    ("SUGGEST", "closed", "10:00-18:00", "SUGGEST", "closed"),              # exact +1
    ("SUGGEST", "10:00-14:00", "10:00-18:00", "SUGGEST", "10:00-14:00"),    # exact +1
    ("SUGGEST", "10:00-14:00", "10:00-18:00", "SUGGEST", "10:30-14:30"),    # near +0.6
    ("SUGGEST", "10:00-14:00", "10:00-18:00", "SUGGEST", "09:00-16:00"),    # same state +0.2
    ("SUGGEST", "10:00-14:00", "10:00-18:00", "SUGGEST", "closed"),         # wrong state -1
    ("SUGGEST", "closed", "10:00-18:00", "SUGGEST", "10:00-14:00"),         # wrong state -1
    ("SUGGEST", "closed", "10:00-18:00", "FLAG", ""),                       # +0.05
    ("SUGGEST", "closed", "10:00-18:00", "NO_ACTION", ""),                  # 0
    ("SUGGEST", "18:00-02:00", "18:00-23:00", "SUGGEST", "18:00-02:30"),    # past midnight, near +0.6
    ("NO_ACTION", "10:00-18:00", "10:00-18:00", "SUGGEST", "10:00-18:00"),  # redundant -0.1
    ("NO_ACTION", "10:00-18:00", "10:00-18:00", "SUGGEST", "10:00-16:00"),  # spam -0.5
    ("NO_ACTION", "10:00-18:00", "10:00-18:00", "SUGGEST", "closed"),       # harmful -1
    ("NO_ACTION", "closed", "closed", "SUGGEST", "10:00-16:00"),            # harmful -1
    ("NO_ACTION", "10:00-18:00", "10:00-18:00", "FLAG", ""),                # -0.1
    ("NO_ACTION", "10:00-18:00", "10:00-18:00", "NO_ACTION", ""),           # 0
    ("FLAG", "closed", "10:00-18:00", "FLAG", ""),                          # +1
    ("FLAG", "closed", "10:00-18:00", "SUGGEST", "closed"),                 # +0.3
    ("FLAG", "closed", "10:00-18:00", "SUGGEST", "10:00-12:00"),            # -1
    ("FLAG", "closed", "10:00-18:00", "NO_ACTION", ""),                     # 0
]


def test_points_match_official_scorer(tmp_path):
    ev = _evaluate_module()
    lab_p, pred_p = tmp_path / "labels.csv", tmp_path / "pred.csv"
    with open(lab_p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "date", "day_key", "event_id", "truth_hours", "displayed_hours_at_snapshot",
                    "needs_action", "expected_action", "mismatch_type", "expected_search_views"])
        for i, (exp, truth, shown, _, _) in enumerate(SYNTH):
            mm = "" if exp == "NO_ACTION" else "DIFFERENT_HOURS"
            w.writerow([f"X-{i:03d}", "2026-12-24", "XMAS_EVE", "", truth, shown, int(exp != "NO_ACTION"), exp, mm, 1])
    with open(pred_p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "date", "action", "hours", "confidence"])
        for i, (_, _, _, act, h) in enumerate(SYNTH):
            w.writerow([f"X-{i:03d}", "2026-12-24", act, h, 0.5])
    res = ev.score(ev.load_predictions(str(pred_p)), str(lab_p))
    ours = sum(DC.points(a, h, t, s, e) for (e, t, s, a, h) in SYNTH)
    mx = sum(1 for (e, *_ ) in SYNTH if e != "NO_ACTION")
    assert res["score_0_100"] == pytest.approx(round(max(0.0, 100 * ours / mx), 2))
    # and row by row, each case on its own
    for i, (e, t, s, a, h) in enumerate(SYNTH):
        one = {(f"X-{i:03d}", "2026-12-24"): (a, ev.norm(h), None)}
        sub = tmp_path / f"l{i}.csv"
        rows = list(csv.reader(open(lab_p)))
        with open(sub, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(rows[0])
            w.writerow(rows[i + 1])
        r1 = ev.score(one, str(sub))
        if e != "NO_ACTION":
            assert r1["score_0_100"] == pytest.approx(max(0.0, 100 * DC.points(a, h, t, s, e)))


def test_expected_points_reduces_to_points_when_certain():
    """With all probability on one truth, EP of suggesting h equals the scorer's points for h."""
    shown = "10:00-18:00"
    cands = ["closed", "10:00-18:00", "10:00-14:00", "10:30-14:30", "09:00-16:00"]
    for truth in cands:
        r = pd.DataFrame({"rid": [0], "shown": [shown], "reg": [shown], "reg_closed": [0.0], "cur_state": [-1]})
        c = pd.DataFrame({"rid": 0, "hours": cands})
        c["is_closed"] = (c.hours == "closed").astype(float)
        oc = np.array([D.open_close(h) for h in c.hours])
        c["c_open"], c["c_close"] = oc[:, 0], oc[:, 1]
        c["p"] = (c.hours == truth).astype(float)
        cep = DC.expected_points(r, c, np.array([0.0]))
        exp = "NO_ACTION" if truth == shown else "SUGGEST"
        for _, row in cep.iterrows():
            assert row.ep == pytest.approx(DC.points("SUGGEST", row.hours, truth, shown, exp)), (truth, row.hours)


# ----------------------------------------------------------------------------- 2. no leakage
def test_context_uses_only_entries_before_as_of():
    as_of = D.SNAP[2025]
    ctx = F.make_context(as_of)
    assert (ctx.entries.entered_at < as_of).all()
    assert len(ctx.entries) > 0


def test_features_identical_when_future_data_is_removed(monkeypatch):
    """Build features for the 2025 back-test with the full files, then with every row stamped on/after
    the snapshot deleted from the history, reviews and user edits: the frames must be identical."""
    as_of = D.SNAP[2025]
    lab = D.labels(2025)
    rows = lab[lab.entity_id.isin(sorted(lab.entity_id.unique())[:60])].reset_index(drop=True)
    r1, c1 = F.build(rows, F.make_context(as_of))

    hh, rv, ue = D.holiday_history(), D.reviews(), D.user_edits()
    monkeypatch.setattr(D, "holiday_history", lambda: hh[hh.entered_at < as_of].reset_index(drop=True))
    monkeypatch.setattr(D, "reviews", lambda: rv[rv.ts < pd.Timestamp(as_of)].reset_index(drop=True))
    monkeypatch.setattr(D, "user_edits", lambda: ue[ue.created < as_of].reset_index(drop=True))
    r2, c2 = F.build(rows, F.make_context(as_of))
    pd.testing.assert_frame_equal(r1.reset_index(drop=True), r2.reset_index(drop=True), check_dtype=False)
    pd.testing.assert_frame_equal(c1.reset_index(drop=True), c2.reset_index(drop=True), check_dtype=False)


def test_peer_stats_do_not_use_the_pooled_file():
    import inspect
    src = inspect.getsource(F)
    assert "peer_benchmarks" not in src


# ----------------------------------------------------------------------------- 3. M1
@pytest.fixture(scope="module")
def small_habit_model():
    th = D.truth_history()
    ids = sorted(th.entity_id.unique())[:200]
    th = th[th.entity_id.isin(ids) & (th.date < D.SNAP[2026])]
    hm = HabitModel(K=4).fit(truth_observations(th), n_init=1)
    hm.attach_evidence(F.make_context(D.SNAP[2026]).entries)
    return hm


def test_posterior_sums_to_one(small_habit_model):
    hm = small_habit_model
    for eid in ["FI-D0001", "FI-00002", "FI-D0017"]:
        for known in ({}, {"XMAS_EVE": "closed"}, {"2026-12-24": "special", "NYE": "regular"}):
            p = hm.posterior(eid, known)
            assert p.shape == (hm.K,)
            assert p.sum() == pytest.approx(1.0)
            assert (p >= 0).all()
            dp = hm.day_probs(eid, "XMAS_DAY", known)
            assert set(dp) == {"closed", "regular", "special"}
            assert sum(dp.values()) == pytest.approx(1.0)


def test_newer_answer_supersedes(small_habit_model):
    hm = small_habit_model
    p_closed = hm.day_probs("FI-D0001", "XMAS_EVE", {"XMAS_EVE": "closed"})["closed"]
    p_open = hm.day_probs("FI-D0001", "XMAS_EVE", {"XMAS_EVE": "special"})["closed"]
    assert p_closed > p_open


def test_older_facts_weigh_less_and_share_one_unit(small_habit_model):
    """B5: several seasons of the same holiday count as one fact in total, the newest weighing most."""
    hm = small_habit_model
    obs = pd.DataFrame({"entity_id": ["X", "X", "X"], "day_key": ["XMAS_EVE", "XMAS_EVE", "NYE"],
                        "date": ["2024-12-24", "2025-12-24", "2025-12-31"], "state": ["closed", "special", "closed"]})
    ids, C = hm.counts(obs)
    j = hm.key_idx["XMAS_EVE"]
    assert C[0, j].sum() == pytest.approx(1.0)
    assert C[0, j, 2] > C[0, j, 0]          # 2025 'special' outweighs 2024 'closed'
    assert C[0, j, 0] / C[0, j, 2] == pytest.approx(hm.decay)


# ----------------------------------------------------------------------------- 4. decisions and the API
def test_decline_rule_never_resuggests_rejected_hours():
    r = pd.DataFrame({"rid": [0], "shown": ["10:00-18:00"], "reg": ["10:00-18:00"], "reg_closed": [0.0],
                      "cur_state": [-1], "rejected_hours": ["closed"], "rejected_inferred": [False]})
    c = pd.DataFrame({"rid": 0, "hours": ["closed", "10:00-18:00", "10:00-14:00"], "c_own1": [1.0, 0.0, 0.0]})
    c["is_closed"] = (c.hours == "closed").astype(float)
    oc = np.array([D.open_close(h) for h in c.hours])
    c["c_open"], c["c_close"] = oc[:, 0], oc[:, 1]
    c["p"] = [0.9, 0.05, 0.05]
    cep = DC.expected_points(r, c, np.array([0.0]))
    d = DC.decide(r, cep, np.array([0.0]), lam=0.0, allow_soft_flag=False)
    assert d.action.iloc[0] != "SUGGEST" or d.hours.iloc[0] != "closed"
    d2 = DC.decide(r.drop(columns=["rejected_hours"]), cep, np.array([0.0]), lam=0.0, allow_soft_flag=False)
    assert d2.action.iloc[0] == "SUGGEST" and d2.hours.iloc[0] == "closed"


def test_owner_entry_is_never_contradicted():
    r = pd.DataFrame({"rid": [0], "shown": ["10:00-14:00"], "reg": ["10:00-18:00"], "reg_closed": [0.0],
                      "cur_state": [2]})
    c = pd.DataFrame({"rid": 0, "hours": ["closed", "10:00-14:00"], "c_own1": [1.0, 0.0]})
    c["is_closed"] = (c.hours == "closed").astype(float)
    oc = np.array([D.open_close(h) for h in c.hours])
    c["c_open"], c["c_close"] = oc[:, 0], oc[:, 1]
    c["p"] = [0.99, 0.01]
    d = DC.decide(r, DC.expected_points(r, c, np.array([0.0])), np.array([0.0]), lam=0.0, allow_soft_flag=False)
    assert d.action.iloc[0] == "NO_ACTION"


@pytest.mark.skipif(not (D.ARTIFACTS / "predictions" / "engine_2026.parquet").exists(), reason="run scripts/train.py")
def test_predict_contract():
    from cs.engine.api import OUT_COLS, predict
    df = predict(2026)
    assert list(df.columns) == OUT_COLS
    assert len(df) == len(D.targets(2026))
    assert set(df.action) <= {"SUGGEST", "NO_ACTION", "FLAG"}
    sug = df[df.action == "SUGGEST"]
    assert sug.hours.map(lambda h: isinstance(h, str) and D.ivs(h) is not None).all()
    assert df[df.action != "SUGGEST"].hours.isna().all()
    assert df.probability.between(0, 1).all()
    assert isinstance(df.evidence.iloc[0], list) and isinstance(df.candidates.iloc[0], list)


# ----------------------------------------------------------------------------- 5. M6 drift
def test_drift_review_parser_four_languages():
    from cs.engine.drift import parse_week_statement
    en = parse_week_statement("Now only open Mon–Fri 11:00–18:00, Sat 11:00–16:00.")
    assert en["monday"] == "11:00-18:00" and en["saturday"] == "11:00-16:00" and en["sunday"] == "closed"
    fi = parse_week_statement("Nykyään auki vain ma–pe 12–14 ja 18–22, la 14–22, päivittäkää sivut.")
    assert fi["friday"] == "12:00-14:00,18:00-22:00" and fi["saturday"] == "14:00-22:00" and fi["sunday"] == "closed"
    sv = parse_week_statement("Numera öppet bara mån–tor 10–23, fre–lör 10–4.")
    assert sv["friday"] == "10:00-04:00" and sv["sunday"] == "closed"
    nb = parse_week_statement("Nå bare åpent man–fre 7.30–18, lør 9–17.")
    assert nb["monday"] == "07:30-18:00" and nb["saturday"] == "09:00-17:00"
    assert parse_week_statement("Great coffee, friendly staff.") is None
