#!/usr/bin/env python3
"""Build suggestions for every target row, run the guardrails, save to MongoDB, write the CSV
and score it with the official evaluate.py (dev labels).

    .venv/bin/python scripts/predict.py --engine baseline
    .venv/bin/python scripts/predict.py --engine ml
    .venv/bin/python scripts/predict.py --engine baseline --season 2025 --no-mongo   # back-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs import export  # noqa: E402
from cs.suggest import engine_adapter  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=("baseline", "ml"), default="baseline")
    ap.add_argument("--season", type=int, choices=(2026, 2025), default=2026)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD for the not_past rule (default: the snapshot day)")
    ap.add_argument("--out", default=None, help="CSV path (default outputs/predictions_<engine>_<season>.csv)")
    ap.add_argument("--no-mongo", action="store_true", help="don't save to MongoDB")
    ap.add_argument("--no-score", action="store_true", help="don't run evaluate.py")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)

    if a.engine == "ml" and not engine_adapter.available(a.season):
        print(f"note: {engine_adapter.parquet_path(a.season)} not found; the ml engine falls back to the baseline",
              file=sys.stderr)
    res = export.run(season=a.season, engine=a.engine, today=a.today, out=a.out,
                     save=not a.no_mongo, do_score=not a.no_score)
    if a.json:
        print(json.dumps(res.as_dict(), indent=2))
        return 0
    print(f"engine {res.engine} · season {res.season} · model {res.model_versions}")
    print(f"rows {res.rows} · actions {res.actions}")
    print(f"guardrail downgrades {res.downgraded or '{}'} · SUGGEST rows not messageable {res.not_messageable}")
    print(f"CSV {res.csv_path} ({res.csv_rows} SUGGEST/FLAG rows)")
    if res.saved:
        print(f"MongoDB suggestions: {res.saved}")
    if res.score:
        s = res.score
        print(f"Score (dev, official evaluate.py): {s['score_0_100']}  · harmful {s['harmful_suggestions']} "
              f"· precision {s['precision_exact']} · recall {s['recall_exact']} "
              f"· search views protected {s['search_views_protected_pct']}%")
        print(f"  breakdown {json.dumps(s['breakdown'])}")
    print(f"time {res.seconds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
