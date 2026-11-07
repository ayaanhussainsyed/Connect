#!/usr/bin/env python
"""Ablations, no-history and learning evaluations (business-grouped CV on dev 2026).

    .venv/bin/python scripts/eval_ablations.py                 # all variants, 3 in parallel
    .venv/bin/python scripts/eval_ablations.py --only no_m1 no_peers

Each variant re-trains M1-M3 on the training folds without one signal group and re-runs the cross-fitted
calibration and λ choice, so every number is honest. Results: artifacts/cache/ablations.json; frames:
artifacts/cache/abl_<variant>_{r,c}.parquet (the notebook reads these).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXED_LAM = (0.2, 0.6)

VARIANTS = {
    "full": dict(),
    "winter_only": dict(train="winter"),
    "no_m1": dict(ablation="no_m1"),
    "no_linked": dict(ablation="no_linked"),
    "no_m1_linked": dict(ablation="no_m1_linked"),
    "no_other_holidays": dict(ablation="no_other_holidays"),
    "no_own2": dict(ablation="no_own2"),
    "no_own_history": dict(ablation="no_own_history"),
    "no_peers": dict(ablation="no_peers"),
    "no_mall": dict(ablation="no_mall"),
    "no_edits_reviews": dict(ablation="no_edits_reviews"),
    "no_events": dict(ablation="no_events"),
    "no_history_masked": dict(mask_history=True),
    "no_pilot_feedback": dict(exclude_sources=("CONNECT_SUGGESTION",)),
}


def run_variant(name: str) -> dict:
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    import numpy as np
    from cs.engine import data as D, pipeline as P
    from cs.engine.decide import score_rows
    spec = VARIANTS[name]
    t0 = time.time()
    seasons = (2024, 2025) if spec.get("train") == "winter" else P.TRAIN_PERIODS[2026]
    r, c = P.run_cv(2026, seasons, ablation=spec.get("ablation"), mask_history=spec.get("mask_history", False),
                    exclude_sources=spec.get("exclude_sources", ()), verbose=False)
    r.to_parquet(D.CACHE / f"abl_{name}_r.parquet")
    c.to_parquet(D.CACHE / f"abl_{name}_c.parquet")
    lab = D.labels(2026)
    dec, info = P.crossfit_decide(r, c, lab)
    s = score_rows(dec, lab)
    dec[["rid", "action", "hours"]].to_parquet(D.CACHE / f"abl_{name}_dec.parquet")
    # less noisy comparisons: the same fixed flip margins for every variant, and M2's log-loss
    from sklearn.metrics import log_loss, roc_auc_score
    from cs.engine.state_model import state_labels
    dfix, _ = P.crossfit_decide(r, c, lab, fixed_lam=FIXED_LAM)
    sf = score_rows(dfix, lab)
    ys = state_labels(r, lab.truth_hours.values)
    P2 = np.clip(r[["p_closed", "p_regular", "p_special"]].values, 1e-6, 1)
    return {"variant": name, "score": s["score"], "harmful": s["harmful"], "precision_exact": s["precision_exact"],
            "recall_exact": s["recall_exact"], "suggestions": s["suggestions"], "lams": info.lam.tolist(),
            "score_fixed_lam": sf["score"], "harmful_fixed_lam": sf["harmful"],
            "m2_logloss": float(log_loss(ys, P2, labels=[0, 1, 2])), "m2_auc_closed": float(roc_auc_score(ys == 0, P2[:, 0])),
            "seconds": round(time.time() - t0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    names = a.only or list(VARIANTS)
    out_path = ROOT / "artifacts" / "cache" / "ablations.json"
    res = json.loads(out_path.read_text()) if out_path.exists() else {}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(run_variant, names):
            res[r["variant"]] = r
            print(json.dumps(r), flush=True)
            out_path.write_text(json.dumps(res, indent=2, default=float))


if __name__ == "__main__":
    main()
