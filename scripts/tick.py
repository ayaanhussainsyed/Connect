#!/usr/bin/env python3
"""Run what is due today: the daily "Open again tomorrow?" question for "closed until further notice".

    .venv/bin/python scripts/tick.py                    # today
    .venv/bin/python scripts/tick.py --today 2026-11-08 # demo: pretend it is tomorrow

In production this runs from cron once a day (inside each owner's contact window).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs import emergency  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    done = emergency.run_due_schedules(today)
    for d in done:
        print(f"asked {d['entity_id']}: open again on {d['asked_for']}? (schedule {d['schedule_id']})")
    print(f"{len(done)} question(s) sent for {today.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
