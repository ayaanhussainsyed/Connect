"""The "answer by" deadline for a card (B1/B4), with a fallback when ``cs.confirm`` is missing.

deadline = the date minus the slowest LIVE publisher's typical update time (``listings.json``),
rounded down to a day. ``cs.confirm.deadline_for`` is the source of truth; the local
calculation here is used only if it can't be imported (it is the same rule).

    card_deadline("FI-D0001", ["2026-12-24", "2026-12-25"])  -> date(2026, 12, 17)
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Callable, Iterable

LIVE = "LIVE"


def _to_date(d: date | datetime | str) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


@lru_cache(maxsize=1)
def _listings() -> dict[str, tuple[dict, ...]]:
    from cs.config import settings
    with open(settings.dataset_dir / "data" / "listings.json", encoding="utf-8") as f:
        rows = json.load(f)["listings"]
    idx: dict[str, list[dict]] = {}
    for r in rows:
        idx.setdefault(r["entityId"], []).append(r)
    return {k: tuple(v) for k, v in idx.items()}


def slowest_live(entity_id: str, listings: Iterable[dict] | None = None) -> tuple[int, str | None]:
    """(seconds, publisherId) of the slowest LIVE publisher; (0, None) if none is live."""
    rows = list(listings) if listings is not None else list(_listings().get(entity_id, ()))
    live = [(int(r["typicalUpdateSpeedSeconds"]), r["publisherId"]) for r in rows if r.get("status") == LIVE]
    return max(live) if live else (0, None)


def fallback_deadline_for(entity_id: str, day: date | str, listings: Iterable[dict] | None = None) -> date:
    """``day`` (00:00) minus the slowest LIVE publisher's update time, as a date."""
    secs, _ = slowest_live(entity_id, listings)
    return (datetime.combine(_to_date(day), time(0, 0)) - timedelta(seconds=secs)).date()


def _confirm_deadline_fn() -> Callable[[str, date | str], date] | None:
    try:
        from cs.confirm import deadline_for  # the Yext helper's version (source of truth)
        return deadline_for
    except Exception:  # noqa: BLE001 - not written yet, or broken: use the fallback
        return None


def deadline_for(entity_id: str, day: date | str) -> date:
    """Approve-by date for one day: ``cs.confirm.deadline_for`` if importable, else the fallback."""
    fn = _confirm_deadline_fn()
    if fn is not None:
        try:
            return _to_date(fn(entity_id, day))
        except Exception:  # noqa: BLE001
            pass
    return fallback_deadline_for(entity_id, day)


def card_deadline(entity_id: str, days: Iterable[date | str]) -> date | None:
    """The card's answer-by date: the earliest per-day deadline (None for no days)."""
    ds = [deadline_for(entity_id, d) for d in days]
    return min(ds) if ds else None
