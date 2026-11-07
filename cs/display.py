"""A1 – "What Google shows now": the hours a business's listing displays for a date.

    shown_hours("FI-D0001", "2026-12-24", as_of="2026-11-06")   -> "07:30-18:00"
    explain_shown(...)                                          -> dict: value + which source produced it

Rules (checked against Fonecta's ``displayed_hours_at_snapshot`` on 100% of
dev_labels_2026 and dev_labels_2025_backtest, and against ``displayed_hours_that_day`` in
dev_truth_history; see tests/test_display.py). In order of precedence:

1. **Closed for good.** ``closed: true`` in entities.json -> ``closed``. The flag has no history,
   so it is applied only when ``as_of`` is on/after the entities.json snapshot (2026-11-05T21:00).
2. **Temporarily closed.** ``hours.reopenDate`` (same snapshot caveat) -> ``closed`` for dates
   before the reopen date. (In this dataset the regular hours are all-closed then anyway.)
3. **Holiday exception.** The latest ``holiday_hours_history`` entry for that date with
   ``entered_at`` strictly before 00:00 on ``as_of`` (later row wins). ``isRegularHours`` means
   "use the regular hours" (step 4).
4. **Regular hours.** The weekday's hours from the regular-hours version in effect at the snapshot
   (``effective_from <= as_of``), with one dataset rule: **a seasonal reopening restores the hours
   published before the closure.** A version whose whole week is closed is a seasonal closure; the
   next open version ends it and the listing goes back to the pre-closure weekly hours. (FI-00330:
   its 2025-11-01 / 2026-11-01 rows list 10:00-18:00, but the listing, entities.json and the
   labels all show the 10:00-17:00 it published on 2025-04-16. With this rule the derived weekly
   hours equal entities.json for all 3,032 entities.)

CLI:  ``python -m cs.display FI-D0001 2026-12-24 [--as-of 2026-11-06] [--json]``
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd

from cs import data
from cs.hours import CLOSED, DAYS, REGULAR, week_from_yext

CLOSED_FOR_GOOD = "CLOSED_FOR_GOOD"
TEMPORARILY_CLOSED = "TEMPORARILY_CLOSED"
HOLIDAY_ENTRY = "HOLIDAY_ENTRY"
REGULAR_HOURS = "REGULAR_HOURS"

SOURCE_LABELS = {
    "OWNER_UI": "the owner",
    "CONNECT_SUGGESTION": "a Connect suggestion (2025 pilot)",
    "FONECTA_SUPPORT": "Fonecta support",
    "API_INTEGRATION": "the shopping centre's integration",
}


@dataclass(frozen=True)
class RegularResolution:
    week: tuple[str, ...]          # 7 hours strings, Monday first: what the listing shows
    effective_from: str            # the version in effect at the snapshot
    listed_week: tuple[str, ...]   # what that version's own history row lists
    seasonal_reopening: bool       # the version ends an all-closed seasonal closure
    closure_from: str | None       # effective_from of the closure it ended
    restored_from: str | None      # version whose hours were restored, if they differ from listed_week


@lru_cache(maxsize=65536)
def resolve_regular(entity_id: str, as_of_iso: str) -> RegularResolution:
    """The weekly hours the listing shows at the snapshot (see rule 4 in the module doc)."""
    versions = data.regular_versions(entity_id, as_of_iso)
    if not versions:
        raise ValueError(f"no regular-hours history for {entity_id} at {as_of_iso}")
    current: RegularResolution | None = None
    last_open: tuple[tuple[str, ...], str] | None = None  # (week shown, version it came from)
    closure_from: str | None = None
    for v in versions:
        listed = tuple(week_from_yext(v["hours"]))
        eff = v["effective_from"]
        if all(h == CLOSED for h in listed):
            current = RegularResolution(listed, eff, listed, False, None, None)
            closure_from = eff
            continue
        if closure_from is not None and last_open is not None:
            week, src = last_open
            current = RegularResolution(week, eff, listed, True, closure_from,
                                        src if week != listed else None)
        else:
            week, src = listed, eff
            current = RegularResolution(listed, eff, listed, False, None, None)
        last_open = (week, src)
        closure_from = None
    assert current is not None
    return current


def label_for(entity_id: str, d: date | str, as_of: date | str) -> dict | None:
    """Fonecta's dev label for this row, if ``as_of`` is a label snapshot and the row exists."""
    as_of = data.to_date(as_of)
    season = data.season_of(as_of)
    if data.SNAPSHOT.get(season) != as_of:
        return None
    return data.label_index(season).get((entity_id, data.to_date(d).isoformat()))


def _fmt_day(d: date) -> str:
    return f"{DAYS[d.weekday()].capitalize()} {d.isoformat()}"


def explain_shown(entity_id: str, d: date | str, as_of: date | str = data.DEFAULT_AS_OF) -> dict:
    """What Google shows for ``entity_id`` on ``d`` at the snapshot ``as_of``, and why.

    Raises ``KeyError`` for an unknown entity and ``ValueError`` for a bad date.
    """
    d, as_of = data.to_date(d), data.to_date(as_of)
    ent = data.entity(entity_id)
    known = data.entity_state_known(as_of)
    closed_flag = bool(ent.get("closed")) and known
    reopen = ((ent.get("hours") or {}).get("reopenDate") or None) if known else None

    reg = resolve_regular(entity_id, as_of.isoformat())
    wd = d.weekday()
    regular_hours = reg.week[wd]

    entries = data.holiday_entries(entity_id, d, as_of)
    hidden = data.holiday_entries_after(entity_id, d, as_of)
    entry = entries[-1] if entries else None

    if closed_flag:
        shown, source = CLOSED, CLOSED_FOR_GOOD
        summary = "The listing is marked permanently closed, so Google shows the business as closed."
    elif reopen and d < date.fromisoformat(reopen):
        shown, source = CLOSED, TEMPORARILY_CLOSED
        summary = f"The listing is temporarily closed until {reopen} (reopenDate), so Google shows closed."
    elif entry is not None and entry["hours"] != REGULAR:
        shown, source = entry["hours"], HOLIDAY_ENTRY
        summary = (f"A holiday exception for {d.isoformat()} was entered on {entry['entered_at'][:10]} "
                   f"by {SOURCE_LABELS.get(entry['source'], entry['source'])}: {shown}.")
        if len(entries) > 1:
            summary += f" It replaced {len(entries) - 1} earlier entr{'y' if len(entries) == 2 else 'ies'}."
    else:
        shown, source = regular_hours, REGULAR_HOURS
        lead = (f"The exception entered on {entry['entered_at'][:10]} says 'regular hours', so"
                if entry is not None else f"No holiday exception was entered before {as_of.isoformat()}, so")
        since = ("published before 2024" if reg.effective_from == data.BEFORE_2024
                 else f"effective {reg.effective_from}")
        summary = (f"{lead} Google shows the regular {DAYS[wd].capitalize()} hours: {regular_hours} "
                   f"(weekly hours {since}).")
        if reg.restored_from:
            summary += (f" That version reopens after the seasonal closure from {reg.closure_from} and "
                        f"restores the hours published on {reg.restored_from}.")
    if hidden:
        summary += (f" ({hidden} entr{'y' if hidden == 1 else 'ies'} made on or after "
                    f"{as_of.isoformat()} {'is' if hidden == 1 else 'are'} not visible at the snapshot.)")

    return {
        "entity_id": entity_id,
        "name": ent.get("name", ""),
        "date": d.isoformat(),
        "weekday": DAYS[wd],
        "as_of": as_of.isoformat(),
        "shown": shown,
        "source": source,
        "summary": summary,
        "regular": {
            "hours": regular_hours,
            "version_effective_from": reg.effective_from,
            "listed_hours": reg.listed_week[wd],
            "seasonal_reopening": reg.seasonal_reopening,
            "closure_from": reg.closure_from,
            "restored_from": reg.restored_from,
        },
        "entry": None if entry is None else {
            "hours": entry["hours"],
            "is_regular_hours": entry["hours"] == REGULAR,
            "entered_at": entry["entered_at"],
            "source": entry["source"],
            "holidayHours": entry["holidayHours"],
        },
        "earlier_entries": max(len(entries) - 1, 0),
        "entries_after_as_of": hidden,
        "closed_for_good": closed_flag,
        "reopen_date": reopen,
        "entity_state_known": known,
    }


def shown_hours(entity_id: str, d: date | str, as_of: date | str = data.DEFAULT_AS_OF) -> str:
    """Canonical hours string Google shows for that date at the snapshot (``closed`` / ``HH:MM-HH:MM[,…]``)."""
    return explain_shown(entity_id, d, as_of)["shown"]


def shown_table(season: int = 2026, as_of: date | str | None = None) -> pd.DataFrame:
    """Every target row of a season with ``shown`` and ``shown_source`` columns added."""
    as_of = data.to_date(as_of or data.SNAPSHOT[season])
    df = data.targets(season).copy()
    ex = [explain_shown(e, d, as_of) for e, d in zip(df["entity_id"], df["date"])]
    df["shown"] = [x["shown"] for x in ex]
    df["shown_source"] = [x["source"] for x in ex]
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.display", description="What Google shows now (A1).")
    ap.add_argument("entity_id")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--as-of", default=data.DEFAULT_AS_OF.isoformat(), help="snapshot date (default 2026-11-06)")
    ap.add_argument("--json", action="store_true", help="print the full explanation as JSON")
    a = ap.parse_args(argv)
    try:
        ex = explain_shown(a.entity_id, a.date, a.as_of)
    except KeyError:
        print(f"unknown entity: {a.entity_id}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"bad input: {e}", file=sys.stderr)
        return 2
    lab = label_for(a.entity_id, a.date, a.as_of)
    if a.json:
        print(json.dumps({**ex, "label": lab}, ensure_ascii=False, indent=2))
        return 0
    d = date.fromisoformat(ex["date"])
    print(f"{ex['entity_id']} {ex['name']} · {_fmt_day(d)} · as of {ex['as_of']}")
    print(f"Google shows: {ex['shown']}   [{ex['source']}]")
    print(f"Why: {ex['summary']}")
    if lab:
        ok = "✓" if lab["displayed_hours_at_snapshot"] == ex["shown"] else "✗"
        print(f"Dev label: displayed {lab['displayed_hours_at_snapshot']} {ok} · true hours {lab['truth_hours']} "
              f"· expected {lab['expected_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
