#!/usr/bin/env python
"""B1 Season Autopilot: coverage curve and error rate of automatic changes (dev businesses).

    .venv/bin/python scripts/eval_autopilot.py

The simulation, step by step:
1. **2025 card.** Every dev business's 2025 season card is approved; an owner who approves fixes anything wrong,
   so the approved value of each 2025 date is its true 2025 hours (dev_labels_2025_backtest.truth_hours).
   These become learned facts: (business, holiday, hours, season 2025, source=owner approval).
2. **Standing permission.** A share g of owners grants "Approve + every year" (sampled per business, 50 seeds).
   g = 8% (the 2025 pilot: 57 of 761 owners), 12% (the owner simulator's fixed rate), 30% and 50% (hypotheses).
   **These grant rates are assumptions;** the simulator cannot show whether our button design changes them.
3. **2026 autopilot.** For each granting business and each 2026 date of a holiday it approved (same day key; for
   in-between days the nearest date; events and school breaks are excluded, as the permission covers "the same
   holidays"), the newest learned fact is applied (weight 0.95 per season of age; facts older than 3 seasons
   would drop out). It is applied at least 14 days ahead; dates closer than that are applied at once with a
   one-tap undo (counted below). Nothing is applied if the fact equals what is already shown.
4. **Scoring against the dev 2026 labels:** the share of 2026 at-risk lookups (expected_search_views on rows that
   need a change) fixed with **zero owner replies** (exact or within 30 min, the scorer's "protected"), and the error
   rate of the automatic changes (exact / near / same state, other hours / wrong open-closed / change not needed).
5. **Reference:** plain per-date approval of the engine's 2026 card (cross-validated decisions) when each owner
   replies with the pilot's 47% reply rate, and the two combined (autopilot for granting owners, the card for the rest).

Output: artifacts/cache/autopilot_eval.json and autopilot_curve.csv. The simulator's hidden owner profiles
(mock_api/sim_data.json) are not read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cs.engine import data as D  # noqa: E402
from cs.engine.features import _cycle_index  # noqa: E402

GRANT_RATES = (0.08, 0.12, 0.30, 0.50)
REPLY_RATE = 0.47
N_SEEDS = 50
DECAY, MAX_AGE, LEAD_DAYS = 0.95, 3, 14
GATE = 0.5


def learned_value(facts: pd.DataFrame, season: int):
    """Newest fact wins; weight decays 0.95 per extra season; facts older than MAX_AGE seasons drop out."""
    f = facts[(season - facts.season) <= MAX_AGE]
    if f.empty:
        return None, 0.0
    newest = f.sort_values("season").iloc[-1]
    return newest.hours, DECAY ** max(0, season - newest.season - 1)


def main():
    l25, l26 = D.labels(2025), D.labels(2026)
    # learned facts from the approved 2025 card
    facts = l25[~l25.day_key.isin(["EVENT", "SCHOOL_BREAK"])][["entity_id", "date", "day_key", "truth_hours"]].copy()
    facts = facts.rename(columns={"truth_hours": "hours"})
    facts["season"] = 2025
    facts["cyc"], facts["idx"] = _cycle_index(facts["date"])
    rows = l26.copy()
    rows["cyc"], rows["idx"] = _cycle_index(rows["date"])
    rows["rid"] = np.arange(len(rows))
    m = rows[["rid", "entity_id", "day_key", "idx"]].merge(facts[["entity_id", "day_key", "idx", "hours", "season"]],
                                                         on=["entity_id", "day_key"], suffixes=("", "_f"))
    m["dist"] = (m.idx - m.idx_f).abs()
    m = m.sort_values(["rid", "dist"]).drop_duplicates("rid")
    auto_val = pd.Series(index=rows.rid, dtype=object)
    for rid, g in m.groupby("rid"):
        auto_val[rid], _ = learned_value(g, 2026)
    rows["auto"] = auto_val.reindex(rows.rid).values
    shown, truth, exp = rows.displayed_hours_at_snapshot.values, rows.truth_hours.values, rows.expected_action.values
    views = rows.expected_search_views.fillna(0).values
    applies = pd.notna(rows.auto.values) & (rows.auto.values != shown)
    days_ahead = (pd.to_datetime(rows.date) - pd.Timestamp(D.SNAP[2026])).dt.days.values

    def outcome(a, t, s, e):
        if e == "NO_ACTION":
            return "not_needed_wrong_state" if (a == "closed") != (s == "closed") else "not_needed_other_hours"
        if e == "FLAG":
            return "closed_for_good"
        if a == t:
            return "exact"
        if (a == "closed") != (t == "closed"):
            return "wrong_open_closed"
        return "near" if D.near(a, t) else "same_state_other_hours"
    oc = np.array([outcome(a, t, s, e) if ap else "" for a, t, s, e, ap in zip(rows.auto.values, truth, shown, exp, applies)],
                  dtype=object)
    at_risk = exp == "SUGGEST"
    fixed = applies & np.isin(oc, ["exact", "near"])
    total_risk = views[at_risk].sum()

    # engine's 2026 card for the per-date reference (dev rows hold the cross-validated decisions)
    eng = pd.read_parquet(D.ARTIFACTS / "predictions" / "engine_2026.parquet")
    eng = rows[["entity_id", "date"]].merge(eng[["entity_id", "date", "action", "hours"]], on=["entity_id", "date"], how="left")
    on_card = eng.action.isin(["SUGGEST", "FLAG"]).values

    # gated autopilot: apply the learned fact only where the 2026 engine (cross-validated) gives it P >= GATE
    engc = pd.read_parquet(D.ARTIFACTS / "predictions" / "engine_2026.parquet", columns=["entity_id", "date", "candidates"])
    engc = rows[["entity_id", "date"]].merge(engc, on=["entity_id", "date"], how="left")
    p_fact = np.array([max([c["prob"] for c in (cands if cands is not None else []) if c["hours"] == a] or [0.0])
                       if isinstance(a, str) else 0.0 for a, cands in zip(rows.auto.values, engc.candidates.values)])
    gated = applies & (p_fact >= GATE)

    ents = np.array(sorted(rows.entity_id.unique()))
    pos = {e: i for i, e in enumerate(ents)}
    ent_idx = rows.entity_id.map(pos).values
    curve = []
    for g in GRANT_RATES:
        res = []
        for seed in range(N_SEEDS):
            rng = np.random.default_rng(seed)
            grant = rng.random(len(ents)) < g
            reply = rng.random(len(ents)) < REPLY_RATE
            gr = grant[ent_idx]
            auto_rows = gr & applies
            covered_auto = (gr & fixed & at_risk)
            covered_gated = (gr & gated & fixed & at_risk)
            card_cov = (~gr) & reply[ent_idx] & on_card & at_risk
            card_only = reply[ent_idx] & on_card & at_risk
            res.append({
                "auto_changes": int(auto_rows.sum()),
                "auto_coverage": views[covered_auto].sum() / total_risk,
                "auto_coverage_of_granting": views[covered_auto].sum() / max(1e-9, views[gr & at_risk].sum()),
                "card_only_coverage": views[card_only].sum() / total_risk,
                "combined_coverage": (views[covered_auto].sum() + views[card_cov].sum()) / total_risk,
                "auto_errors_wrong_state": int((auto_rows & np.isin(oc, ["wrong_open_closed", "not_needed_wrong_state"])).sum()),
                "auto_exact": int((auto_rows & (oc == "exact")).sum()),
                "auto_within_14_days": int((auto_rows & (days_ahead < LEAD_DAYS)).sum()),
                "gated_changes": int((gr & gated).sum()),
                "gated_coverage": views[covered_gated].sum() / total_risk,
                "gated_errors_wrong_state": int((gr & gated & np.isin(oc, ["wrong_open_closed", "not_needed_wrong_state"])).sum()),
                "gated_combined_coverage": (views[covered_gated].sum() + views[(~(gr & gated)) & reply[ent_idx] & on_card & at_risk].sum()) / total_risk,
            })
        r = pd.DataFrame(res)
        curve.append({"grant_rate": g, **{f"{k}_mean": float(r[k].mean()) for k in r.columns},
                      **{f"{k}_p05": float(r[k].quantile(0.05)) for k in ("auto_coverage", "combined_coverage")},
                      **{f"{k}_p95": float(r[k].quantile(0.95)) for k in ("auto_coverage", "combined_coverage")}})
    curve = pd.DataFrame(curve)
    # error rate of automatic changes over all dev businesses (independent of who grants): every applicable change
    a = oc[applies]
    err = {k: float((a == k).mean()) for k in ["exact", "near", "same_state_other_hours", "wrong_open_closed",
                                                 "not_needed_wrong_state", "not_needed_other_hours", "closed_for_good"]}
    err["n_changes"] = int(applies.sum())
    err["wrong_open_closed_total"] = err["wrong_open_closed"] + err["not_needed_wrong_state"]
    ag = oc[gated]
    err_g = {k: float((ag == k).mean()) for k in ["exact", "near", "same_state_other_hours", "wrong_open_closed",
                                                    "not_needed_wrong_state", "not_needed_other_hours", "closed_for_good"]}
    err_g["n_changes"] = int(gated.sum())
    err_g["wrong_open_closed_total"] = err_g["wrong_open_closed"] + err_g["not_needed_wrong_state"]
    out = {"total_at_risk_views": float(total_risk), "reply_rate_assumed": REPLY_RATE, "grant_rates_are_assumptions": True,
           "error_rate_of_automatic_changes": err, "error_rate_of_gated_changes": err_g, "gate": GATE,
           "share_of_at_risk_rows_with_a_2025_fact": float((at_risk & applies).sum() / at_risk.sum()),
           "engine_card_reference": {"precision_exact_of_engine_suggestions": None},
           "curve": curve.to_dict("records")}
    D.CACHE.mkdir(parents=True, exist_ok=True)
    curve.to_csv(D.CACHE / "autopilot_curve.csv", index=False)
    (D.CACHE / "autopilot_eval.json").write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps({"ungated": err, "gated": err_g}, indent=1))
    print(curve[["grant_rate", "auto_changes_mean", "auto_coverage_mean", "auto_coverage_p05", "auto_coverage_p95",
                 "card_only_coverage_mean", "combined_coverage_mean", "auto_errors_wrong_state_mean",
                 "auto_within_14_days_mean", "gated_coverage_mean", "gated_errors_wrong_state_mean",
                 "gated_combined_coverage_mean"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
