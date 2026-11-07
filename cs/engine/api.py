"""The engine's public interface for the app (contract in docs/AGENTS.md).

    from cs.engine.api import predict, HabitModel, habit_model, question_value
    df = predict(2026)          # one row per target row; cached in artifacts/predictions/engine_2026.parquet

Columns of ``predict``: entity_id, date, day_key, action ('SUGGEST'|'NO_ACTION'|'FLAG'), hours (str or None),
probability (calibrated P(hours exact)), basis, evidence (list of {source, detail, ref, weight}),
candidates (list of {hours, prob, basis}), p_closed_for_good.

Models live in ``artifacts/models/`` (``engine.joblib``: M1-M4 + calibrator + λ; ``habits.joblib``: M1 with the
2026 evidence attached). ``scripts/train.py`` rebuilds them.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from . import data as D
from .features import LINK_KEYS, STATES
from .habits import HabitModel  # noqa: F401  (re-exported for the app)

MODELS = D.ARTIFACTS / "models"
PREDS = D.ARTIFACTS / "predictions"
ENGINE_PATH = MODELS / "engine.joblib"
HABITS_PATH = MODELS / "habits.joblib"

BASES = ["OWN_LAST_YEAR", "OWN_2_SEASONS", "USER_EDIT", "REVIEW", "LINKED_HOLIDAY", "MALL", "PEERS", "MODEL"]
OUT_COLS = ["entity_id", "date", "day_key", "action", "hours", "probability", "basis", "evidence", "candidates",
            "p_closed_for_good"]


def pred_path(season: int) -> Path:
    return PREDS / f"engine_{season}.parquet"


@lru_cache(maxsize=1)
def load_engine() -> dict:
    if not ENGINE_PATH.exists():
        raise FileNotFoundError(f"{ENGINE_PATH} missing: run `.venv/bin/python scripts/train.py` first")
    return joblib.load(ENGINE_PATH)


@lru_cache(maxsize=1)
def habit_model() -> HabitModel:
    """M1 fitted on the dev truth, with every business's entries before the 2026 snapshot as evidence."""
    return joblib.load(HABITS_PATH)


# ----------------------------------------------------------------------------- explanations
def _basis_for(cand: pd.Series, row: pd.Series) -> str:
    """Where a candidate comes from, strongest (most trusted by owners) first."""
    h = cand["hours"]
    if cand.get("c_own1", 0) == 1:
        return "OWN_LAST_YEAR"
    if cand.get("c_own2", 0) == 1:
        return "OWN_2_SEASONS"
    if cand.get("c_ue_now", 0) == 1 or cand.get("c_ue_prev", 0) == 1:
        return "USER_EDIT"
    if h == "closed":
        if row.get("rv_closed_prev", 0) > 0:
            return "REVIEW"
        if any(row.get(f"lk_{k}", -1) == 0 for k in LINK_KEYS):
            return "LINKED_HOLIDAY"
        if row.get("mall_state", -1) == 0:
            return "MALL"
        if (row.get("peert_closed_among") or 0) >= 0.5 or (row.get("peers_closed_among") or 0) >= 0.5:
            return "PEERS"
        return "MODEL"
    if cand.get("c_link", 0) == 1:
        return "LINKED_HOLIDAY"
    if cand.get("c_mall", 0) == 1:
        return "MALL"
    if cand.get("c_peer", 0) == 1:
        return "PEERS"
    return "MODEL"


def _evidence(row: pd.Series, best: pd.Series | None, p_cfg: float, sig: dict | None) -> list[dict]:
    ev = []
    eid = row["entity_id"]
    for lag, src in ((1, "OWN_LAST_YEAR"), (2, "OWN_2_SEASONS")):
        h = row.get(f"own{lag}_hours")
        if isinstance(h, str):
            d = row.get(f"own{lag}_date")
            ev.append({"source": src, "detail": f"You entered '{h}' for {d}", "ref": f"holiday_hours_history:{eid}:{d}",
                       "weight": 1.0 if lag == 1 else 0.95})
    for k in ("ue_now_hours", "ue_prev_hours"):
        h = row.get(k)
        if isinstance(h, str):
            when = "this date" if k == "ue_now_hours" else "the same day last season"
            ev.append({"source": "USER_EDIT", "detail": f"A map user suggested '{h}' for {when}",
                       "ref": f"publisher_suggestions:{eid}", "weight": 0.8})
    if row.get("rv_closed_prev", 0) > 0:
        ev.append({"source": "REVIEW", "detail": f"{int(row['rv_closed_prev'])} closed-door review(s) right after this "
                                                 f"day last season", "ref": f"reviews:{eid}", "weight": 0.6})
    linked = [k for k in LINK_KEYS if row.get(f"lk_{k}", -1) == 0 and k != row["day_key"]]
    if linked:
        ev.append({"source": "LINKED_HOLIDAY", "detail": "Closed last season on " + ", ".join(linked[:4]),
                   "ref": f"holiday_hours_history:{eid}", "weight": 0.7})
    if isinstance(row.get("mall_hours"), str):
        ev.append({"source": "MALL", "detail": f"Your shopping centre entered '{row['mall_hours']}'",
                   "ref": f"holiday_hours_history:{row.get('mall_id')}:{row['date']}", "weight": 0.8})
    pc = row.get("peert_closed_among")
    if pc is not None and not pd.isna(pc) and (row.get("peert_n") or 0) >= 10:
        ev.append({"source": "PEERS", "detail": f"{pc:.0%} of similar businesses nearby that entered this day were closed",
                   "ref": f"peers:{row['country']}/{row['city_tier']}/{row['segment']}/{row['day_key']}", "weight": 0.5})
    if not pd.isna(row.get("m1_closed", np.nan)):
        ev.append({"source": "MODEL", "detail": "Habit-type model: P(closed)={:.2f}, P(regular)={:.2f}, P(special)={:.2f}"
                  .format(row["m1_closed"], row["m1_regular"], row["m1_special"]), "ref": "model:M1", "weight": 0.5})
    ev.append({"source": "MODEL", "detail": "State model: P(closed)={:.2f}, P(regular)={:.2f}, P(special)={:.2f}"
              .format(row["p_closed"], row["p_regular"], row["p_special"]), "ref": "model:M2", "weight": 0.5})
    if p_cfg >= 0.2 and sig:
        ev.append({"source": "REVIEW", "detail": f"Closed-for-good signals: {sig}", "ref": f"reviews:{eid}",
                   "weight": float(p_cfg)})
    return ev


def explain(r: pd.DataFrame, cep: pd.DataFrame, dec: pd.DataFrame, cfg_sig: pd.DataFrame | None = None) -> pd.DataFrame:
    """Turn engine frames into the contract's output columns."""
    r = r.set_index("rid", drop=False)
    groups = {k: g.sort_values("p", ascending=False) for k, g in cep.groupby("rid")}
    out = []
    for _, d in dec.iterrows():
        rid = d["rid"]
        row = r.loc[rid]
        g = groups.get(rid)
        cands = []
        if g is not None:
            for _, cnd in g.head(5).iterrows():
                cands.append({"hours": cnd["hours"], "prob": float(round(cnd["p"], 4)), "basis": _basis_for(cnd, row)})
        p_cfg = float(row.get("p_cfg", 0.0))
        best = None
        basis = "MODEL"
        hours = d["hours"] if d["action"] == "SUGGEST" else None
        if hours is not None and g is not None:
            best = g[g.hours == hours].iloc[0]
            basis = _basis_for(best, row)
        elif d["action"] == "FLAG":
            basis = "REVIEW" if p_cfg >= 0.5 else "MODEL"
        elif cands:
            basis = cands[0]["basis"]
        sig = None
        if cfg_sig is not None and row["entity_id"] in cfg_sig.index and p_cfg >= 0.2:
            s = cfg_sig.loc[row["entity_id"]]
            sig = {"closure_reviews": int(s.perm_reviews), "closed_user_edits": int(s.closed_edits),
                   "views_collapse": bool(s.collapse)}
        prob = float(d["prob"]) if d["action"] == "SUGGEST" else (p_cfg if d["action"] == "FLAG" else
                                                                    float(d.get("p_shown", np.nan)))
        out.append({"entity_id": row["entity_id"], "date": row["date"], "day_key": row["day_key"], "action": d["action"],
                    "hours": hours, "probability": prob, "basis": basis, "evidence": _evidence(row, best, p_cfg, sig),
                    "candidates": cands, "p_closed_for_good": p_cfg})
    return pd.DataFrame(out, columns=OUT_COLS)


# ----------------------------------------------------------------------------- the contract
def run(season: int = 2026, rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute predictions with the saved models (no cache)."""
    from .flags import signals
    from .pipeline import predict_season
    eng = load_engine()
    r, cep, dec = predict_season(eng["fit"], eng["calibrator"], eng["lam"], season=season, rows=rows,
                                 allow_soft_flag=eng.get("allow_soft_flag", False),
                                 other_near=eng.get("other_near", 0.25))
    sig = signals(D.SNAP[season], entity_ids=pd.unique(r.entity_id))
    return explain(r, cep, dec, cfg_sig=sig)


def predict(season: int = 2026) -> pd.DataFrame:
    """One row per target row of ``evaluation/targets_<season>.csv`` (see module docstring).

    Reads ``artifacts/predictions/engine_<season>.parquet`` if it exists (written by scripts/train.py);
    otherwise computes it with the saved models and writes it. For 2025 the cached file holds the honest
    back-test (business-grouped, trained only on data before 6 Nov 2025)."""
    p = pred_path(season)
    if p.exists():
        return _normalise(pd.read_parquet(p))
    df = run(season)
    write_predictions(df, season)
    return _normalise(df)


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Plain Python values after the parquet round-trip: hours None (not NaN), evidence/candidates as lists."""
    df = df.copy()
    df["hours"] = pd.Series([h if isinstance(h, str) and h else None for h in df["hours"]], index=df.index, dtype=object)
    for col in ("evidence", "candidates"):
        df[col] = [list(x) if x is not None else [] for x in df[col]]
    return df


def write_predictions(df: pd.DataFrame, season: int):
    PREDS.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pred_path(season), index=False)


def to_scorer_csv(df: pd.DataFrame, path) -> int:
    """Write the scorer's format (entity_id,date,action,hours,confidence) for every row; returns #actions."""
    out = pd.DataFrame({"entity_id": df.entity_id, "date": df.date, "action": df.action,
                        "hours": df.hours.where(df.action == "SUGGEST", ""),
                        "confidence": df.probability.round(4).where(df.action != "NO_ACTION", np.nan)})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return int((df.action != "NO_ACTION").sum())


def question_value(entity_id: str, season: int = 2026, known: dict | None = None) -> list[tuple[str, float]]:
    """Candidate dates for B1's one question, sorted by expected points gained (0 means don't ask)."""
    from .question import question_value as qv
    return qv(entity_id, season, known or {})
