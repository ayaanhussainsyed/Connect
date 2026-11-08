#!/usr/bin/env python
"""Oracle-owner test for B1's one question (milestone d).

For a sample of dev businesses with at least one row needing a change:
- "no question": the engine's decisions for the business's season card, scored with the official table;
- "one question": ask the date with the highest question_value(), let an oracle owner answer with the true state
  (closed / regular / special, from the dev labels), re-decide every other date, and score again. The asked date
  itself scores +1 if it needed a change (the owner supplies the hours).
- "random question": the same with a random date, as a control.
- "off-card question": what the app asks (``cs.season._question``): the best date that is NOT already a
  suggestion on the card.

    .venv/bin/python scripts/eval_question.py --n 150

Caveat (stated in the notebook): the final engine was trained on the dev businesses' earlier seasons, so absolute
points are optimistic; the *difference* between the arms is what this measures.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cs.engine import data as D  # noqa: E402
from cs.engine.decide import points  # noqa: E402
from cs.engine.features import STATES, state_of  # noqa: E402
from cs.engine.question import _decisions, question_value  # noqa: E402


def card_points(eid, known, asked_date, lab_e, only=None):
    """Points of the card; ``only`` restricts the sum to one date (None = all dates)."""
    r, dec = _decisions(eid, 2026, known)
    tot = 0.0
    for (_, row), (_, d) in zip(r.iterrows(), dec.iterrows()):
        lb = lab_e.loc[row["date"]]
        if only is not None and row["date"] != only:
            continue
        if row["date"] == asked_date:
            tot += 1.0 if lb.expected_action == "SUGGEST" else 0.0
            continue
        tot += points(d.action, d.hours, lb.truth_hours, lb.displayed_hours_at_snapshot, lb.expected_action)
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    lab = D.labels(2026)
    reg = D.regular_hours_for(lab, D.SNAP[2026])
    lab = lab.assign(state=[state_of(t, g) for t, g in zip(lab.truth_hours, reg)])
    need = lab[lab.expected_action == "SUGGEST"].entity_id.unique()
    rng = np.random.default_rng(a.seed)
    sample = rng.choice(sorted(need), size=min(a.n, len(need)), replace=False)
    rows = []
    for i, eid in enumerate(sample):
        lab_e = lab[lab.entity_id == eid].set_index("date")
        base = card_points(eid, {}, None, lab_e)
        qv = question_value(eid, 2026, {})
        if not qv:
            continue
        best_date, best_val = qv[0]
        ask = best_val > 0
        gain_q = card_points(eid, {best_date: lab_e.loc[best_date, "state"]}, best_date, lab_e) if ask else base
        # the asked date's own gain (the owner tells us its hours) vs the spillover to the other dates
        own_before = card_points(eid, {}, None, lab_e, only=best_date)
        own_after = 1.0 if lab_e.loc[best_date, "expected_action"] == "SUGGEST" else 0.0
        dates = [d for d, _ in qv]
        rd = dates[rng.integers(0, len(dates))]
        gain_r = card_points(eid, {rd: lab_e.loc[rd, "state"]}, rd, lab_e)
        r0, dec0 = _decisions(eid, 2026, {})
        on_card = {row["date"] for (_, row), (_, d) in zip(r0.iterrows(), dec0.iterrows()) if d.action == "SUGGEST"}
        off = next(((d, v) for d, v in qv if v > 0 and d not in on_card), None)
        gain_off = card_points(eid, {off[0]: lab_e.loc[off[0], "state"]}, off[0], lab_e) if off else base
        rows.append({"entity_id": eid, "no_question": base, "best_question": gain_q, "random_question": gain_r,
                     "off_card_question": gain_off, "asked_off_card": off is not None,
                     "best_date_on_card": ask and best_date in on_card,
                     "asked": ask, "asked_date": best_date, "predicted_value": best_val,
                     "asked_date_gain": own_after - own_before})
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/{len(sample)} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    out = {"businesses": len(df), "asked_share": float(df.asked.mean()),
           "points_no_question": float(df.no_question.sum()), "points_best_question": float(df.best_question.sum()),
           "points_random_question": float(df.random_question.sum()),
           "gain_per_business_best": float((df.best_question - df.no_question).mean()),
           "gain_per_business_random": float((df.random_question - df.no_question).mean()),
           "points_off_card_question": float(df.off_card_question.sum()),
           "gain_per_business_off_card": float((df.off_card_question - df.no_question).mean()),
           "asked_share_off_card": float(df.asked_off_card.mean()),
           "best_date_was_on_card_share": float(df.best_date_on_card.mean()),
           "predicted_value_mean": float(df.predicted_value.mean()),
           "gain_on_asked_date": float(df.asked_date_gain.mean()),
           "gain_spillover_other_dates": float((df.best_question - df.no_question - df.asked_date_gain).mean())}
    D.CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(D.CACHE / "question_eval.csv", index=False)
    (D.CACHE / "question_eval.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
