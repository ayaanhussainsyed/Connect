"""Where the new hours show, and by when the owner must approve (BUILD_PLAN_A §A9, APP_DESIGN step 6).

The data is the dataset's ``data/listings.json`` (Fonecta: listings are fixed; the sandbox
Listings API is empty). For the sandbox café the rows are FI-D0001's, and every row says so in
its ``source`` field.

    where_it_shows("FI-D0001", record.at)   # -> rows, fastest first
    deadline_for("FI-D0001", "2026-12-24")  # -> date(2026, 12, 17): approve by this day

Status texts: LIVE -> the rounded update time ("~2 min", "~1 h", "~2 days"); OPTED_OUT -> "not
connected"; WAITING_ON_PUBLISHER -> "pending at the publisher".
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

from cs.config import settings

LIVE = "LIVE"
STATUS_TEXT = {"OPTED_OUT": "not connected", "WAITING_ON_PUBLISHER": "pending at the publisher"}
DATASET_SOURCE = "dataset listings.json"


@lru_cache(maxsize=1)
def _listings_index() -> dict[str, tuple[dict, ...]]:
    with open(settings.dataset_dir / "data" / "listings.json", encoding="utf-8") as f:
        rows = json.load(f)["listings"]
    idx: dict[str, list[dict]] = {}
    for r in rows:
        idx.setdefault(r["entityId"], []).append(r)
    return {k: tuple(v) for k, v in idx.items()}


def listings_for(entity_id: str) -> list[dict]:
    """The raw listings.json rows for one dataset business (copies)."""
    return [dict(r) for r in _listings_index().get(entity_id, ())]


def dataset_id(entity_id: str) -> str:
    """Map a sandbox entity id (e.g. SinceAi04) back to its dataset business (FI-D0001)."""
    from cs.yext import SANDBOX_MAP

    reverse = {v: k for k, v in SANDBOX_MAP.items()}
    return reverse.get(entity_id, entity_id)


def eta_text(seconds: int | float) -> str:
    """Rounded update time: 120 -> '~2 min', 3600 -> '~1 h', 86400 -> '~1 day', 172800 -> '~2 days'."""
    s = max(0, float(seconds))
    if s < 3600:
        return f"~{max(1, round(s / 60))} min"
    if s < 86400:
        return f"~{max(1, round(s / 3600))} h"
    n = max(1, round(s / 86400))
    return f"~{n} day" + ("" if n == 1 else "s")


def _as_utc(t: datetime | str | None) -> datetime:
    if t is None:
        return datetime.now(timezone.utc)
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _sort_key(row: dict):
    return (row["status"] != LIVE, int(row["typicalUpdateSpeedSeconds"]), row.get("publisherName", ""))


def where_it_shows(dataset_entity_id: str, written_at: datetime | str | None = None, *,
                   target: str = "mock") -> list[dict]:
    """One row per publisher, LIVE ones first, fastest first::

        {publisher, publisher_id, status, typical_seconds, eta_text, expected_live_at, listing_url, source}

    ``eta_text`` is the rounded time for LIVE rows and the status text otherwise;
    ``expected_live_at`` = ``written_at`` + typical time (UTC datetime) for LIVE rows, else None.
    ``dataset_entity_id`` may also be the sandbox id; it is mapped back to the dataset business.
    """
    eid = dataset_id(dataset_entity_id)
    at = _as_utc(written_at)
    if target == "sandbox" or eid != dataset_entity_id:
        source = (f"{DATASET_SOURCE} for {eid} (the sandbox entity stands in for this business; "
                  f"the sandbox Listings API is empty)")
    else:
        source = f"{DATASET_SOURCE} for {eid}"
    out = []
    for r in sorted(listings_for(eid), key=_sort_key):
        secs = int(r["typicalUpdateSpeedSeconds"])
        live = r["status"] == LIVE
        out.append({
            "publisher": r.get("publisherName") or r["publisherId"],
            "publisher_id": r["publisherId"],
            "status": r["status"],
            "typical_seconds": secs,
            "eta_text": eta_text(secs) if live else STATUS_TEXT.get(r["status"], r["status"].lower()),
            "expected_live_at": at + timedelta(seconds=secs) if live else None,
            "listing_url": r.get("listingUrl"),
            "source": source,
        })
    return out


def _holiday_start(d: date | str) -> datetime:
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return datetime.combine(d, time(0, 0))


def deadline_by_publisher(dataset_entity_id: str, day: date | str) -> dict[str, str]:
    """{publisherId: ISO datetime} for LIVE publishers: the latest moment to approve so that
    publisher shows the change by 00:00 on ``day`` (fills ``Card.deadline_by_publisher``)."""
    start = _holiday_start(day)
    return {r["publisherId"]: (start - timedelta(seconds=int(r["typicalUpdateSpeedSeconds"]))).isoformat()
            for r in listings_for(dataset_id(dataset_entity_id)) if r["status"] == LIVE}


def deadline_for(dataset_entity_id: str, day: date | str) -> date:
    """The latest approve-by date so every LIVE publisher shows the change in time:
    ``day`` minus the slowest LIVE publisher's typical update time, rounded down to a day
    (2026-12-24 with a 7-day publisher -> 2026-12-17; with 1 h at most -> 2026-12-23).
    With no LIVE publisher there is nothing to wait for, so it is ``day`` itself."""
    start = _holiday_start(day)
    live = [int(r["typicalUpdateSpeedSeconds"]) for r in listings_for(dataset_id(dataset_entity_id))
            if r["status"] == LIVE]
    return (start - timedelta(seconds=max(live, default=0))).date()


def slowest_live(dataset_entity_id: str) -> dict | None:
    """The slowest LIVE publisher row (the one that sets the deadline), or None."""
    rows = [r for r in where_it_shows(dataset_entity_id) if r["status"] == LIVE]
    return rows[-1] if rows else None


def format_confirmation(rows: list[dict]) -> str:
    """Plain-text confirmation (CLI / logs). The owner-facing wording lives in cs.compose."""
    lines = ["Where it shows:"]
    for r in rows:
        lines.append(f"  {r['publisher']:<40} {r['eta_text']}")
    if rows:
        lines.append(f"  (source: {rows[0]['source']})")
    return "\n".join(lines)
