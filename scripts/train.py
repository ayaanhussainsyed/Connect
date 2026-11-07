#!/usr/bin/env python
"""Train the score engine (M1-M5) and write predictions.

    .venv/bin/python scripts/train.py                 # CV on dev 2026, final fit, predictions for 2026
    .venv/bin/python scripts/train.py --backtest      # also the honest 2025 back-test
    .venv/bin/python scripts/train.py --refresh       # recompute the cross-validated frames

What it does:
1. Business-grouped 5-fold CV on dev 2026 (M1, M2, M3 re-fitted per fold on the other businesses, seasons
   before 2026 only), then cross-fitted calibration and λ (A4). This is the honest dev number.
2. Final fit on all dev businesses -> predictions for every 2026 target row (test, demo).
   In the output files the **dev rows hold the cross-validated decisions**, so running evaluate.py on
   outputs/engine_predictions_2026.csv reproduces the honest dev score; test and demo rows come from the
   final model.
3. With --backtest: the same for the 2025 season, using only data stamped before 6 Nov 2025.

Outputs:
    artifacts/models/engine.joblib, artifacts/models/habits.joblib
    artifacts/predictions/engine_2026.parquet (+ engine_2025.parquet)
    outputs/engine_predictions_2026.csv (+ _2025) in the scorer's format, and train_report.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cs.engine import api, data as D, pipeline as P  # noqa: E402
from cs.engine.decide import Calibrator, expected_points, score_rows  # noqa: E402
from cs.engine.flags import signals  # noqa: E402

CACHE = D.CACHE


def evaluate(csv_path: Path, season: int) -> dict:
    out = subprocess.run([sys.executable, str(D.DS / "evaluation" / "evaluate.py"), "--predictions", str(csv_path),
                          "--labels", str(D.labels_path(season)), "--json"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def cv(season: int, refresh: bool, **kw):
    fr, fc = CACHE / f"oof_r_{season}.parquet", CACHE / f"oof_c_{season}.parquet"
    if fr.exists() and fc.exists() and not refresh:
        return pd.read_parquet(fr), pd.read_parquet(fc)
    r, c = P.run_cv(season, P.TRAIN_PERIODS[season], **kw)
    CACHE.mkdir(parents=True, exist_ok=True)
    r.to_parquet(fr)
    c.to_parquet(fc)
    return r, c


def crossfit_cep(r, c, lab):
    """Calibrated candidate frame with EPs, cross-fitted by fold (for explanations of the CV rows)."""
    y = (c.hours.values == lab.truth_hours.values[c.rid.values]).astype(int)
    parts = []
    for k in sorted(r.fold.unique()):
        tr = r.fold.values != k
        cal = Calibrator().fit(c[tr[c.rid.values]], y[tr[c.rid.values]])
        cc, _ = cal.transform(c[~tr[c.rid.values]], len(r))
        parts.append(cc)
    cc = pd.concat(parts).sort_values("rid")
    po = np.clip(1 - np.bincount(cc.rid.values, weights=cc.p.values, minlength=len(r)), 0, 1)
    return expected_points(r, cc, po, p_cfg=r.p_cfg.values)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="recompute the cross-validated frames")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--soft-flag", action="store_true", help="allow FLAG on uncertain rows (+0.05 points)")
    a = ap.parse_args()
    t0 = time.time()
    seasons = [2026] + ([2025] if a.backtest else [])
    rep_path = ROOT / "artifacts" / "predictions" / "train_report.json"
    report = json.loads(rep_path.read_text()) if rep_path.exists() else {}
    for season in seasons:
        lab = D.labels(season)
        r, c = cv(season, a.refresh)
        dec, info = P.crossfit_decide(r, c, lab, allow_soft_flag=a.soft_flag)
        s = score_rows(dec, lab)
        print(f"[{season}] CV score {s['score']:.2f} harmful {s['harmful']} lam {info.lam.tolist()} "
              f"({time.time() - t0:.0f}s)", flush=True)
        cv_df = api.explain(r, crossfit_cep(r, c, lab), dec,
                            cfg_sig=signals(D.SNAP[season], entity_ids=pd.unique(r.entity_id)))
        if season == 2026:
            y = (c.hours.values == lab.truth_hours.values[c.rid.values]).astype(int)
            cal = Calibrator().fit(c, y)
            lam = tuple(float(np.median([l_[i] for l_ in info.lam])) for i in (0, 1))
            fit = P.final_fit(P.TRAIN_PERIODS["final2026"], 2026)
            api.MODELS.mkdir(parents=True, exist_ok=True)
            joblib.dump({"fit": fit, "calibrator": cal, "lam": lam, "allow_soft_flag": a.soft_flag,
                         "other_near": 0.25, "train_periods": P.TRAIN_PERIODS["final2026"],
                         "cv_score": s["score"], "cv_harmful": s["harmful"]}, api.ENGINE_PATH)
            from cs.engine.features import make_context
            hm = fit.hm
            hm.attach_evidence(make_context(D.SNAP[2026]).entries)
            joblib.dump(hm, api.HABITS_PATH)
            api.load_engine.cache_clear()
            api.habit_model.cache_clear()
            full = api.run(2026)
            key = full.entity_id + "|" + full.date
            cv_keys = set(cv_df.entity_id + "|" + cv_df.date)
            df = pd.concat([full[~key.isin(cv_keys).values], cv_df], ignore_index=True)
            order = {k: i for i, k in enumerate(key)}
            df = df.iloc[np.argsort((df.entity_id + "|" + df.date).map(order).values)].reset_index(drop=True)
        else:
            df = cv_df.reset_index(drop=True)
        api.write_predictions(df, season)
        out_csv = ROOT / "outputs" / f"engine_predictions_{season}.csv"
        n = api.to_scorer_csv(df, out_csv)
        res = evaluate(out_csv, season)
        report[str(season)] = {"cv_score": s["score"], "harmful": s["harmful"], "lams": info.lam.tolist(),
                               "evaluate_py": res}
        print(f"[{season}] wrote {len(df)} rows ({n} actions) -> {out_csv.relative_to(ROOT)}; evaluate.py: "
              f"score {res['score_0_100']} harmful {res['harmful_suggestions']} precision {res['precision_exact']} "
              f"recall {res['recall_exact']} ({time.time() - t0:.0f}s)", flush=True)
        rep_path.write_text(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
