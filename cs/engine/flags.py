"""M5: closed-for-good scorer (A5). Rules over three weak signals, each time-filtered to ``as_of``:

1. closure-language reviews (fi / sv / nb / en) in the last 150 days;
2. Google/Apple/Bing user edits that mark the business ``closed``;
3. an analytics collapse: search views in the last 28 days far below the business's own earlier level.

A business closed for good is never messaged: it goes to a person (FLAG on all its rows).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D

PERM_RE = (r"lopettanut|lopetti|pysyvästi|suljettu lopullisesti|ei ole enää|stängt för gott|permanent|har lagt ned|"
           r"lagt ned|stengt for godt|nedlagt|closed down|closed for good|out of business|no longer exists|"
           r"slutat|har stängt")


def signals(as_of: str, entity_ids=None) -> pd.DataFrame:
    ents = D.entities()
    ids = list(ents.entity_id) if entity_ids is None else list(entity_ids)
    out = pd.DataFrame(index=pd.Index(ids, name="entity_id"))
    t = pd.Timestamp(as_of)
    R = D.reviews()
    R = R[(R.ts < t) & (R.ts >= t - pd.Timedelta(days=150))]
    perm = R[R.content.str.lower().str.contains(PERM_RE, regex=True)]
    out["perm_reviews"] = perm.groupby("entity_id").size().reindex(ids).fillna(0).values
    ue = D.user_edits()
    ue = ue[(ue.kind == "CLOSED") & (ue.created < as_of)]
    out["closed_edits"] = ue.groupby("entity_id").size().reindex(ids).fillna(0).values
    out["closed_edits_recent"] = ue[ue.created >= (t - pd.Timedelta(days=180)).strftime("%Y-%m-%d")] \
        .groupby("entity_id").size().reindex(ids).fillna(0).values
    A = D.analytics()
    A = A[(pd.to_datetime(A.date) < t)]
    dts = pd.to_datetime(A.date)
    recent = A[dts >= t - pd.Timedelta(days=30)].groupby("entity_id").GOOGLE_SEARCH_VIEWS.mean()
    before = A[(dts < t - pd.Timedelta(days=30)) & (dts >= t - pd.Timedelta(days=210))] \
        .groupby("entity_id").GOOGLE_SEARCH_VIEWS.mean()
    ratio = (recent / before.clip(lower=0.1)).reindex(ids)
    out["views_ratio"] = ratio.values
    out["collapse"] = (ratio < 0.35).fillna(False).astype(float).values
    lc = ents.set_index("entity_id").listing_closed.reindex(ids).values
    out["listing_closed"] = lc if as_of >= D.SNAP[2026] else np.zeros(len(ids), dtype=bool)
    return out


def p_closed_for_good(sig: pd.DataFrame) -> pd.Series:
    """Rule-based probability. Weights are set by hand and checked on the dev labels (7 examples)."""
    s = (1.6 * (sig.perm_reviews >= 1) + 0.6 * (sig.perm_reviews >= 2) + 1.4 * (sig.closed_edits_recent >= 1)
         + 0.4 * (sig.closed_edits >= 2) + 0.8 * sig.collapse)
    p = 1 / (1 + np.exp(-(s - 2.2) * 2.5))
    p[sig.listing_closed.astype(bool)] = 0.0   # already shown closed: nothing to flag
    return pd.Series(p.values, index=sig.index, name="p_closed_for_good")
