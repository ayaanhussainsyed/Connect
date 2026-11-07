"""M1: habit-type mixture (latent-class model fitted with EM).

Each business belongs to one of K hidden "habit types". A type is a table of probabilities
P(state | day_key) over three states: closed, regular hours, special hours. Days are conditionally
independent given the type, so one observed day updates the odds for every other day.

- Fitted on the **unbiased** dev truth (``labels/dev_truth_history.csv``), never on owner entries.
- At prediction time a business's own entries are *partial evidence*: each entered day counts as an
  observation of that day's state (entries are 99% accurate), and a missing entry counts as nothing
  (entries are missing not at random, so absence is not evidence of "regular").
- Repeated observations of one day key (several seasons, or several INTERDAY dates) share a total
  weight of 1, newest first, so habits that repeat every year don't make the posterior overconfident.
- The mixture weights depend on the business segment (shrunk towards the global weights), which gives
  new customers with no history a sensible prior (A12).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D
from .features import STATES, state_of

EXCLUDE_KEYS = ("EVENT", "SCHOOL_BREAK")
DECAY = 0.95  # weight of a fact one more season old (measured in the notebook, section 6: 0.870 / 0.917)


def _logsumexp(a, axis=-1):
    m = a.max(axis=axis, keepdims=True)
    return (m + np.log(np.exp(a - m).sum(axis=axis, keepdims=True))).squeeze(axis)


def truth_observations(truth: pd.DataFrame) -> pd.DataFrame:
    """Dev truth rows -> (entity_id, day_key, date, state) using the regular hours published on that date."""
    t = truth[~truth.day_key.isin(EXCLUDE_KEYS)][["entity_id", "date", "day_key", "truth_hours"]].copy()
    reg = D.regular_history()
    t = t.sort_values("date")
    t["_d"] = pd.to_datetime(t["date"])
    r = reg.copy()
    r["_d"] = pd.to_datetime(r["effective_from"].replace("0000-00-00", "1900-01-01"))
    r = r.sort_values("_d")
    m = pd.merge_asof(t, r[["entity_id", "_d"] + D.DAYS], on="_d", by="entity_id", direction="backward")
    wd = pd.to_datetime(m["date"]).dt.day_of_week.values
    regv = m[D.DAYS].values
    m["reg"] = [regv[i, w] if isinstance(regv[i, w], str) else "closed" for i, w in enumerate(wd)]
    m["state"] = [state_of(h, rg) for h, rg in zip(m.truth_hours, m.reg)]
    return m[["entity_id", "day_key", "date", "state"]]


def entry_observations(entries: pd.DataFrame) -> pd.DataFrame:
    """Context entries (with a 'state' column) -> observations."""
    e = entries[~entries.day_key.isin(EXCLUDE_KEYS)]
    return e[["entity_id", "day_key", "date", "state"]].copy()


class HabitModel:
    def __init__(self, K: int = 6, alpha: float = 0.5, seg_strength: float = 20.0, decay: float = DECAY):
        self.K, self.alpha, self.seg_strength, self.decay = K, alpha, seg_strength, decay
        self.keys: list[str] = []
        self.key_idx: dict[str, int] = {}
        self.evidence: dict[str, np.ndarray] = {}
        self.entity_segment: dict[str, str] = {}
        self.entity_country: dict[str, str] = {}

    # ------------------------------------------------------------------ evidence tensors
    def counts(self, obs: pd.DataFrame, ids=None) -> tuple[list[str], np.ndarray]:
        """Weighted counts C[i, key, state]. Observations of one key share weight 1, newer ones weigh more."""
        o = obs[obs.day_key.isin(self.key_idx)].copy()
        if ids is None:
            ids = sorted(o.entity_id.unique())
        pos = {e: i for i, e in enumerate(ids)}
        o = o[o.entity_id.isin(pos)]
        C = np.zeros((len(ids), len(self.keys), 3))
        if len(o):
            o["cyc"] = [D.cycle(d) for d in o["date"]]
            top = o.groupby(["entity_id", "day_key"]).cyc.transform("max")
            o["w"] = np.power(self.decay, (top - o["cyc"]).values)
            o["w"] = o["w"] / o.groupby(["entity_id", "day_key"]).w.transform("sum")
            ii = o.entity_id.map(pos).values
            kk = o.day_key.map(self.key_idx).values
            ss = o.state.map({s: j for j, s in enumerate(STATES)}).values
            np.add.at(C, (ii, kk, ss), o["w"].values)
        return list(ids), C

    # ------------------------------------------------------------------ fit
    def fit(self, obs: pd.DataFrame, n_iter: int = 200, n_init: int = 3, seed: int = 0, tol: float = 1e-5):
        self.keys = sorted(k for k in obs.day_key.unique() if k not in EXCLUDE_KEYS)
        self.key_idx = {k: i for i, k in enumerate(self.keys)}
        ids, C = self.counts(obs)
        ents = D.entities().set_index("entity_id")
        segs = ents.loc[ids, "segment"].values
        best = None
        rng = np.random.default_rng(seed)
        for init in range(n_init):
            R = rng.dirichlet(np.ones(self.K), size=len(ids))
            prev = -np.inf
            for it in range(n_iter):
                pi, theta = self._m_step(C, R)
                ll_i, R = self._e_step(C, np.log(pi)[None, :].repeat(len(ids), 0), theta)
                ll = ll_i.sum()
                if ll - prev < tol * abs(ll):
                    break
                prev = ll
            if best is None or ll > best[0]:
                best = (ll, pi, theta, R)
        self.loglik_, self.pi, self.theta, R = best
        # segment-specific mixture weights, shrunk towards the global ones
        self.seg_pi = {}
        for s in np.unique(segs):
            m = segs == s
            self.seg_pi[s] = (R[m].sum(0) + self.seg_strength * self.pi) / (m.sum() + self.seg_strength)
        self.train_resp_ = pd.DataFrame(R, index=ids)
        return self

    def _m_step(self, C, R):
        pi = (R.sum(0) + 1.0) / (R.shape[0] + self.K)
        num = np.einsum("ik,ijs->kjs", R, C) + self.alpha
        theta = num / num.sum(-1, keepdims=True)
        return pi, theta

    def _e_step(self, C, logprior, theta):
        L = logprior + np.einsum("ijs,kjs->ik", C, np.log(theta))
        ll = _logsumexp(L, axis=1)
        return ll, np.exp(L - ll[:, None])

    # ------------------------------------------------------------------ inference
    def prior_for(self, ids) -> np.ndarray:
        ents = D.entities().set_index("entity_id")
        segs = ents.reindex(ids)["segment"].values
        return np.array([self.seg_pi.get(s, self.pi) for s in segs])

    def score_ll(self, obs: pd.DataFrame) -> float:
        """Held-out log-likelihood (summed over businesses) of their observations."""
        ids, C = self.counts(obs)
        ll, _ = self._e_step(C, np.log(self.prior_for(ids)), self.theta)
        return float(ll.sum())

    def posterior_batch(self, obs: pd.DataFrame, ids) -> np.ndarray:
        ids, C = self.counts(obs, ids=list(ids))
        _, R = self._e_step(C, np.log(self.prior_for(ids)), self.theta)
        return R

    def day_probs_batch(self, R: np.ndarray, day_keys) -> np.ndarray:
        """R[i] posterior over types, day_keys[i] -> P(state) matrix (n, 3). Unknown keys -> nan."""
        out = np.full((len(R), 3), np.nan)
        kk = np.array([self.key_idx.get(k, -1) for k in day_keys])
        ok = kk >= 0
        out[ok] = np.einsum("ik,iks->is", R[ok], self.theta[:, kk[ok], :].transpose(1, 0, 2))
        return out

    # ------------------------------------------------------------------ the interface used by the app (B1)
    def attach_evidence(self, entries: pd.DataFrame):
        """Store each business's own entry evidence (e.g. the 2026 snapshot) for posterior()/day_probs()."""
        obs = entry_observations(entries)
        ids, C = self.counts(obs)
        self.evidence = {e: C[i] for i, e in enumerate(ids)}
        ents = D.entities().set_index("entity_id")
        self.entity_segment = ents["segment"].to_dict()
        self.entity_country = ents["country"].to_dict()
        return self

    def _key_of(self, entity_id: str, day: str) -> str:
        if day in self.key_idx:
            return day
        cal = D.calendar()
        c = self.entity_country.get(entity_id)
        m = cal[(cal.country == c) & (cal.date == day)]
        if len(m):
            return m.day_key.iloc[0]
        raise KeyError(f"{day!r} is neither a day key nor a special day for {entity_id}")

    def _evidence_with(self, entity_id: str, known: dict | None) -> np.ndarray:
        C = self.evidence.get(entity_id, np.zeros((len(self.keys), 3))).copy()
        for day, st in (known or {}).items():
            k = self._key_of(entity_id, day)
            if k not in self.key_idx or st not in STATES:
                continue
            j = self.key_idx[k]
            C[j] = 0.0                       # a newer answer supersedes older facts for that day
            C[j, STATES.index(st)] = 1.0
        return C

    def posterior(self, entity_id: str, known: dict | None = None) -> np.ndarray:
        """P(habit type | own entries + known answers). ``known``: {day_key or date: state}."""
        C = self._evidence_with(entity_id, known)
        seg = self.entity_segment.get(entity_id)
        prior = self.seg_pi.get(seg, self.pi)
        L = np.log(prior) + np.einsum("js,kjs->k", C, np.log(self.theta))
        L = L - L.max()
        p = np.exp(L)
        return p / p.sum()

    def day_probs(self, entity_id: str, day: str, known: dict | None = None) -> dict[str, float]:
        """{'closed': p, 'regular': p, 'special': p} for one day (day key or ISO date)."""
        k = self._key_of(entity_id, day)
        post = self.posterior(entity_id, known)
        p = post @ self.theta[:, self.key_idx[k], :]
        return {s: float(v) for s, v in zip(STATES, p)}

    def describe_types(self, top: int = 6) -> pd.DataFrame:
        """Plain table: per type, its weight and the share closed / special on the main winter days."""
        rows = []
        show = [k for k in ["XMAS_EVE", "XMAS_DAY", "BOXING_DAY", "NYE", "NEW_YEARS_DAY", "EPIPHANY", "INDEPENDENCE_DAY",
                            "DEC23", "INTERDAY", "GOOD_FRIDAY", "MIDSUMMER_EVE"] if k in self.key_idx]
        for k in range(self.K):
            row = {"type": k, "weight": round(float(self.pi[k]), 3)}
            for d in show:
                p = self.theta[k, self.key_idx[d]]
                row[d] = f"{p[0]:.2f}/{p[1]:.2f}/{p[2]:.2f}"
            rows.append(row)
        return pd.DataFrame(rows).sort_values("weight", ascending=False)


def select_k(obs: pd.DataFrame, Ks=(1, 2, 4, 6, 8, 10), n_folds: int = 5, seed: int = 0, n_init: int = 2):
    """Held-out log-likelihood per business for each K, on business-grouped folds."""
    ids = np.array(sorted(obs.entity_id.unique()))
    rng = np.random.default_rng(seed)
    fold = rng.integers(0, n_folds, len(ids))
    fmap = dict(zip(ids, fold))
    f = obs.entity_id.map(fmap).values
    res = []
    for K in Ks:
        tot = 0.0
        for k in range(n_folds):
            m = HabitModel(K=K).fit(obs[f != k], n_init=n_init, seed=seed + k)
            tot += m.score_ll(obs[f == k])
        res.append({"K": K, "heldout_ll_per_business": tot / len(ids)})
    return pd.DataFrame(res)
