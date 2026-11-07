"""M2: the state model. P(closed / regular hours / special hours) for each business and date.

scikit-learn HistGradientBoostingClassifier (LightGBM is unavailable), trained on the **unbiased dev
truth** of past seasons, with features built at each season's snapshot.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from .features import CAT_COLS, LINK_KEYS, STATES, state_of

NUM_COLS = [
    "weekday", "idx", "official_public_holiday", "reg_closed", "reg_open", "reg_close", "reg_len", "shown_closed",
    "shown_is_reg", "cur_state", "own1_state", "own2_state", "own1_eq_reg", "own2_eq_reg", "n_entries_prev",
    "n_entries_cur", "n_all", "share_closed", "share_special", "in_mall", "mall_state",
    "peert_closed", "peert_special", "peert_regular", "peert_closed_among", "peert_n",
    "peers_closed", "peers_special", "peers_regular", "peers_closed_among", "peers_n", "peer_mode_share",
    "ue_now_state", "ue_prev_state", "ue_prev_accepted", "rv_closed_prev", "rv_closed_total",
    "event_end_min", "event_att", "in_school_break", "customer_years", "seasonal",
    "m1_closed", "m1_regular", "m1_special",
] + [f"lk_{k}" for k in LINK_KEYS]

HGB_PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=0.1,
                  random_state=0)


class Vocab:
    """Fixed integer codes for categorical columns (kept identical between training and prediction)."""

    def __init__(self):
        self.maps: dict[str, dict] = {}

    def fit(self, df: pd.DataFrame, cols):
        for c in cols:
            vals = sorted(str(v) for v in pd.Series(df[c]).dropna().unique())
            self.maps[c] = {v: i for i, v in enumerate(vals)}
        return self

    def transform(self, df: pd.DataFrame, cols) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        for c in cols:
            out[c] = df[c].astype(str).map(self.maps[c]).astype(float)
        return out


def state_labels(r: pd.DataFrame, truth) -> np.ndarray:
    return np.array([STATES.index(state_of(t, g)) for t, g in zip(truth, r["reg"].values)])


class StateModel:
    def __init__(self, num_cols=None, params=None):
        self.num_cols = list(num_cols or NUM_COLS)
        self.params = dict(HGB_PARAMS, **(params or {}))
        self.vocab = Vocab()

    def _X(self, r: pd.DataFrame) -> pd.DataFrame:
        X = self.vocab.transform(r, CAT_COLS)
        for c in self.num_cols:
            X[c] = pd.to_numeric(r[c], errors="coerce") if c in r else np.nan
        return X

    def fit(self, r: pd.DataFrame, y: np.ndarray):
        self.vocab.fit(r, CAT_COLS)
        X = self._X(r)
        # drop columns that carry no information (e.g. blanked in an ablation): HGB cannot bin them
        self.num_cols = [c for c in self.num_cols if X[c].nunique(dropna=True) >= 2]
        X = self._X(r)
        cat = [c in CAT_COLS for c in X.columns]
        self.clf = HistGradientBoostingClassifier(categorical_features=cat, **self.params)
        self.clf.fit(X, y)
        return self

    def predict_proba(self, r: pd.DataFrame) -> np.ndarray:
        p = self.clf.predict_proba(self._X(r))
        out = np.zeros((len(r), 3))
        out[:, self.clf.classes_] = p
        return out
