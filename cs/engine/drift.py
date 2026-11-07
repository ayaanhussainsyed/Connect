"""M6: drift detector (A13). Weekly hours that changed but were never updated on the listing.

Signals, each filtered to data stamped before ``as_of``:
1. **Drift reviews** that state the new week ("Now only open Mon–Fri 11:00–18:00, Sat 11:00–16:00",
   "Nykyään auki vain ma–pe 8–16, la 10–15", "Numera öppet bara mån–fre 10–18", "Nå bare åpent man–fre 8–18").
   Parsed into a full week; days not mentioned are closed.
2. **Map-user edits to weekly hours** (publisher suggestions with weekday fields): votes per (weekday, hours) that
   differ from the published hours. About half of all user edits are noise, so one lone edit is weak evidence.
3. **Regular-hours history:** how long the published week has stood unchanged.

Output per business: P(outdated) from a small logistic regression (trained on ``listing_is_outdated`` with
cross-validation), the proposed week (the parsed review if there is one, else the published week with the
best-supported user-edit values) and the evidence.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from . import data as D

# ----------------------------------------------------------------------------- review parsing
PREFIXES = [r"now only open", r"nykyään auki vain", r"numera öppet bara", r"nå bare åpent"]
DAY_ABBR = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "ma": 0, "ti": 1, "ke": 2, "to": 3, "pe": 4, "la": 5, "su": 6,
    "mån": 0, "tis": 1, "ons": 2, "tor": 3, "fre": 4, "lör": 5, "sön": 6,
    "man": 0, "tir": 1, "lør": 5, "søn": 6,
}
_PREFIX_RE = re.compile(r"(" + "|".join(PREFIXES) + r")\s+(.*)", re.I | re.S)
_TIME = r"\d{1,2}(?:[:.]\d{2})?"
_RANGE_RE = re.compile(rf"({_TIME})\s*[–-]\s*({_TIME})")
_DAYSPEC_RE = re.compile(r"^([a-zåäöø]+)(?:\s*[–-]\s*([a-zåäöø]+))?\s+(.*)$", re.I)


def _t(s: str) -> str:
    s = s.replace(".", ":")
    if ":" in s:
        h, m = s.split(":")
    else:
        h, m = s, "00"
    return f"{int(h):02d}:{int(m):02d}"


def parse_week_statement(text: str) -> dict | None:
    """'Now only open Mon–Fri 11:00–18:00, Sat 11:00–16:00.' -> {'monday': '11:00-18:00', ..., 'sunday': 'closed'}."""
    m = _PREFIX_RE.search(text or "")
    if not m:
        return None
    body = m.group(2)
    body = re.split(r",\s*(päivittäkää|uppdatera|oppdater|please)", body, flags=re.I)[0]
    body = re.sub(r"\.\s*$", "", body.strip())
    week: dict[int, str] = {}
    for seg in re.split(r",\s*", body):
        dm = _DAYSPEC_RE.match(seg.strip())
        if not dm:
            continue
        a, b, times = dm.group(1).lower(), (dm.group(2) or "").lower(), dm.group(3)
        if a not in DAY_ABBR or (b and b not in DAY_ABBR):
            continue
        days = range(DAY_ABBR[a], DAY_ABBR[b] + 1) if b else [DAY_ABBR[a]]
        ivs = [f"{_t(x)}-{_t(y)}" for x, y in _RANGE_RE.findall(times)]
        if not ivs:
            continue
        for d in days:
            week[d] = ",".join(ivs)
    if not week:
        return None
    return {D.DAYS[i]: week.get(i, "closed") for i in range(7)}


# ----------------------------------------------------------------------------- signals
def published_weeks(as_of: str) -> dict[str, dict]:
    ents = D.entities()
    out = {}
    for e in ents.entity_id:
        rows = pd.DataFrame({"entity_id": [e] * 7, "date": pd.date_range("2026-11-09", periods=7).strftime("%Y-%m-%d")})
        out[e] = dict(zip(D.DAYS, D.regular_hours_for(rows, as_of).values))
    return out


def signals(as_of: str, entity_ids=None) -> tuple[pd.DataFrame, dict, dict]:
    """Feature table per business + proposed weeks + evidence lists."""
    ents = D.entities()
    ents = ents[ents.role.isna()]
    ids = list(ents.entity_id) if entity_ids is None else list(entity_ids)
    pub = published_weeks(as_of)
    t = pd.Timestamp(as_of)

    R = D.reviews()
    R = R[(R.ts < t) & R.entity_id.isin(ids)]
    R = R[R.content.str.contains("|".join(PREFIXES), case=False, regex=True)].sort_values("ts")
    rev_week, rev_ev, n_rev = {}, {}, {}
    for _, rv in R.iterrows():
        wk = parse_week_statement(rv.content)
        if wk is None:
            continue
        e = rv.entity_id
        differs = any(wk[d] != pub[e][d] for d in D.DAYS)
        n_rev[e] = n_rev.get(e, 0) + int(differs)
        if differs:
            rev_week[e] = wk          # newest review wins (sorted by time)
            rev_ev.setdefault(e, []).append({"source": "REVIEW", "detail": rv.content, "ref": f"reviews:{rv.review_id}",
                                             "weight": 0.9})

    ue = D.user_edits()
    ue = ue[(ue.kind == "WEEKDAY") & (ue.created < as_of) & ue.entity_id.isin(ids)].copy()
    ue["published"] = [pub[e][d] for e, d in zip(ue.entity_id, ue.weekday)]
    ue = ue[ue.hours != ue.published]
    ue["recent"] = ue.created >= (t - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    votes = ue.groupby(["entity_id", "weekday", "hours"]).agg(n=("edit_id", "nunique"), last=("created", "max")).reset_index()
    ue["d_open"] = [D.open_close(h)[0] - D.open_close(p)[0] for h, p in zip(ue.hours, ue.published)]
    ue["d_close"] = [D.open_close(h)[1] - D.open_close(p)[1] for h, p in zip(ue.hours, ue.published)]
    edit_week, edit_ev = {}, {}
    for e, g in ue.groupby("entity_id"):
        edit_week[e] = _week_from_edits(pub[e], g)
    for _, u in ue.iterrows():
        edit_ev.setdefault(u.entity_id, []).append({
            "source": "USER_EDIT", "detail": f"{u.publisher} user suggested {u.weekday} {u.hours} "
                                            f"(listed {u.published}) on {u.created}, {u.status.lower()}",
            "ref": f"publisher_suggestions:{u.edit_id}", "weight": 0.4})

    rh = D.regular_history()
    rh = rh[rh.effective_from <= as_of]
    last_change = rh.groupby("entity_id").effective_from.max()
    n_versions = rh.groupby("entity_id").size()

    f = pd.DataFrame(index=pd.Index(ids, name="entity_id"))
    f["drift_reviews"] = pd.Series(n_rev).reindex(ids).fillna(0).values
    f["edits_differing"] = ue.groupby("entity_id").edit_id.nunique().reindex(ids).fillna(0).values
    f["edits_recent"] = ue[ue.recent].groupby("entity_id").edit_id.nunique().reindex(ids).fillna(0).values
    f["edit_max_votes"] = votes.groupby("entity_id").n.max().reindex(ids).fillna(0).values
    f["edit_days"] = ue.groupby("entity_id").weekday.nunique().reindex(ids).fillna(0).values
    # edits that agree with each other on the shift of the opening or closing time across weekdays
    sh = ue.dropna(subset=["d_open"]).groupby(["entity_id", "d_open", "d_close"]).edit_id.nunique()
    f["edit_shift_agree"] = sh.groupby("entity_id").max().reindex(ids).fillna(0).values
    f["edits_closed_day"] = ue[ue.hours == "closed"].groupby("entity_id").edit_id.nunique().reindex(ids).fillna(0).values
    f["never_updated"] = (last_change.reindex(ids).fillna("0000") == "0000-00-00").astype(float).values
    f["n_versions"] = n_versions.reindex(ids).fillna(1).values

    proposed, evidence = {}, {}
    for e in ids:
        if e in rev_week:
            proposed[e] = rev_week[e]
        elif e in edit_week:
            proposed[e] = edit_week[e]
        else:
            proposed[e] = dict(pub[e])
        evidence[e] = rev_ev.get(e, []) + edit_ev.get(e, [])
    return f, proposed, evidence


def _shift_hours(h: str, do: float, dc: float) -> str:
    i = D.ivs(h)
    if not i:
        return h
    a, b = int(i[0][0] + do), int(i[-1][1] + dc)
    if len(i) > 1 or b <= a:
        return h
    fmt = lambda m: "23:59" if m == 1439 else f"{(m % 1440) // 60:02d}:{m % 60:02d}"  # noqa: E731
    return f"{fmt(a)}-{fmt(b)}"


def _week_from_edits(pub: dict, g: pd.DataFrame) -> dict:
    """Proposed week from one business's differing user edits.

    Drift in this data is usually one rule for the whole week (open an hour later, close an hour earlier) or one day
    that became closed. So the best-supported edit pattern is generalised: a time shift is applied to every open day,
    a 'closed' edit only to its own weekday."""
    wk = dict(pub)
    closed = g[g.hours == "closed"].groupby("weekday").edit_id.nunique()
    shifts = g[g.hours != "closed"].dropna(subset=["d_open", "d_close"])
    shifts = shifts.groupby(["d_open", "d_close"]).agg(n=("edit_id", "nunique"), last=("created", "max"))
    best_shift = shifts.sort_values(["n", "last"], ascending=False).head(1)
    n_shift = int(best_shift.n.iloc[0]) if len(best_shift) else 0
    if len(closed) and closed.max() >= n_shift:
        wk[closed.idxmax()] = "closed"
    elif n_shift:
        do, dc = best_shift.index[0]
        for d in D.DAYS:
            if wk[d] != "closed":
                wk[d] = _shift_hours(wk[d], do, dc)
    return wk


FEATURES = ["drift_reviews", "edits_differing", "edits_recent", "edit_max_votes", "edit_days", "edit_shift_agree",
            "edits_closed_day", "never_updated", "n_versions"]


def make_model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))


def true_weeks() -> dict[str, dict]:
    rt = D.regular_truth()
    return {e: {d: D.day_str(json.loads(w).get(d)) for d in D.DAYS} for e, w in zip(rt.entity_id, rt.true_regular_hours_now)}
