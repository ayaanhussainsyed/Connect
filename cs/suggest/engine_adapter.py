"""A3 v1 – the ML engine's saved predictions as Suggestions.

Reads ``artifacts/predictions/engine_<season>.parquet`` (written by the ML agent; columns as in the
docs/AGENTS.md contract: entity_id, date, day_key, action, hours, probability, basis, evidence,
candidates, p_closed_for_good). It never imports the ML code. Every target row gets a Suggestion:
rows the file lacks become NO_ACTION with a note. If the file is missing, ``suggestions`` falls back
to the baseline (and says so in the log and in ``model_version``).
"""
from __future__ import annotations

import logging
import math
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

from cs import data, display
from cs.config import settings
from cs.domain import Evidence, Suggestion, suggestion_id
from cs.suggest import baseline

log = logging.getLogger(__name__)

MODEL_VERSION = "ml-engine"
REQUIRED_COLUMNS = ("entity_id", "date", "day_key", "action", "hours", "probability", "basis",
                    "evidence", "candidates", "p_closed_for_good")


def parquet_path(season: int = 2026) -> Path:
    return settings.root / "artifacts" / "predictions" / f"engine_{season}.parquet"


def available(season: int = 2026) -> bool:
    return parquet_path(season).exists()


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_parquet(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} lacks columns {missing}")
    return df


def load(season: int = 2026) -> pd.DataFrame:
    p = parquet_path(season)
    return _load(str(p), p.stat().st_mtime)


def artifact_info(season: int = 2026) -> dict:
    p = parquet_path(season)
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    return {"path": str(p), "exists": True, "mtime": pd.Timestamp(st.st_mtime, unit="s", tz="UTC").isoformat(timespec="seconds"),
            "bytes": st.st_size}


def _num(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _str(x) -> str | None:
    if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA:
        return None
    s = str(x).strip()
    return s or None


def _evidence(items) -> list[Evidence]:
    out = []
    for e in list(items) if items is not None else []:
        if isinstance(e, dict):
            out.append(Evidence(source=str(e.get("source", "MODEL")), detail=str(e.get("detail", "")),
                                ref=_str(e.get("ref")), weight=_num(e.get("weight")) or 0.0))
    return out


def _candidates(items) -> list[dict]:
    out = []
    for c in list(items) if items is not None else []:
        if isinstance(c, dict):
            out.append({"hours": _str(c.get("hours")), "prob": _num(c.get("prob")), "basis": _str(c.get("basis"))})
    return out


def row_to_suggestion(r: dict, as_of: date, season: int) -> Suggestion:
    action = (_str(r.get("action")) or "NO_ACTION").upper()
    hours = _str(r.get("hours"))
    return Suggestion(
        id=suggestion_id(MODEL_VERSION, r["entity_id"], r["date"]),
        entity_id=r["entity_id"], kind="HOLIDAY_HOURS", date=r["date"], day_key=r["day_key"],
        shown=display.shown_hours(r["entity_id"], r["date"], as_of),
        action=action, value=hours if action == "SUGGEST" else None,
        probability=_num(r.get("probability")), basis=_str(r.get("basis")) or "MODEL",
        evidence=_evidence(r.get("evidence")), model_version=MODEL_VERSION, season=season,
        candidates=_candidates(r.get("candidates")), p_closed_for_good=_num(r.get("p_closed_for_good")),
    )


def suggestions(season: int = 2026, entity_ids: list[str] | set[str] | None = None,
                as_of: date | str | None = None) -> list[Suggestion]:
    as_of = data.to_date(as_of or data.SNAPSHOT[season])
    if not available(season):
        log.warning("engine predictions %s missing; falling back to the baseline", parquet_path(season))
        return baseline.suggestions(season, entity_ids, as_of)
    t = data.targets(season)[["entity_id", "date", "day_key"]]
    if entity_ids is not None:
        t = t[t["entity_id"].isin(set(entity_ids))]
    df = load(season)
    if entity_ids is not None:
        df = df[df["entity_id"].isin(set(entity_ids))]
    by_key = {(r["entity_id"], r["date"]): r for r in df.to_dict("records")}
    out = []
    for e, d, k in zip(t["entity_id"], t["date"], t["day_key"]):
        r = by_key.get((e, d))
        if r is None:
            s = Suggestion(id=suggestion_id(MODEL_VERSION, e, d), entity_id=e, kind="HOLIDAY_HOURS", date=d,
                           day_key=k, shown=display.shown_hours(e, d, as_of), action="NO_ACTION",
                           basis="MODEL", model_version=MODEL_VERSION, season=season,
                           guardrail_notes=["engine: no prediction row for this target"])
        else:
            s = row_to_suggestion(r, as_of, season)
        out.append(s)
    return out
