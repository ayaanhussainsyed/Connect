#!/usr/bin/env python
"""Honest back-test of the score engine for one season (business-grouped CV, data before the snapshot only).

    .venv/bin/python scripts/backtest.py --season 2025          # uses cached frames if present
    .venv/bin/python scripts/backtest.py --season 2025 --refresh

Writes outputs/engine_predictions_<season>.csv and prints Fonecta's evaluate.py result next to the baseline's.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from cs.engine import api, data as D, pipeline as P  # noqa: E402
from cs.engine.flags import signals  # noqa: E402
from train import crossfit_cep, cv, evaluate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025, choices=[2025, 2026])
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    lab = D.labels(a.season)
    r, c = cv(a.season, a.refresh)
    dec, info = P.crossfit_decide(r, c, lab)
    df = api.explain(r, crossfit_cep(r, c, lab), dec, cfg_sig=signals(D.SNAP[a.season], entity_ids=pd.unique(r.entity_id)))
    out = ROOT / "outputs" / f"engine_predictions_{a.season}_cv.csv"
    api.to_scorer_csv(df, out)
    ours = evaluate(out, a.season)
    base = evaluate(D.DS / "outputs" / f"baseline_predictions_{a.season}.csv", a.season)
    for name, res in (("baseline", base), ("engine", ours)):
        print(f"{name:9s} score {res['score_0_100']:6.2f}  harmful {res['harmful_suggestions']:4d}  "
              f"precision {res['precision_exact']:.3f}  recall {res['recall_exact']:.3f}  "
              f"views protected {res['search_views_protected_pct']}%")
    print("λ per fold:", info.lam.tolist())


if __name__ == "__main__":
    main()
