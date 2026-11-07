"""B1: which single question to ask an owner, valued through the whole pipeline.

For each candidate date d of the business's season card and each possible answer a in {closed, regular,
special} (weighted by the engine's current P(a)):
1. the habit-type model (M1) is updated with d = a (closed form);
2. the new M1 day probabilities are fed into the state model (M2) and the ranker (M3) for every other date;
3. the expected points of the best action are recomputed for every date. The answered date itself becomes
   certain: the owner gives the hours, so it scores +1 if it needs a change.

value(d) = E_a[ total expected points after the answer ] - total expected points now.
Dates are returned sorted by value; 0 means the answer cannot change anything, so don't ask.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import data as D
from . import features as F
from .decide import assemble, decide, expected_points
from .features import STATES
from .pipeline import add_m1

M1_COLS = ["m1_closed", "m1_regular", "m1_special"]


def _engine():
    from .api import load_engine
    return load_engine()


@lru_cache(maxsize=4)
def _context(season: int):
    return F.make_context(D.SNAP[season], link_weights=_engine()["fit"].lw)


@lru_cache(maxsize=4096)
def _frames(entity_id: str, season: int):
    eng = _engine()
    rows = D.targets(season)
    rows = rows[rows.entity_id == entity_id].reset_index(drop=True)
    if rows.empty:
        raise KeyError(f"{entity_id} has no target rows in {season}")
    ctx = _context(season)
    r, c = F.build(rows, ctx)
    r = add_m1(r, eng["fit"].hm, ctx)
    r["listing_closed"] = bool(D.entities().set_index("entity_id").listing_closed.get(entity_id, False)) \
        if season == 2026 else False
    from .pipeline import cfg_probs
    r["p_cfg"] = cfg_probs(r, season)
    return r, c


def _m1_for(entity_id: str, r: pd.DataFrame, known: dict) -> np.ndarray:
    from .api import habit_model
    hm = habit_model()
    out = np.full((len(r), 3), np.nan)
    post = hm.posterior(entity_id, known)
    for i, k in enumerate(r["day_key"].values):
        if k in hm.key_idx:
            out[i] = post @ hm.theta[:, hm.key_idx[k], :]
    return out


def _fixed(r: pd.DataFrame, known: dict) -> dict[int, str]:
    """Rows whose state the owner has answered (by date, or by day key except multi-date keys)."""
    out = {}
    for i, (d, k) in enumerate(zip(r["date"].values, r["day_key"].values)):
        st = known.get(d, known.get(k) if k != "INTERDAY" else None)
        if st in STATES:
            out[i] = st
    return out


def _decisions(entity_id: str, season: int = 2026, known: dict | None = None):
    """The engine's decisions for one business's card, given the owner's answers so far."""
    eng = _engine()
    fit = eng["fit"]
    known = dict(known or {})
    r, c = _frames(entity_id, season)
    r = r.copy()
    r[M1_COLS] = _m1_for(entity_id, r, known)
    p_state = fit.sm.predict_proba(r)
    for i, st in _fixed(r, known).items():
        p_state[i] = np.eye(3)[STATES.index(st)]
    spm = (c.is_closed.values == 0) & (c.is_reg.values == 0)
    p_rank = np.zeros(len(c))
    if spm.any():
        p_rank[spm] = fit.rk.predict(c[spm], r)
    cc, p_other = assemble(r, c, p_state, p_rank)
    if eng.get("calibrator") is not None:
        cc, p_other = eng["calibrator"].transform(cc, len(r))
    r["p_other"] = p_other
    cep = expected_points(r, cc, p_other, p_cfg=r["p_cfg"].values, other_near=eng.get("other_near", 0.25))
    dec = decide(r, cep, p_other, p_cfg=r["p_cfg"].values, lam=eng["lam"],
                 allow_soft_flag=eng.get("allow_soft_flag", False))
    return r, dec


def _total_ep(entity_id: str, season: int, known: dict) -> float:
    r, dec = _decisions(entity_id, season, known)
    ep = np.where(dec.action.values == "FLAG", dec.ep_flag.values, dec.ep.values)
    for i, st in _fixed(r, known).items():
        shown, reg = r.loc[i, "shown"], r.loc[i, "reg"]
        if st == "closed":
            ep[i] = 1.0 if shown != "closed" else 0.0
        elif st == "regular":
            ep[i] = 1.0 if shown != reg else 0.0
        else:  # special: the owner types the hours
            ep[i] = 1.0 if (shown == reg or shown == "closed") else 0.5
    return float(np.sum(ep))


def question_value(entity_id: str, season: int = 2026, known: dict | None = None, max_dates: int | None = None
                   ) -> list[tuple[str, float]]:
    """Candidate dates for the one question, sorted by expected points gained (0 means don't ask)."""
    known = dict(known or {})
    r, _ = _frames(entity_id, season)
    base = _total_ep(entity_id, season, known)
    rr = r.copy()
    rr[M1_COLS] = _m1_for(entity_id, r, known)
    p_state = _engine()["fit"].sm.predict_proba(rr)
    fixed = _fixed(r, known)
    out = []
    for i, (d, k) in enumerate(zip(r["date"].values, r["day_key"].values)):
        if i in fixed or k in ("EVENT", "SCHOOL_BREAK"):
            continue
        gain, tot = 0.0, 0.0
        for j, st in enumerate(STATES):
            pa = float(p_state[i, j])
            if pa < 0.02:
                continue
            kn = dict(known)
            kn[d] = st
            gain += pa * _total_ep(entity_id, season, kn)
            tot += pa
        out.append((d, round(max(gain / max(tot, 1e-9) - base, 0.0), 4)))
    out.sort(key=lambda x: -x[1])
    return out[:max_dates] if max_dates else out
