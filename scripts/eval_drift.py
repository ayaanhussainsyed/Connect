#!/usr/bin/env python
"""M6 drift detector: evaluate on the dev labels and write predictions for every business.

    .venv/bin/python scripts/eval_drift.py

1. Features at the 2026 snapshot (cs/engine/drift.py), dev labels `listing_is_outdated` (22 of 802).
2. 5-fold stratified CV over dev businesses (one row per business, so the folds are grouped by business):
   out-of-fold P(outdated), precision / recall at the 0.5 threshold, and whether the proposed week equals
   `true_regular_hours_now` on the businesses we would message.
3. Final model on all dev businesses -> artifacts/predictions/drift_2026.parquet for all 3,017 businesses
   (dev rows carry their out-of-fold probability, so dev numbers stay honest).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.model_selection import StratifiedKFold  # noqa: E402

from cs.engine import data as D, drift as DR  # noqa: E402

THRESHOLD = 0.5


def main():
    as_of = D.SNAP[2026]
    f, proposed, evidence = DR.signals(as_of)
    rt = D.regular_truth().set_index("entity_id")
    dev = [e for e in rt.index if e in f.index]
    X, y = f.loc[dev, DR.FEATURES].values, rt.loc[dev, "listing_is_outdated"].values.astype(int)
    tw = DR.true_weeks()

    oof = np.zeros(len(dev))
    for seed in range(5):  # repeat the CV with 5 seeds and average: 22 positives make one split noisy
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        for tr, te in skf.split(X, y):
            m = DR.make_model().fit(X[tr], y[tr])
            oof[te] += m.predict_proba(X[te])[:, 1] / 5
    final = DR.make_model().fit(X, y)
    p_all = pd.Series(final.predict_proba(f[DR.FEATURES].values)[:, 1], index=f.index)
    p_all.loc[dev] = oof

    pub = DR.published_weeks(as_of)
    differs = pd.Series({e: proposed[e] != pub[e] for e in f.index})
    action = np.where((p_all.values >= THRESHOLD) & differs.reindex(p_all.index).values, "SUGGEST", "NO_ACTION")
    out = pd.DataFrame({"entity_id": f.index, "p_outdated": p_all.values,
                        "proposed_week": [proposed[e] for e in f.index], "published_week": [pub[e] for e in f.index],
                        "evidence": [evidence[e] for e in f.index], "action": action})
    out["basis"] = np.where(f["drift_reviews"].values > 0, "REVIEW", np.where(f["edits_differing"].values > 0, "USER_EDITS", "NONE"))
    out = out.merge(D.entities()[["entity_id", "split"]], on="entity_id", how="left")
    (ROOT / "artifacts" / "predictions").mkdir(parents=True, exist_ok=True)
    out[["entity_id", "p_outdated", "proposed_week", "evidence", "action", "basis", "published_week", "split"]].to_parquet(
        ROOT / "artifacts" / "predictions" / "drift_2026.parquet", index=False)

    # ---- evaluation on dev
    d = out.set_index("entity_id").loc[dev]
    pred = d.action.values == "SUGGEST"
    tp = int((pred & (y == 1)).sum())
    exact = sum(1 for e, pr, yy in zip(dev, pred, y) if pr and yy and proposed[e] == tw[e])
    days_right = days_tot = 0
    for e, pr, yy in zip(dev, pred, y):
        if pr and yy:
            for dd in D.DAYS:
                if pub[e][dd] != tw[e][dd] or proposed[e][dd] != pub[e][dd]:
                    days_tot += 1
                    days_right += proposed[e][dd] == tw[e][dd]
    rev_only = (f.loc[dev, "drift_reviews"].values > 0)
    res = {
        "dev_businesses": len(dev), "outdated": int(y.sum()), "suggested": int(pred.sum()), "true_positives": tp,
        "precision": tp / max(1, pred.sum()), "recall": tp / max(1, y.sum()),
        "proposed_week_exact_on_true_positives": exact, "changed_days_right": days_right, "changed_days_total": days_tot,
        "review_rule_only": {"suggested": int(rev_only.sum()), "true_positives": int((rev_only & (y == 1)).sum())},
        "auc": float(__import__("sklearn.metrics", fromlist=["roc_auc_score"]).roc_auc_score(y, oof)),
        "coefficients": dict(zip(DR.FEATURES, np.round(final[-1].coef_[0], 3).tolist())),
        "all_businesses_suggested": int((out.action == "SUGGEST").sum()),
        "SE-D0016": out.set_index("entity_id").loc["SE-D0016", ["p_outdated", "action"]].to_dict(),
    }
    curve = []
    for th in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        pr = (oof >= th) & differs.reindex(dev).values
        curve.append({"threshold": th, "suggested": int(pr.sum()), "tp": int((pr & (y == 1)).sum()),
                      "precision": float((pr & (y == 1)).sum() / max(1, pr.sum())), "recall": float((pr & (y == 1)).sum() / y.sum())})
    res["threshold_curve"] = curve
    eo = (f.loc[dev, "drift_reviews"].values == 0) & (f.loc[dev, "edits_differing"].values > 0)
    res["edit_only"] = {"businesses": int(eo.sum()), "outdated_among_them": int((eo & (y == 1)).sum()),
                        "max_p_outdated": float(oof[eo].max()) if eo.any() else 0.0,
                        "no_signal_outdated": int(((f.loc[dev, "drift_reviews"].values == 0) & (f.loc[dev, "edits_differing"].values == 0) & (y == 1)).sum())}
    misses = [e for e, pr, yy in zip(dev, pred, y) if yy and not pr]
    res["missed"] = {e: {"p": round(float(d.loc[e, "p_outdated"]), 3), "drift_reviews": float(f.loc[e, "drift_reviews"]),
                         "edits": float(f.loc[e, "edits_differing"])} for e in misses}
    (D.CACHE / "drift_eval.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=2, default=float))


if __name__ == "__main__":
    main()
