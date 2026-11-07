"""Data loading for the score engine (read-only access to the Fonecta dataset).

Everything here is plain pandas. Each loader is cached in memory, so the notebook, the
training script and the app share one copy per process.

Time rules (honest back-tests):
- a season ``s`` covers special days from 7 Nov ``s`` to 6 Jan ``s+1``;
- its snapshot is ``SNAP[s]`` (6 Nov ``s``): only data stamped strictly before it may be used.
"""
from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DS = Path(os.environ.get("CS_DATASET_DIR", ROOT / "dataset" / "FONECTA_CONNECT_SUGGESTS_2026"))
ARTIFACTS = ROOT / "artifacts"
CACHE = ARTIFACTS / "cache"

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SNAP = {2023: "2023-11-06", 2024: "2024-11-06", 2025: "2025-11-06", 2026: "2026-11-06"}

# The winter special days that the scored seasons contain.
WINTER_KEYS = ["FATHERS_DAY", "BLACK_FRIDAY", "INDEPENDENCE_DAY", "LUCIA", "DEC23", "XMAS_EVE", "XMAS_DAY",
               "BOXING_DAY", "INTERDAY", "NYE", "NEW_YEARS_DAY", "EPIPHANY_EVE", "EPIPHANY", "EVENT",
               "SCHOOL_BREAK"]


# ----------------------------------------------------------------------------- hours helpers
def day_str(obj: dict | None) -> str:
    """Yext day object -> canonical hours string ('closed' / '09:00-17:00' / '..,..')."""
    if not obj or obj.get("isClosed"):
        return "closed"
    s = ",".join(f"{i['start']}-{i['end']}" for i in obj.get("openIntervals", []) or [])
    return s or "closed"


def holiday_item_str(h: dict) -> str:
    """Yext holidayHours item -> 'REGULAR' | 'closed' | hours string."""
    if h.get("isRegularHours"):
        return "REGULAR"
    return day_str(h)


def ivs(s: str) -> list[tuple[int, int]]:
    """Hours string -> list of (start, end) minutes; end may pass 1440 (past midnight). Scorer semantics."""
    if s is None or s == "closed" or s == "":
        return []
    out = []
    for iv in s.split(","):
        a, b = iv.split("-")
        sa, sb = int(a[:2]) * 60 + int(a[3:]), int(b[:2]) * 60 + int(b[3:])
        if sb <= sa and not (a == "00:00" and b == "23:59"):
            sb += 1440
        out.append((sa, sb))
    return out


def near(a: str, b: str, tol: int = 30) -> bool:
    """The scorer's 'near' test: same open/closed state and both ends within ``tol`` minutes."""
    ia, ib = ivs(a), ivs(b)
    if not ia or not ib:
        return not ia and not ib
    return abs(ia[0][0] - ib[0][0]) <= tol and abs(ia[-1][1] - ib[-1][1]) <= tol


def open_close(s: str) -> tuple[float, float]:
    """First opening and last closing minute (nan, nan for closed)."""
    i = ivs(s)
    if not i:
        return (np.nan, np.nan)
    return (float(i[0][0]), float(i[-1][1]))


def minutes_open(s: str) -> float:
    return float(sum(b - a for a, b in ivs(s)))


def cycle(d: str | date | pd.Timestamp) -> int:
    """Holiday 'year' used to match a day with the same day one season earlier (Feb..Jan)."""
    d = pd.Timestamp(d)
    return d.year if d.month >= 2 else d.year - 1


def season_of(d) -> int:
    """Scored season of a winter day: Nov..Dec -> that year, Jan -> previous year."""
    d = pd.Timestamp(d)
    return d.year if d.month >= 7 else d.year - 1


# ----------------------------------------------------------------------------- loaders
def _p(*parts) -> Path:
    return DS.joinpath(*parts)


@lru_cache(maxsize=None)
def entities() -> pd.DataFrame:
    E = json.load(open(_p("data", "entities.json"), encoding="utf-8"))["response"]["entities"]
    cities = pd.read_csv(_p("data", "cities.csv"))
    tiers = dict(zip(cities.city, cities.city_tier))
    rows = []
    for e in E:
        h = e.get("hours", {}) or {}
        rows.append({
            "entity_id": e["meta"]["id"],
            "country": e["meta"]["countryCode"],
            "language": e["meta"].get("language"),
            "name": e.get("name"),
            "city": e["address"]["city"],
            "city_tier": tiers.get(e["address"]["city"], "town"),
            "lat": (e.get("geocodedCoordinate") or {}).get("latitude"),
            "lon": (e.get("geocodedCoordinate") or {}).get("longitude"),
            "segment": e.get("c_segment"),
            "category": (e.get("categoryIds") or [None])[0],
            "mall_id": e.get("c_shoppingCenterEntityId"),
            "role": e.get("c_entityRole"),
            "customer_since": e.get("c_connectCustomerSince"),
            "listing_closed": bool(e.get("closed")),
            "reopen_date": h.get("reopenDate"),
        })
    df = pd.DataFrame(rows)
    sp = pd.read_csv(_p("data", "splits.csv"))
    df = df.merge(sp, on="entity_id", how="left")
    cats = pd.read_csv(_p("data", "categories.csv"))[["segment", "archetype"]].drop_duplicates("segment")
    df = df.merge(cats, on="segment", how="left")
    return df


@lru_cache(maxsize=None)
def holiday_history() -> pd.DataFrame:
    """All exception entries: entity_id, date, day_key, hours ('REGULAR'|'closed'|str), entered_at, source."""
    rows = []
    with open(_p("data", "holiday_hours_history.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows.append((r["entity_id"], r["date"], r["day_key"], holiday_item_str(r["holidayHours"]),
                         r["entered_at"], r["source"]))
    df = pd.DataFrame(rows, columns=["entity_id", "date", "day_key", "hours", "entered_at", "source"])
    return df.sort_values(["entity_id", "date", "entered_at"]).reset_index(drop=True)


def entries_as_of(as_of: str, exclude_sources: tuple[str, ...] = (), entity_ids=None) -> pd.DataFrame:
    """The latest entry per (entity, date) among entries stamped strictly before ``as_of``."""
    h = holiday_history()
    h = h[h.entered_at < as_of]
    if exclude_sources:
        h = h[~h.source.isin(exclude_sources)]
    if entity_ids is not None:
        h = h[h.entity_id.isin(set(entity_ids))]
    return h.drop_duplicates(["entity_id", "date"], keep="last").reset_index(drop=True)


@lru_cache(maxsize=None)
def regular_history() -> pd.DataFrame:
    rows = []
    with open(_p("data", "regular_hours_history.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            h = r["hours"]
            eff = "0000-00-00" if r["effective_from"] == "before-2024" else r["effective_from"]
            row = {"entity_id": r["entity_id"], "effective_from": eff, "reopen_date": h.get("reopenDate")}
            for i, d in enumerate(DAYS):
                row[d] = day_str(h.get(d))
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["entity_id", "effective_from"]).reset_index(drop=True)


@lru_cache(maxsize=None)
def regular_at(as_of: str) -> pd.DataFrame:
    """Published regular hours (one row per entity, columns monday..sunday) as of the snapshot."""
    r = regular_history()
    r = r[r.effective_from <= as_of]
    return r.drop_duplicates("entity_id", keep="last").set_index("entity_id")


@lru_cache(maxsize=None)
def calendar() -> pd.DataFrame:
    return pd.read_csv(_p("data", "calendar_special_days.csv"))


@lru_cache(maxsize=None)
def truth_history() -> pd.DataFrame:
    return pd.read_csv(_p("labels", "dev_truth_history.csv"), dtype={"event_id": str})


@lru_cache(maxsize=None)
def labels(season: int) -> pd.DataFrame:
    f = "dev_labels_2026.csv" if season == 2026 else "dev_labels_2025_backtest.csv"
    return pd.read_csv(_p("labels", f), dtype={"event_id": str})


def labels_path(season: int) -> Path:
    return _p("labels", "dev_labels_2026.csv" if season == 2026 else "dev_labels_2025_backtest.csv")


@lru_cache(maxsize=None)
def targets(season: int) -> pd.DataFrame:
    f = "targets_2026.csv" if season == 2026 else "targets_2025_backtest.csv"
    return pd.read_csv(_p("evaluation", f), dtype={"event_id": str})


@lru_cache(maxsize=None)
def regular_truth() -> pd.DataFrame:
    return pd.read_csv(_p("labels", "dev_regular_hours_truth.csv"))


@lru_cache(maxsize=None)
def reviews() -> pd.DataFrame:
    R = json.load(open(_p("data", "reviews.json"), encoding="utf-8"))["response"]["reviews"]
    df = pd.DataFrame([{"review_id": r["id"], "entity_id": r["locationId"], "rating": r["rating"],
                        "content": r.get("content") or "", "publisher": r.get("publisherId"),
                        "ts": pd.Timestamp(r["publisherDate"], unit="ms"),
                        "n_comments": len(r.get("comments") or [])} for r in R])
    df["day"] = df.ts.dt.strftime("%Y-%m-%d")
    return df


@lru_cache(maxsize=None)
def user_edits() -> pd.DataFrame:
    """Publisher (Google/Apple/Bing user) suggestions, one row per suggested holiday date or field.

    kind: HOLIDAY (a dated exception), WEEKDAY (regular hours of a weekday), CLOSED (closed for good)."""
    S = json.load(open(_p("data", "publisher_suggestions.json"), encoding="utf-8"))["response"]["suggestions"]
    rows = []
    for s in S:
        base = {"edit_id": s["id"], "entity_id": s["locationId"], "publisher": s["publisherId"],
                "created": s["dateCreated"], "resolved": s.get("dateResolved"), "status": s["status"],
                "field": s["fieldName"]}
        if s["fieldName"] == "closed":
            rows.append({**base, "kind": "CLOSED", "date": None, "weekday": None, "hours": "closed"})
            continue
        try:
            sug = json.loads(s["suggestedContent"] or "{}")
        except json.JSONDecodeError:
            continue
        for h in sug.get("holidayHours", []) or []:
            rows.append({**base, "kind": "HOLIDAY", "date": h.get("date"), "weekday": None,
                         "hours": holiday_item_str(h)})
        for d in DAYS:
            if d in sug:
                rows.append({**base, "kind": "WEEKDAY", "date": None, "weekday": d, "hours": day_str(sug[d])})
    return pd.DataFrame(rows)


@lru_cache(maxsize=None)
def events() -> pd.DataFrame:
    J = json.load(open(_p("data", "local_events.json"), encoding="utf-8"))["data"]
    return pd.DataFrame([{"event_id": e["@id"], "name_en": e["name"].get("en"), "start": e["start_time"],
                          "end": e["end_time"], "lon": e["location"]["position"]["coordinates"][0],
                          "lat": e["location"]["position"]["coordinates"][1],
                          "keywords": ",".join(e.get("keywords") or []), "country": e.get("x_country"),
                          "attendance": e.get("x_expected_attendance")} for e in J])


@lru_cache(maxsize=None)
def school_breaks() -> pd.DataFrame:
    return pd.read_csv(_p("data", "school_breaks.csv"))


@lru_cache(maxsize=None)
def pilot_log() -> pd.DataFrame:
    return pd.read_csv(_p("data", "pilot_decision_log_2025.csv"))


@lru_cache(maxsize=None)
def contacts() -> pd.DataFrame:
    return pd.read_csv(_p("data", "customer_contacts.csv"))


@lru_cache(maxsize=None)
def analytics() -> pd.DataFrame:
    return pd.read_parquet(_p("data", "analytics_daily.parquet"))


@lru_cache(maxsize=None)
def listings() -> pd.DataFrame:
    return pd.DataFrame(json.load(open(_p("data", "listings.json"), encoding="utf-8"))["listings"])


# ----------------------------------------------------------------------------- what Google shows now (A1)
def _own_displayed(rows: pd.DataFrame, as_of: str, exclude_sources: tuple[str, ...] = ()) -> np.ndarray:
    """Fallback A1 logic (99.85% parity); used only if the builder's cs.display is unavailable."""
    ent = entries_as_of(as_of, exclude_sources)
    m = rows[["entity_id", "date"]].merge(ent[["entity_id", "date", "hours"]], on=["entity_id", "date"], how="left")
    reg_h = _own_regular_for(rows, as_of)
    e_h = m["hours"].values
    out = np.where(pd.isna(e_h) | (e_h == "REGULAR"), reg_h, e_h)
    if as_of >= SNAP[2026]:
        closed_ids = set(entities().loc[entities().listing_closed, "entity_id"])
        out = np.where(np.isin(rows["entity_id"].values, list(closed_ids)), "closed", out)
    return out


def _own_regular_for(rows: pd.DataFrame, as_of: str) -> np.ndarray:
    reg = regular_at(as_of)
    wd = pd.to_datetime(rows["date"]).dt.day_of_week.values
    idx = reg.index.get_indexer(rows["entity_id"].values)
    regvals = reg[DAYS].values
    return np.array([regvals[i, w] if i >= 0 else "closed" for i, w in zip(idx, wd)], dtype=object)


def displayed(rows: pd.DataFrame, as_of: str, exclude_sources: tuple[str, ...] = ()) -> pd.Series:
    """What the listing shows for each (entity_id, date) row at ``as_of`` (A1).

    Delegates to the builder's ``cs.display`` (100% parity with Fonecta's displayed hours); falls back to
    the engine's own copy of the baseline logic if that module is missing. ``exclude_sources`` (used for the
    learning ablation) forces the fallback, since it changes which entries exist."""
    if not exclude_sources:
        try:
            from cs.display import explain_shown
            out = [explain_shown(e, d, as_of)["shown"] for e, d in zip(rows["entity_id"].values, rows["date"].values)]
            return pd.Series(out, index=rows.index, name="shown")
        except ImportError:
            pass
    return pd.Series(_own_displayed(rows, as_of, exclude_sources), index=rows.index, name="shown")


@lru_cache(maxsize=None)
def _regular_weeks(as_of: str) -> dict:
    try:
        from cs.display import resolve_regular
    except ImportError:
        return {}
    out = {}
    for eid in entities().entity_id:
        try:
            out[eid] = resolve_regular(eid, as_of).week
        except Exception:  # noqa: BLE001 - entity without regular hours
            pass
    return out


def regular_hours_for(rows: pd.DataFrame, as_of: str) -> pd.Series:
    """Published regular hours of the row's weekday at ``as_of`` (seasonal reopen rule from cs.display)."""
    weeks = _regular_weeks(as_of)
    if not weeks:
        return pd.Series(_own_regular_for(rows, as_of), index=rows.index)
    wd = pd.to_datetime(rows["date"]).dt.day_of_week.values
    fb = None
    out = []
    for e, w in zip(rows["entity_id"].values, wd):
        wk = weeks.get(e)
        if wk is None:
            if fb is None:
                fb = dict(zip(zip(rows["entity_id"].values, wd), _own_regular_for(rows, as_of)))
            out.append(fb[(e, w)])
        else:
            out.append(wk[w])
    return pd.Series(out, index=rows.index)


# Extra training periods from the dev truth (non-winter holidays), each with its own snapshot.
# name -> (snapshot, first day, last day). They add training rows for M2/M3; scored seasons are ints.
PERIODS = {
    "w2023": ("2023-11-06", "2023-11-07", "2024-01-06"),
    "sp2024": ("2024-03-01", "2024-03-01", "2024-07-31"),
    "sp2025": ("2025-03-01", "2025-03-01", "2025-07-31"),
    "sp2026": ("2026-03-01", "2026-03-01", "2026-07-31"),
    "au2024": ("2024-10-01", "2024-10-01", "2024-11-06"),
    "au2025": ("2025-10-01", "2025-10-01", "2025-11-06"),
    "au2026": ("2026-10-01", "2026-10-01", "2026-11-05"),
}
for _k, (_snap, _a, _b) in PERIODS.items():
    SNAP[_k] = _snap


def season_window(season) -> tuple[str, str]:
    if season in PERIODS:
        return PERIODS[season][1], PERIODS[season][2]
    return f"{season}-11-07", f"{season + 1}-01-06"


def season_truth(season: int) -> pd.DataFrame:
    """Dev truth rows for a scored season window (from the labels for 2025/2026, the truth history otherwise)."""
    if season in (2025, 2026):
        lab = labels(season)
        return lab[["entity_id", "date", "day_key", "event_id", "truth_hours", "expected_action"]].copy()
    a, b = season_window(season)
    t = truth_history()
    t = t[(t.date >= a) & (t.date <= b)]
    t = t[["entity_id", "date", "day_key", "event_id", "truth_hours"]].copy()
    t["expected_action"] = None
    return t.reset_index(drop=True)
