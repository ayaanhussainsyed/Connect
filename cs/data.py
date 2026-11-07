"""Cached, read-only loaders for the Fonecta dataset.

Data: "Connect Suggests simulator, Since AI ry 2026", CC BY 4.0. The dataset folder is
read-only: nothing here writes to it. ``mock_api/sim_data.json`` is never read.

Time rules (no leaks):
- The snapshot instant is **00:00 on ``as_of``** (the 2026 scenario: 6 Nov 2026, data up to 5 Nov).
- Anything *stamped* (``entered_at``, ``sent_at``, ``dateCreated``, review time, analytics
  day …) is visible only if it is strictly before that instant.
- A regular-hours version is visible if it is *in effect* at that instant, i.e.
  ``effective_from <= as_of``. Fonecta's labels confirm this: FI-00848's version effective
  2025-11-06 is what Google showed at the 2025-11-06 snapshot.
- ``entities.json`` is the live state at its ``meta.timestamp`` (2026-11-05T21:00). Its
  current-state fields (``closed``, ``reopenDate``, ``holidayHours``) carry no history, so
  callers must only trust them for ``as_of`` on or after ``entity_snapshot_at()``.

Returned objects are shared caches: treat them as read-only (copy before mutating).
"""
from __future__ import annotations

import csv
import json
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from cs.config import settings
from cs.hours import from_yext_day

SNAPSHOT = {2026: date(2026, 11, 6), 2025: date(2025, 11, 6)}
DEFAULT_AS_OF = SNAPSHOT[2026]
BEFORE_2024 = "before-2024"
# Earliest of the three local midnights (FI is UTC+2, SE/NO UTC+1): the strictest cutoff
# for epoch-stamped data such as review times.
_CUTOFF_TZ = ZoneInfo("Europe/Helsinki")


# ---------------------------------------------------------------------------
# Paths and time helpers
# ---------------------------------------------------------------------------
def dataset_path(*parts: str) -> Path:
    return Path(settings.dataset_dir, *parts)


def to_date(x: date | datetime | str) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def cutoff_str(as_of: date | str) -> str:
    """'YYYY-MM-DD' of the snapshot day. For ISO timestamps, ``ts < cutoff_str`` means
    'stamped before 00:00 on as_of' ('2025-11-06T09:00:00' > '2025-11-06')."""
    return to_date(as_of).isoformat()


def cutoff_epoch_ms(as_of: date | str) -> int:
    d = to_date(as_of)
    return int(datetime.combine(d, time(0, 0), tzinfo=_CUTOFF_TZ).timestamp() * 1000)


def season_of(as_of: date | str) -> int:
    """The season a snapshot belongs to: 2025-11-06 -> 2025, 2026-11-06 -> 2026."""
    d = to_date(as_of)
    return d.year if d.month >= 7 else d.year - 1


def _read_csv(*parts: str, **kw) -> pd.DataFrame:
    return pd.read_csv(dataset_path(*parts), dtype=str, keep_default_na=False, **kw)


def _read_json(*parts: str):
    with open(dataset_path(*parts), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Entities (the live Yext state at 2026-11-05T21:00)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def entities() -> dict[str, dict]:
    """entity_id -> raw Yext entity (businesses and shopping centres)."""
    ents = _read_json("data", "entities.json")["response"]["entities"]
    return {e["meta"]["id"]: e for e in ents}


def entity(entity_id: str) -> dict:
    """Raw entity; ``KeyError`` if unknown."""
    return entities()[entity_id]


@lru_cache(maxsize=1)
def entity_snapshot_at() -> datetime:
    """When entities.json was taken (latest ``meta.timestamp``, naive local time)."""
    return max(datetime.fromisoformat(e["meta"]["timestamp"]) for e in entities().values())


def entity_state_known(as_of: date | str) -> bool:
    """True if the snapshot instant (00:00 on as_of) is at or after entities.json was taken,
    so its current-state fields (closed, reopenDate, holidayHours) apply."""
    return datetime.combine(to_date(as_of), time(0, 0)) >= entity_snapshot_at()


@lru_cache(maxsize=1)
def splits() -> dict[str, str]:
    """entity_id -> dev | test | demo | reference."""
    return dict(_read_csv("data", "splits.csv")[["entity_id", "split"]].itertuples(index=False))


@lru_cache(maxsize=1)
def entity_index() -> pd.DataFrame:
    """One row per entity: id, name, country, city, segment, mall, flags, split."""
    sp = splits()
    rows = []
    for eid, e in entities().items():
        rows.append({
            "entity_id": eid,
            "name": e.get("name", ""),
            "country": e["meta"].get("countryCode", ""),
            "language": e["meta"].get("language", ""),
            "city": (e.get("address") or {}).get("city", ""),
            "segment": e.get("c_segment", ""),
            "mall_id": e.get("c_shoppingCenterEntityId") or "",
            "is_mall": e.get("c_entityRole") == "SHOPPING_CENTRE",
            "closed": bool(e.get("closed")),
            "reopen_date": (e.get("hours") or {}).get("reopenDate") or "",
            "split": sp.get(eid, ""),
        })
    return pd.DataFrame(rows).sort_values("entity_id", ignore_index=True)


# ---------------------------------------------------------------------------
# Holiday-hours history (exceptions entered by owners, Connect, support, malls)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _holiday_rows() -> tuple[dict, ...]:
    rows = []
    with open(dataset_path("data", "holiday_hours_history.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                r["hours"] = from_yext_day(r["holidayHours"])  # 'closed' | intervals | REGULAR
                rows.append(r)
    rows.sort(key=lambda r: (r["entity_id"], r["date"], r["entered_at"]))
    return tuple(rows)


@lru_cache(maxsize=1)
def _holiday_index() -> dict[tuple[str, str], tuple[dict, ...]]:
    idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in _holiday_rows():
        idx[(r["entity_id"], r["date"])].append(r)
    return {k: tuple(v) for k, v in idx.items()}


def holiday_entries(entity_id: str, d: date | str, as_of: date | str) -> list[dict]:
    """Entries for one business and date entered before the snapshot, oldest first
    (the last one is what counts). Each row: entity_id, date, day_key, holidayHours,
    entered_at, source, hours ('closed' | 'HH:MM-HH:MM[,…]' | 'REGULAR')."""
    cut = cutoff_str(as_of)
    rows = _holiday_index().get((entity_id, to_date(d).isoformat()), ())
    return [r for r in rows if r["entered_at"] < cut]


def holiday_entries_after(entity_id: str, d: date | str, as_of: date | str) -> int:
    """How many entries for this date were entered at/after the snapshot (invisible)."""
    cut = cutoff_str(as_of)
    return sum(1 for r in _holiday_index().get((entity_id, to_date(d).isoformat()), ())
               if r["entered_at"] >= cut)


def holiday_history(as_of: date | str) -> pd.DataFrame:
    """All entries entered before the snapshot, as a DataFrame (holidayHours kept as dict)."""
    cut = cutoff_str(as_of)
    df = pd.DataFrame([r for r in _holiday_rows() if r["entered_at"] < cut])
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Regular-hours history (published weekly hours, incl. seasonal closures)
# ---------------------------------------------------------------------------
def _version_key(effective_from: str) -> str:
    return "0000-00-00" if effective_from == BEFORE_2024 else effective_from


@lru_cache(maxsize=1)
def _regular_index() -> dict[str, tuple[dict, ...]]:
    idx: dict[str, list[dict]] = defaultdict(list)
    with open(dataset_path("data", "regular_hours_history.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                idx[r["entity_id"]].append(r)
    return {k: tuple(sorted(v, key=lambda r: _version_key(r["effective_from"]))) for k, v in idx.items()}


def regular_versions(entity_id: str, as_of: date | str) -> list[dict]:
    """Versions in effect at the snapshot instant or earlier (``effective_from <= as_of``),
    oldest first. Each row: entity_id, effective_from ('before-2024' or date), hours (Yext week)."""
    versions = _regular_index().get(entity_id, ())
    keys = [_version_key(v["effective_from"]) for v in versions]
    return list(versions[: bisect_right(keys, cutoff_str(as_of))])


def regular_history(as_of: date | str) -> pd.DataFrame:
    cut = cutoff_str(as_of)
    rows = [v for vs in _regular_index().values() for v in vs if _version_key(v["effective_from"]) <= cut]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Calendar, targets, labels
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def calendar() -> pd.DataFrame:
    """Special days FI/SE/NO 2024–2027 (public data, no as_of needed)."""
    return _read_csv("data", "calendar_special_days.csv")


@lru_cache(maxsize=2)
def targets(season: int = 2026) -> pd.DataFrame:
    """Rows to predict: evaluation/targets_2026.csv or targets_2025_backtest.csv."""
    name = "targets_2026.csv" if season == 2026 else "targets_2025_backtest.csv"
    return _read_csv("evaluation", name)


@lru_cache(maxsize=2)
def labels(season: int = 2026) -> pd.DataFrame:
    """Dev labels for a season (dev businesses only; never use them for test rows)."""
    name = "dev_labels_2026.csv" if season == 2026 else "dev_labels_2025_backtest.csv"
    df = _read_csv("labels", name)
    df["needs_action"] = df["needs_action"].astype(int)
    df["expected_search_views"] = pd.to_numeric(df["expected_search_views"], errors="coerce")
    return df


@lru_cache(maxsize=2)
def label_index(season: int = 2026) -> dict[tuple[str, str], dict]:
    """(entity_id, date) -> label row as a dict."""
    return {(r["entity_id"], r["date"]): r for r in labels(season).to_dict("records")}


@lru_cache(maxsize=1)
def _dev_truth_history_all() -> pd.DataFrame:
    return _read_csv("labels", "dev_truth_history.csv")


def dev_truth_history(as_of: date | str) -> pd.DataFrame:
    """True hours on special days before the snapshot (dev businesses, training data)."""
    df = _dev_truth_history_all()
    return df[df["date"] < cutoff_str(as_of)].reset_index(drop=True)


@lru_cache(maxsize=1)
def dev_regular_truth() -> pd.DataFrame:
    """True current regular hours, listing_is_outdated, permanently_closed (dev, 2026 truth)."""
    df = _read_csv("labels", "dev_regular_hours_truth.csv")
    for c in ("listing_is_outdated", "permanently_closed"):
        df[c] = df[c].astype(int)
    return df


# ---------------------------------------------------------------------------
# CRM, listings and signals
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def contacts() -> pd.DataFrame:
    df = _read_csv("data", "customer_contacts.csv")
    for c in ("standing_permission_holidays", "opted_out_of_suggestions"):
        df[c] = df[c].astype(int)
    return df


@lru_cache(maxsize=1)
def listings() -> pd.DataFrame:
    """Publishers per business with typical update speed (current state)."""
    df = pd.DataFrame(_read_json("data", "listings.json")["listings"])
    df["typicalUpdateSpeedSeconds"] = df["typicalUpdateSpeedSeconds"].astype(int)
    return df


@lru_cache(maxsize=1)
def _reviews_all() -> tuple[dict, ...]:
    return tuple(_read_json("data", "reviews.json")["response"]["reviews"])


def reviews(as_of: date | str) -> list[dict]:
    """Reviews published before the snapshot (``publisherDate`` epoch ms). Owner replies
    (``comments``) stamped at/after the snapshot are removed."""
    cut = cutoff_epoch_ms(as_of)
    out = []
    for r in _reviews_all():
        if r["publisherDate"] < cut:
            comments = [c for c in r.get("comments") or [] if _comment_ms(c) < cut]
            out.append({**r, "comments": comments} if comments != (r.get("comments") or []) else r)
    return out


def _comment_ms(c: dict) -> int:
    for k in ("publisherDate", "date", "dateCreated"):
        v = c.get(k)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v:
            try:
                return int(datetime.fromisoformat(v).replace(tzinfo=_CUTOFF_TZ).timestamp() * 1000)
            except ValueError:
                pass
    return 0  # undated reply: keep it (it is attached to an already-visible review)


@lru_cache(maxsize=1)
def _publisher_suggestions_all() -> tuple[dict, ...]:
    return tuple(_read_json("data", "publisher_suggestions.json")["response"]["suggestions"])


def publisher_suggestions(as_of: date | str) -> list[dict]:
    """Google/Apple/Bing user edits created before the snapshot. A resolution
    (``status``/``dateResolved``) made at/after the snapshot is reset to WAITING_ON_CUSTOMER."""
    cut = cutoff_str(as_of)
    out = []
    for s in _publisher_suggestions_all():
        if s["dateCreated"] < cut:
            if s.get("dateResolved") and s["dateResolved"] >= cut:
                s = {**s, "status": "WAITING_ON_CUSTOMER", "dateResolved": None, "resolvedBy": None}
            out.append(s)
    return out


@lru_cache(maxsize=1)
def _pilot_all() -> pd.DataFrame:
    return _read_csv("data", "pilot_decision_log_2025.csv")


def pilot_log(as_of: date | str) -> pd.DataFrame:
    """2025 pilot messages sent before the snapshot; decisions made at/after it are blanked."""
    cut = cutoff_str(as_of)
    df = _pilot_all()
    df = df[df["sent_at"] < cut].copy()
    late = (df["decided_at"] != "") & (df["decided_at"] >= cut)
    for c in ("decision", "final_hours", "decided_at", "owner_comment"):
        df.loc[late, c] = ""
    df.loc[late, "standing_permission_granted"] = "False"
    return df.reset_index(drop=True)


@lru_cache(maxsize=1)
def peers() -> pd.DataFrame:
    """Peer benchmarks. Aggregated over seasons 2023–2025 with no time stamp, so for the
    2025 back-test they include the season being predicted: use with care there."""
    df = _read_csv("data", "peer_benchmarks.csv")
    num = [c for c in df.columns if c not in ("country", "city_tier", "segment", "day_key")]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@lru_cache(maxsize=1)
def _analytics_all() -> pd.DataFrame:
    df = pd.read_parquet(dataset_path("data", "analytics_daily.parquet"))
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    return df


def analytics(as_of: date | str) -> pd.DataFrame:
    """Daily Google metrics per business for days before the snapshot."""
    df = _analytics_all()
    return df[df["date"] < cutoff_str(as_of)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Context (static or scheduled-in-advance data)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def cities() -> pd.DataFrame:
    return _read_csv("data", "cities.csv")


@lru_cache(maxsize=1)
def categories() -> pd.DataFrame:
    return _read_csv("data", "categories.csv")


@lru_cache(maxsize=1)
def school_breaks() -> pd.DataFrame:
    return _read_csv("data", "school_breaks.csv")


@lru_cache(maxsize=1)
def local_events() -> list[dict]:
    """Scheduled events (announced in advance, so future events are fair to use)."""
    return _read_json("data", "local_events.json")["data"]


@lru_cache(maxsize=1)
def _weather_all() -> pd.DataFrame:
    df = _read_csv("data", "weather_daily.csv")
    df["is_forecast"] = df["is_forecast"].astype(int)
    return df


def weather_daily(as_of: date | str, include_forecast: bool = True) -> pd.DataFrame:
    """Observed weather before the snapshot, plus the published forecast rows if the
    forecast was issued by then (the 6–15 Nov 2026 forecast exists only for the 2026 snapshot)."""
    df = _weather_all()
    cut = cutoff_str(as_of)
    observed = (df["is_forecast"] == 0) & (df["date"] < cut)
    keep = observed
    if include_forecast:
        fc = df["is_forecast"] == 1
        if fc.any() and cut >= df.loc[fc, "date"].min():
            keep = observed | fc
    return df[keep].reset_index(drop=True)


@lru_cache(maxsize=1)
def weather_normals() -> pd.DataFrame:
    return _read_csv("data", "weather_normals.csv")


def iter_csv(*parts: str):
    """Stream any dataset CSV as dicts (for one-off scripts)."""
    with open(dataset_path(*parts), encoding="utf-8") as f:
        yield from csv.DictReader(f)
