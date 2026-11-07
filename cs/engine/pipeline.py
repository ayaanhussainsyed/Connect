"""Season-level orchestration: build frames, train M1-M3, predict distributions, cross-validate.

Honesty rules implemented here:
- features for a season use only data stamped before its snapshot (features.make_context);
- M1, link weights, M2 and M3 are fitted on dev truth from seasons *before* the target season;
- dev scores come from business-grouped folds: a business's rows are predicted by models that never
  saw that business.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import data as D
from . import features as F
from .decide import assemble, decide, expected_points
from .flags import p_closed_for_good, signals
from .habits import HabitModel, entry_observations, truth_observations
from .ranker import Ranker, special_candidates
from .state_model import StateModel, state_labels

K_TYPES = 10

# Training periods (dev truth) used for each scored season: winter seasons plus non-winter holiday periods,
# all ending before the target season's snapshot.
TRAIN_PERIODS = {
    2026: ("w2023", "sp2024", "sp2025", "sp2026", "au2024", "au2025", "au2026", 2024, 2025),
    # the final model for the test businesses also learns from the dev businesses' 2026 season
    "final2026": ("w2023", "sp2024", "sp2025", "sp2026", "au2024", "au2025", "au2026", 2024, 2025, 2026),
    2025: ("w2023", "sp2024", "sp2025", "au2024", "au2025", 2024),
}

# Ablations: (row feature columns blanked, candidate sources removed)
ABLATIONS = {
    "no_m1": (["m1_closed", "m1_regular", "m1_special"], []),
    "no_linked": ([f"lk_{k}" for k in F.LINK_KEYS], ["c_link"]),
    "no_own2": (["own2_state", "own2_eq_reg"], ["c_own2"]),
    "no_m1_linked": (["m1_closed", "m1_regular", "m1_special"] + [f"lk_{k}" for k in F.LINK_KEYS], ["c_link"]),
    "no_other_holidays": (["m1_closed", "m1_regular", "m1_special", "share_closed", "share_special", "n_all",
                           "n_entries_prev", "n_entries_cur"] + [f"lk_{k}" for k in F.LINK_KEYS], ["c_link"]),
    "no_own_history": (["own1_state", "own2_state", "own1_eq_reg", "own2_eq_reg", "m1_closed", "m1_regular",
                        "m1_special", "n_entries_prev", "n_all", "share_closed", "share_special"]
                       + [f"lk_{k}" for k in F.LINK_KEYS], ["c_own1", "c_own2", "c_link"]),
    "no_peers": (["peert_closed", "peert_special", "peert_regular", "peert_closed_among", "peert_n", "peers_closed",
                  "peers_special", "peers_regular", "peers_closed_among", "peers_n", "peer_mode_share"], ["c_peer"]),
    "no_mall": (["mall_state", "in_mall"], ["c_mall"]),
    "no_edits_reviews": (["ue_now_state", "ue_prev_state", "ue_prev_accepted", "rv_closed_prev", "rv_closed_total"],
                         ["c_ue_now", "c_ue_prev"]),
    "no_events": (["event_end_min", "event_att", "in_school_break"], ["c_event"]),
}


def ablate(r: pd.DataFrame, c: pd.DataFrame, name: str | None):
    """Blank a signal group in both frames (rows keep their columns; candidates only from removed sources go)."""
    if not name:
        return r, c
    cols, srcs = ABLATIONS[name]
    r = r.copy()
    for col in cols:
        if col in r:
            r[col] = -1 if col.endswith("_state") or col.startswith("lk_") else np.nan
    if srcs:
        c = c.copy()
        for sname in srcs:
            c[sname] = 0.0
        keep = c[F.SRC].sum(axis=1) > 0
        for sname in srcs:
            if sname == "c_link":
                for k in ["link_n", "link_max", "link_sum", "link_dec_max", "link_age_min"]:
                    c[k] = np.nan if k != "link_n" else 0
        c = c[keep].copy()
        c["n_src"] = c[F.SRC].sum(axis=1)
    return r, c


def add_m1(r: pd.DataFrame, hm: HabitModel, ctx: F.Context) -> pd.DataFrame:
    obs = entry_observations(ctx.entries)
    ids = list(pd.unique(r.entity_id))
    R = hm.posterior_batch(obs, ids)
    pos = {e: i for i, e in enumerate(ids)}
    Rr = R[r.entity_id.map(pos).values]
    dp = hm.day_probs_batch(Rr, r.day_key.values)
    r = r.copy()
    r["m1_closed"], r["m1_regular"], r["m1_special"] = dp[:, 0], dp[:, 1], dp[:, 2]
    r["m1_entropy"] = -(Rr * np.log(np.clip(Rr, 1e-12, 1))).sum(1)
    return r


@dataclass
class Fitted:
    hm: HabitModel
    lw: dict
    sm: StateModel
    rk: Ranker
    train_seasons: tuple
    as_of_fit: str


def season_frames(rows: pd.DataFrame, season, hm: HabitModel, lw: dict, exclude_sources=(), mask_entities=None,
                  shown=None, ablation=None):
    ctx = F.make_context(D.SNAP[season], exclude_sources=exclude_sources, mask_entities=mask_entities,
                         link_weights=lw)
    r, c = F.build(rows, ctx, shown=shown)
    r = add_m1(r, hm, ctx)
    r, c = ablate(r, c, ablation)
    return r, c, ctx


def training_frames(ids, seasons, hm, lw, exclude_sources=(), ablation=None):
    rs, cs = [], []
    off = 0
    for s in seasons:
        t = D.season_truth(s)
        t = t[t.entity_id.isin(set(ids))].reset_index(drop=True)
        r, c, _ = season_frames(t, s, hm, lw, exclude_sources=exclude_sources, ablation=ablation)
        r["truth"] = t.truth_hours.values
        r["season"] = s
        r["rid"] += off
        c["rid"] += off
        off += len(r)
        rs.append(r)
        cs.append(c)
    r = pd.concat(rs, ignore_index=True)
    c = pd.concat(cs, ignore_index=True)
    r["state_y"] = state_labels(r, r.truth.values)
    c["y"] = (c.hours.values == r.set_index("rid").loc[c.rid.values, "truth"].values).astype(int)
    return r, c


def fit_all(ids, train_seasons, as_of_fit: str, extra_truth: pd.DataFrame | None = None, exclude_sources=(),
            K: int = K_TYPES, sm_params=None, rk_params=None, ablation=None) -> Fitted:
    """Fit M1 (+ link weights) on dev truth before ``as_of_fit`` of ``ids``; M2/M3 on the training seasons."""
    th = D.truth_history()
    th = th[(th.date < as_of_fit) & th.entity_id.isin(set(ids))]
    if extra_truth is not None:
        th = pd.concat([th, extra_truth[extra_truth.entity_id.isin(set(ids))]], ignore_index=True)
    hm = HabitModel(K=K).fit(truth_observations(th), n_init=2)
    lw = F.link_weights_from_truth(th, as_of_fit)
    r, c = training_frames(ids, train_seasons, hm, lw, exclude_sources=exclude_sources, ablation=ablation)
    sm = StateModel(params=sm_params).fit(r, r.state_y.values)
    sp = r.rid[r.state_y == 2]
    cs = special_candidates(c[c.rid.isin(set(sp))])
    rk = Ranker(params=rk_params).fit(cs, r, cs.y.values)
    return Fitted(hm=hm, lw=lw, sm=sm, rk=rk, train_seasons=tuple(train_seasons), as_of_fit=as_of_fit)


def predict_dist(fit: Fitted, r: pd.DataFrame, c: pd.DataFrame, sm_cols_drop=None):
    """Distribution over the true hours: candidate probabilities and P(other) per row."""
    p_state = fit.sm.predict_proba(r)
    p_rank = np.zeros(len(c))
    spm = (c.is_closed.values == 0) & (c.is_reg.values == 0)
    if spm.any():
        sub = c[spm]
        p_rank[spm] = fit.rk.predict(sub, r)
    cc, p_other = assemble(r, c, p_state, p_rank)
    return cc, p_other, p_state


def cfg_probs(r: pd.DataFrame, season: int) -> np.ndarray:
    sig = signals(D.SNAP[season], entity_ids=pd.unique(r.entity_id))
    p = p_closed_for_good(sig)
    return p.reindex(r.entity_id.values).fillna(0).values


def run_cv(target_season: int, train_seasons, n_folds: int = 5, seed: int = 0, exclude_sources=(),
           mask_history: bool = False, K: int = K_TYPES, sm_params=None, rk_params=None, rows=None, verbose=True,
           ablation=None):
    """Business-grouped CV on the dev businesses of ``target_season``. Returns per-row OOF frames.

    mask_history=True removes the held-out businesses' own entry history (the no-history evaluation)."""
    lab = D.labels(target_season) if rows is None else rows
    dev_ids = np.array(sorted(lab.entity_id.unique()))
    rng = np.random.default_rng(seed)
    fold_of = dict(zip(dev_ids, rng.integers(0, n_folds, len(dev_ids))))
    as_of = D.SNAP[target_season]
    outs_r, outs_c = [], []
    for k in range(n_folds):
        te = [e for e in dev_ids if fold_of[e] == k]
        tr = [e for e in dev_ids if fold_of[e] != k]
        fit = fit_all(tr, train_seasons, as_of, exclude_sources=exclude_sources, K=K, sm_params=sm_params,
                      rk_params=rk_params, ablation=ablation)
        rows_k = lab[lab.entity_id.isin(set(te))].reset_index(drop=True)
        r, c, _ = season_frames(rows_k, target_season, fit.hm, fit.lw, exclude_sources=exclude_sources,
                                mask_entities=te if mask_history else None,
                                shown=D.displayed(rows_k, as_of).values, ablation=ablation)
        cc, p_other, p_state = predict_dist(fit, r, c)
        r["p_other"] = p_other
        r[["p_closed", "p_regular", "p_special"]] = p_state
        r["fold"] = k
        r["key"] = r.entity_id + "|" + r.date
        cc["key"] = r.set_index("rid").loc[cc.rid.values, "key"].values
        outs_r.append(r)
        outs_c.append(cc)
        if verbose:
            print(f"fold {k}: train {len(tr)} businesses, test {len(te)}", flush=True)
    r = pd.concat(outs_r, ignore_index=True)
    c = pd.concat(outs_c, ignore_index=True)
    # re-index rid globally, in label order
    order = {k: i for i, k in enumerate(lab.entity_id + "|" + lab.date)}
    r["rid_new"] = r.key.map(order)
    c["rid"] = c.key.map(order)
    r["rid"] = r["rid_new"]
    r = r.drop(columns=["rid_new"]).sort_values("rid").reset_index(drop=True)
    c = c.sort_values("rid").reset_index(drop=True)
    r["listing_closed"] = D.entities().set_index("entity_id").listing_closed.reindex(r.entity_id).values \
        if target_season == 2026 else False
    r["p_cfg"] = cfg_probs(r, target_season)
    return r, c


def decide_frame(r, c, lam=0.0, other_near=0.25, allow_soft_flag=True, use_cfg=True, min_ep=0.0):
    p_cfg = r["p_cfg"].values if use_cfg else None
    cep = expected_points(r, c, r["p_other"].values, p_cfg=p_cfg, other_near=other_near)
    return decide(r, cep, r["p_other"].values, p_cfg=p_cfg, lam=lam, allow_soft_flag=allow_soft_flag, min_ep=min_ep), cep


LAM_GRID = tuple((lo, li) for lo in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5) for li in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
                 if li >= lo)


def labels_for(r: pd.DataFrame, lab: pd.DataFrame) -> pd.DataFrame:
    return lab.set_index(lab.entity_id + "|" + lab.date).loc[r.entity_id + "|" + r.date].reset_index(drop=True)


def _subset(r, c, mask_rows):
    keep = np.where(mask_rows)[0]
    new = -np.ones(len(r), dtype=int)
    new[keep] = np.arange(len(keep))
    rs = r.iloc[keep].copy()
    rs["rid"] = np.arange(len(rs))
    cs = c[mask_rows[c.rid.values]].copy()
    cs["rid"] = new[cs.rid.values]
    return rs.reset_index(drop=True), cs.reset_index(drop=True)


# A4: harmful must stay at or below the baseline's 224 on dev; λ is chosen on the other folds with a 5% buffer,
# because the held-out fold's harm varies around the selection folds' value.
HARM_RATE = 0.95 * 224 / 11100


def crossfit_decide(r: pd.DataFrame, c: pd.DataFrame, lab: pd.DataFrame, lam_grid=LAM_GRID, harm_rate=HARM_RATE,
                    calibrate=True, allow_soft_flag=False, use_cfg=True, other_near=0.25, fixed_lam=None,
                    use_feedback=True):
    """Cross-fitted calibration and λ choice: for each fold, the calibrator and λ come from the other folds only.

    λ (a pair: own-history flips, inferred flips) maximises points on the other folds subject to
    harmful <= harm_rate * rows. Returns decisions for all rows (in r order) and the per-fold choices."""
    from .decide import Calibrator, score_rows
    if "rejected_hours" not in r and use_feedback:
        season = int(pd.Timestamp(r.date.min()).year)
        fb = F.pilot_feedback(r, D.SNAP[season])
        r = r.assign(rejected_hours=fb["rejected_hours"].values, rejected_inferred=fb["rejected_inferred"].values)
    if not use_feedback:
        r = r.drop(columns=[k for k in ("rejected_hours", "rejected_inferred") if k in r])
    y = (c.hours.values == lab.truth_hours.values[c.rid.values]).astype(int)
    folds = sorted(r.fold.unique())
    decs, info = [], []
    for k in folds:
        tr = r.fold.values != k
        rk_, ck_ = _subset(r, c, ~tr)
        rt_, ct_ = _subset(r, c, tr)
        labt = lab[tr].reset_index(drop=True)
        if calibrate:
            cal = Calibrator().fit(c[tr[c.rid.values]], y[tr[c.rid.values]])
            ct_, pot = cal.transform(ct_, len(rt_))
            ck_, pok = cal.transform(ck_, len(rk_))
            rt_["p_other"], rk_["p_other"] = pot, pok
        pc_t = rt_["p_cfg"].values if use_cfg else None
        if fixed_lam is None:
            cep_t = expected_points(rt_, ct_, rt_["p_other"].values, p_cfg=pc_t, other_near=other_near)
            best = None
            for lam in lam_grid:
                d = decide(rt_, cep_t, rt_["p_other"].values, p_cfg=pc_t, lam=lam, allow_soft_flag=allow_soft_flag)
                s = score_rows(d, labt)
                ok = s["harmful"] <= harm_rate * len(rt_)
                key = (ok, s["points"] if ok else -s["harmful"])
                if best is None or key > best[0]:
                    best = (key, lam)
            lam = best[1]
        else:
            lam = fixed_lam
        d, _ = decide_frame(rk_, ck_, lam=lam, allow_soft_flag=allow_soft_flag, use_cfg=use_cfg, other_near=other_near)
        d["rid"] = np.where(~tr)[0]
        decs.append(d)
        info.append({"fold": k, "lam": lam})
    dec = pd.concat(decs).sort_values("rid").reset_index(drop=True)
    return dec, pd.DataFrame(info)


def final_fit(train_seasons=(2024, 2025), target_season: int = 2026, K: int = K_TYPES, sm_params=None, rk_params=None):
    """Fit on all dev businesses (for predicting the test businesses of ``target_season``)."""
    lab = D.labels(target_season)
    ids = sorted(lab.entity_id.unique())
    return fit_all(ids, train_seasons, D.SNAP[target_season], K=K, sm_params=sm_params, rk_params=rk_params)


def predict_season(fit: Fitted, calibrator, lam: float, season: int = 2026, rows: pd.DataFrame | None = None,
                   allow_soft_flag: bool = False, other_near: float = 0.25):
    """Distributions and decisions for every target row of ``season``."""
    rows = D.targets(season) if rows is None else rows
    rows = rows.reset_index(drop=True)
    r, c, ctx = season_frames(rows, season, fit.hm, fit.lw)
    cc, p_other, p_state = predict_dist(fit, r, c)
    r["p_other"] = p_other
    r[["p_closed", "p_regular", "p_special"]] = p_state
    if calibrator is not None:
        cc, r["p_other"] = calibrator.transform(cc, len(r))
    r["listing_closed"] = D.entities().set_index("entity_id").listing_closed.reindex(r.entity_id).values \
        if season == 2026 else False
    r["p_cfg"] = cfg_probs(r, season)
    dec, cep = decide_frame(r, cc, lam=lam, allow_soft_flag=allow_soft_flag, other_near=other_near)
    return r, cep, dec
