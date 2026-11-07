"""M4: the decision rule. Expected points under the scorer's exact table, FLAG included.

For each target row we have a calibrated distribution over the true hours T:
P(T = each candidate string) plus P(T = some other open hours). For every option we compute the
average points the official scorer would give, and pick the best:

    needs a change (T != shown): exact +1 | same state, both ends within 30 min +0.6 | same state +0.2
                                 | wrong open/closed state -1 | FLAG +0.05 | NO_ACTION 0
    already correct (T == shown): NO_ACTION 0 | FLAG -0.1 | SUGGEST shown -0.1 | SUGGEST other -0.5
                                 | SUGGEST wrong state -1
    closed for good (listing open): FLAG +1 | SUGGEST 'closed' +0.3 | SUGGEST open -1

A safety margin ``lam`` is subtracted from any suggestion that flips the open/closed state of what is
shown; it is tuned by business-grouped cross-validation (maximise points with harmful <= baseline).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D


# ----------------------------------------------------------------------------- the scorer, row by row
def points(action: str, hours: str | None, truth: str, shown: str, expected_action: str) -> float:
    """Points for one row exactly as ``evaluation/evaluate.py`` gives them."""
    h = D_norm(hours)
    if expected_action == "SUGGEST":
        if action == "SUGGEST":
            if h == truth:
                return 1.0
            if (h == "closed") != (truth == "closed"):
                return -1.0
            if D.near(h, truth):
                return 0.6
            return 0.2
        return 0.05 if action == "FLAG" else 0.0
    if expected_action == "FLAG":
        if action == "FLAG":
            return 1.0
        if action == "SUGGEST":
            return 0.3 if h == "closed" else -1.0
        return 0.0
    if action == "SUGGEST":
        if h == shown:
            return -0.1
        if (h == "closed") != (shown == "closed"):
            return -1.0
        return -0.5
    return -0.1 if action == "FLAG" else 0.0


def D_norm(s):
    s = (s if isinstance(s, str) else "").strip().lower().replace(" ", "")
    if s in ("", "closed", "kiinni", "stängt", "stengt"):
        return "closed"
    return s


# ----------------------------------------------------------------------------- distributions
def assemble(r: pd.DataFrame, c: pd.DataFrame, p_state: np.ndarray, p_rank: np.ndarray) -> tuple[pd.DataFrame, np.ndarray]:
    """Combine M2 (state) and M3 (which special hours) into P(T = candidate) and P(other) per row.

    ``c`` must hold every candidate of every row; ``p_rank`` gives P(candidate | special) for special
    candidates (ignored for 'closed' and the regular hours)."""
    c = c.copy()
    rid = c.rid.values
    pc, pr, ps = p_state[rid, 0], p_state[rid, 1], p_state[rid, 2]
    is_cl = c.is_closed.values == 1
    is_rg = (c.is_reg.values == 1) & ~is_cl
    p = np.where(is_cl, pc, np.where(is_rg, pr, ps * p_rank))
    c["p"] = p
    # if the regular hours are 'closed', the regular class folds into closed
    reg_closed = r["reg_closed"].values.astype(bool)
    c.loc[is_cl & reg_closed[rid], "p"] = pc[is_cl & reg_closed[rid]] + pr[is_cl & reg_closed[rid]]
    tot = c.groupby("rid").p.sum().reindex(r.rid.values).fillna(0).values
    p_other = np.clip(1.0 - tot, 0.0, 1.0)
    # rows whose regular hours are missing as a candidate (never happens, but keep mass consistent)
    return c, p_other


def expected_points(r: pd.DataFrame, c: pd.DataFrame, p_other: np.ndarray, p_cfg: np.ndarray | None = None,
                    other_near: float = 0.25) -> pd.DataFrame:
    """EP of suggesting each candidate (columns: rid, hours, p, ep, flip)."""
    shown = r.set_index("rid")["shown"]
    c = c.copy()
    c["shown"] = shown.reindex(c.rid.values).values
    c["h_closed"] = c.hours.values == "closed"
    pairs = c[["rid", "hours", "h_closed", "c_open", "c_close", "shown"]].merge(
        c[["rid", "hours", "p", "c_open", "c_close"]].rename(columns={"hours": "t", "p": "pt", "c_open": "t_open",
                                                                      "c_close": "t_close"}), on="rid")
    t_closed = pairs.t.values == "closed"
    h_closed = pairs.h_closed.values
    t_is_shown = pairs.t.values == pairs.shown.values
    s_closed = pairs.shown.values == "closed"
    exact = pairs.hours.values == pairs.t.values
    nr = (~h_closed) & (~t_closed) & (np.abs(pairs.c_open.values - pairs.t_open.values) <= 30) & \
         (np.abs(pairs.c_close.values - pairs.t_close.values) <= 30)
    pts = np.where(exact, 1.0, np.where(h_closed != t_closed, -1.0, np.where(nr, 0.6, 0.2)))
    pts_shown = np.where(pairs.hours.values == pairs.shown.values, -0.1, np.where(h_closed != s_closed, -1.0, -0.5))
    pts = np.where(t_is_shown, pts_shown, pts)
    pairs["w"] = pairs.pt.values * pts
    ep = pairs.groupby(["rid", "hours"], sort=False).w.sum()
    c = c.merge(ep.rename("ep_known").reset_index(), on=["rid", "hours"], how="left")
    po = p_other[c.rid.values]
    c["ep"] = c.ep_known.values + po * np.where(c.h_closed.values, -1.0, 0.2 + 0.4 * other_near)
    if p_cfg is not None:
        pf = p_cfg[c.rid.values]
        c["ep"] = (1 - pf) * c["ep"].values + pf * np.where(c.h_closed.values, 0.3, -1.0)
    c["flip"] = c.h_closed.values != (c.shown.values == "closed")
    own = np.zeros(len(c), dtype=bool)
    for k in ("c_own1", "c_own2", "c_cur"):
        if k in c:
            own |= c[k].values == 1
    c["own"] = own
    return c


def decide(r: pd.DataFrame, c_ep: pd.DataFrame, p_other: np.ndarray, p_cfg: np.ndarray | None = None,
           lam=0.0, min_ep: float = 0.0, allow_soft_flag: bool = True, cfg_flag_threshold: float = 0.5
           ) -> pd.DataFrame:
    """Pick SUGGEST/NO_ACTION/FLAG per row. Returns rid, action, hours, prob, ep, ep_flag.

    ``lam`` is the flip margin: a float, or a pair (lam_own, lam_inferred) where lam_own applies when the
    owner's own past entries (or an entry for this date) back the suggested state and lam_inferred otherwise."""
    n = len(r)
    p_cfg = np.zeros(n) if p_cfg is None else p_cfg
    lam_own, lam_inf = (lam, lam) if np.isscalar(lam) else lam
    c = c_ep[c_ep.hours.values != c_ep.shown.values].copy()
    if "rejected_hours" in r:
        # B5: never re-suggest hours the owner rejected for this holiday last season
        rej = r.set_index("rid")["rejected_hours"].reindex(c.rid.values).values
        c = c[c.hours.values != rej]
    pen = np.where(c["own"].values, lam_own, lam_inf) if "own" in c else np.full(len(c), lam_inf)
    if "rejected_inferred" in r:
        # B5: an owner who declined a peer / shopping-centre suggestion gets no inferred open/closed flips
        ri = r.set_index("rid")["rejected_inferred"].reindex(c.rid.values).fillna(False).values.astype(bool)
        pen = np.where(ri & ~c["own"].values, 9.0, pen)
    c["score"] = c["ep"].values - pen * c["flip"].values
    best = c.sort_values(["rid", "score"], ascending=[True, False]).drop_duplicates("rid").set_index("rid")
    p_shown = c_ep[c_ep.hours.values == c_ep.shown.values].groupby("rid").p.sum().reindex(r.rid.values).fillna(0).values
    ep_flag = p_cfg + (1 - p_cfg) * (0.05 * (1 - p_shown) - 0.1 * p_shown)
    if not allow_soft_flag:
        ep_flag = np.where(p_cfg >= cfg_flag_threshold, ep_flag, -np.inf)
    b_score = best["score"].reindex(r.rid.values).fillna(-np.inf).values
    b_hours = best["hours"].reindex(r.rid.values).values
    b_p = best["p"].reindex(r.rid.values).values
    b_ep = best["ep"].reindex(r.rid.values).values
    action = np.full(n, "NO_ACTION", dtype=object)
    sug = (b_score > min_ep) & (b_score >= ep_flag)
    action[sug] = "SUGGEST"
    flag = (~sug) & (ep_flag > 0)
    action[flag] = "FLAG"
    # hard rules: never contradict the owner's own entry for this date; leave closed listings alone
    hard = (r["cur_state"].values >= 0)
    if "listing_closed" in r:
        hard |= r["listing_closed"].values.astype(bool)
    action[hard & (action == "SUGGEST")] = "NO_ACTION"
    out = pd.DataFrame({"rid": r.rid.values, "action": action,
                        "hours": np.where(action == "SUGGEST", b_hours, None),
                        "prob": np.where(action == "SUGGEST", b_p, np.nan), "ep": np.where(sug, b_ep, 0.0),
                        "ep_flag": ep_flag, "p_shown": p_shown, "best_hours": b_hours, "best_ep": b_ep,
                        "best_p": b_p})
    return out


def score_rows(dec: pd.DataFrame, lab: pd.DataFrame) -> dict:
    """Score decisions against labels with the official rules (same numbers as evaluate.py)."""
    pts = [points(a, h, t, s, e) for a, h, t, s, e in zip(dec.action.values, dec.hours.values, lab.truth_hours.values,
                                                         lab.displayed_hours_at_snapshot.values,
                                                         lab.expected_action.values)]
    pts = np.array(pts)
    mx = (lab.expected_action.values != "NO_ACTION").sum()
    sug = dec.action.values == "SUGGEST"
    h = np.array([D_norm(x) for x in dec.hours.values])
    truth = lab.truth_hours.values
    shown = lab.displayed_hours_at_snapshot.values
    exp = lab.expected_action.values
    harm = sug & (((exp == "SUGGEST") & ((h == "closed") != (truth == "closed"))) |
                  ((exp == "NO_ACTION") & (h != shown) & ((h == "closed") != (shown == "closed"))))
    exact = sug & (exp == "SUGGEST") & (h == truth)
    return {"score": 100 * pts.sum() / mx, "points": pts.sum(), "harmful": int(harm.sum()),
            "suggestions": int(sug.sum()), "precision_exact": exact.sum() / max(1, sug.sum()),
            "recall_exact": exact.sum() / max(1, (exp == "SUGGEST").sum()), "flags": int((dec.action == "FLAG").sum()),
            "row_points": pts, "row_harm": harm}


# ----------------------------------------------------------------------------- calibration (A4.1)
class Calibrator:
    """Isotonic maps for P(T = candidate), one for 'closed' and one for open hours, then renormalised per row."""

    def __init__(self):
        from sklearn.isotonic import IsotonicRegression
        self._iso = {k: IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0) for k in ("closed", "open")}

    def fit(self, c: pd.DataFrame, y: np.ndarray):
        m = c.is_closed.values == 1
        self._iso["closed"].fit(c.p.values[m], y[m])
        self._iso["open"].fit(c.p.values[~m], y[~m])
        return self

    def transform(self, c: pd.DataFrame, n_rows: int) -> tuple[pd.DataFrame, np.ndarray]:
        c = c.copy()
        m = c.is_closed.values == 1
        p = np.empty(len(c))
        p[m] = self._iso["closed"].predict(c.p.values[m])
        p[~m] = self._iso["open"].predict(c.p.values[~m])
        tot = np.bincount(c.rid.values, weights=p, minlength=n_rows)
        scale = np.maximum(tot, 1.0)
        c["p"] = p / scale[c.rid.values]
        p_other = np.clip(1 - np.bincount(c.rid.values, weights=c.p.values, minlength=n_rows), 0, 1)
        return c, p_other


def harmful_cap(n_rows: int, base_harm: int = 224, base_rows: int = 11100) -> float:
    return base_harm * n_rows / base_rows
