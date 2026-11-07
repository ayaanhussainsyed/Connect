"""M3: the hours ranker. Given that a day is special (open, not the regular hours), which exact hours?

A gradient-boosted binary model scores each candidate hours string (own past, linked holidays,
shopping centre, peers, user edits, event-extended ...). Scores are normalised within a row so the
probabilities of mutually exclusive candidates sum to at most 1; the rest is "some other hours".
Trained on the dev truth of past seasons (rows whose true state is special).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from .features import SRC
from .state_model import HGB_PARAMS, Vocab

CAND_NUM = SRC + ["link_n", "link_max", "link_sum", "link_dec_max", "link_age_min", "peer_mode_share_c", "shift_open",
                  "shift_close", "c_open",
                  "c_close", "c_len", "d_open", "d_close", "len_ratio", "n_src", "n_intervals"]
ROW_NUM = ["weekday", "reg_open", "reg_close", "reg_len", "own1_state", "own2_state", "n_entries_prev", "n_all",
           "share_special", "in_mall", "m1_special", "cur_state", "event_end_min", "in_school_break"]
ROW_CAT = ["day_key", "segment", "country"]


def special_candidates(c: pd.DataFrame) -> pd.DataFrame:
    return c[(c.is_closed == 0) & (c.is_reg == 0)]


class Ranker:
    def __init__(self, params=None):
        self.params = dict(HGB_PARAMS, **(params or {}))
        self.vocab = Vocab()
        self.iso: IsotonicRegression | None = None
        self.row_num, self.cand_num = list(ROW_NUM), list(CAND_NUM)

    def _X(self, c: pd.DataFrame, r: pd.DataFrame) -> pd.DataFrame:
        rr = r.set_index("rid").loc[c.rid.values]
        rr.index = c.index
        X = self.vocab.transform(rr, ROW_CAT)
        for k in getattr(self, "row_num", ROW_NUM):
            X[k] = pd.to_numeric(rr[k], errors="coerce")
        for k in getattr(self, "cand_num", CAND_NUM):
            X[k] = pd.to_numeric(c[k], errors="coerce") if k in c else np.nan
        return X

    def fit(self, c: pd.DataFrame, r: pd.DataFrame, y: np.ndarray):
        self.vocab.fit(r, ROW_CAT)
        X = self._X(c, r)
        keep = {k for k in X.columns if X[k].nunique(dropna=True) >= 2}
        self.row_num = [k for k in self.row_num if k in keep]
        self.cand_num = [k for k in self.cand_num if k in keep]
        X = self._X(c, r)
        cat = [k in ROW_CAT for k in X.columns]
        self.clf = HistGradientBoostingClassifier(categorical_features=cat, **self.params)
        self.clf.fit(X, y)
        return self

    def raw(self, c: pd.DataFrame, r: pd.DataFrame) -> np.ndarray:
        if len(c) == 0:
            return np.zeros(0)
        return self.clf.predict_proba(self._X(c, r))[:, 1]

    def predict(self, c: pd.DataFrame, r: pd.DataFrame) -> np.ndarray:
        """P(candidate is the true hours | special day), normalised within each row (sum <= 1)."""
        s = self.raw(c, r)
        if self.iso is not None and len(s):
            s = self.iso.predict(s)
        tot = pd.Series(s, index=c.index).groupby(c.rid.values).transform("sum").values
        return s / np.maximum(tot, 1.0)

    def calibrate(self, scores: np.ndarray, y: np.ndarray):
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(scores, y)
        return self
