"""Time-respecting features for the score engine.

Every feature for a season is computed from data stamped strictly before that season's snapshot
(``as_of``). The same code builds training rows (past seasons, labelled with the dev truth) and
prediction rows (the target season).

Two frames come out of :func:`build`:
- ``rows``: one row per (entity_id, date) with row-level features (for M2, the state model);
- ``cands``: one row per (row, candidate hours string) with candidate features (for M3, the ranker).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import data as D

STATES = ["closed", "regular", "special"]
STATE_CODE = {"none": -1, "closed": 0, "regular": 1, "special": 2}

# Keys whose own state (most recent entry) is used directly as a row feature (linked holidays).
LINK_KEYS = ["XMAS_EVE", "XMAS_DAY", "BOXING_DAY", "DEC23", "INTERDAY", "NYE", "NEW_YEARS_DAY", "EPIPHANY_EVE",
             "EPIPHANY", "INDEPENDENCE_DAY", "FATHERS_DAY", "BLACK_FRIDAY", "ALL_SAINTS", "GOOD_FRIDAY",
             "EASTER_MONDAY", "MAY_DAY", "MIDSUMMER_EVE", "ASCENSION", "NATIONAL_DAY", "LUCIA"]

CAT_COLS = ["day_key", "country", "segment", "city_tier", "archetype", "day_type"]

# Words that mark a closed-door / wrong-hours review (fi, sv, nb, en).
CLOSED_DOOR_RE = (r"kiinni|suljettu|ovi oli|stängt|stängd|stengt|closed|locked|wrong hours|väärät|fel öppettider|"
                  r"feil åpningstider|aukiolo")


def state_of(h: str, reg: str) -> str:
    if h == "closed":
        return "closed"
    if h == "REGULAR" or h == reg:
        return "regular"
    return "special"


def _cycle_index(dates: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    d = pd.to_datetime(dates)
    cyc = np.where(d.dt.month >= 2, d.dt.year, d.dt.year - 1)
    start = pd.to_datetime(pd.Series(cyc).astype(str) + "-02-01").values
    idx = ((d.values - start) / np.timedelta64(1, "D")).astype(int)
    return cyc, idx


@dataclass
class Context:
    """Everything known at ``as_of`` that features need (cheap to build, reused across calls)."""
    as_of: str
    entries: pd.DataFrame            # latest entry per (entity, date) before as_of, with cyc/idx/reg/state
    ents: pd.DataFrame               # entity table (indexed by entity_id)
    link_weights: dict = field(default_factory=dict)
    mask_entities: frozenset = frozenset()


def make_context(as_of: str, exclude_sources: tuple[str, ...] = (), mask_entities=None,
                 link_weights: dict | None = None) -> Context:
    E = D.entries_as_of(as_of, exclude_sources).copy()
    if mask_entities:
        E = E[~E.entity_id.isin(set(mask_entities))]
    E["cyc"], E["idx"] = _cycle_index(E["date"])
    E["reg"] = D.regular_hours_for(E, as_of).values
    hrs = np.where(E["hours"].values == "REGULAR", E["reg"].values, E["hours"].values)
    E["hours_res"] = hrs
    E["state"] = [state_of(h, r) for h, r in zip(E["hours"].values, E["reg"].values)]
    ents = D.entities().set_index("entity_id")
    return Context(as_of=as_of, entries=E.reset_index(drop=True), ents=ents,
                   link_weights=link_weights or {}, mask_entities=frozenset(mask_entities or ()))


def link_weights_from_truth(truth: pd.DataFrame, as_of: str) -> dict:
    """P(hours on key k == hours on key k' | both special, same business, same cycle), from the dev truth."""
    t = truth.copy()
    t["reg"] = D.regular_hours_for(t, as_of).values
    t["state"] = [state_of(h, r) for h, r in zip(t.truth_hours.values, t.reg.values)]
    t = t[(t.state == "special") & ~t.day_key.isin(["EVENT", "SCHOOL_BREAK"])]
    t["cyc"], _ = _cycle_index(t["date"])
    t = t.drop_duplicates(["entity_id", "cyc", "day_key"])
    m = t.merge(t, on=["entity_id", "cyc"], suffixes=("", "_2"))
    m = m[m.day_key != m.day_key_2]
    m["same"] = (m.truth_hours == m.truth_hours_2).astype(float)
    g = m.groupby(["day_key", "day_key_2"]).same.agg(["mean", "size"])
    g = g[g["size"] >= 20]
    return {(a, b): float(v) for (a, b), v in g["mean"].items()}


# ----------------------------------------------------------------------------- row features
def _own_same_key(rows: pd.DataFrame, E: pd.DataFrame, lag: int) -> pd.DataFrame:
    """Own entry for the same day key ``lag`` cycles back (nearest date for multi-date keys)."""
    q = rows[["rid", "entity_id", "day_key", "cyc", "idx"]].copy()
    q["cyc"] = q["cyc"] - lag
    m = q.merge(E[["entity_id", "day_key", "cyc", "idx", "hours_res", "state", "date"]],
                on=["entity_id", "day_key", "cyc"], suffixes=("", "_e"))
    m["dist"] = (m["idx"] - m["idx_e"]).abs()
    m = m.sort_values(["rid", "dist"]).drop_duplicates("rid")
    return m.set_index("rid")[["hours_res", "state", "date"]]


def _peer_stats(ctx: Context, rows: pd.DataFrame) -> pd.DataFrame:
    """Leak-free peer statistics from entries in the previous cycle (computed by us, not the pooled file)."""
    E = ctx.entries
    ents = ctx.ents
    biz = ents[ents.role.isna()]
    cal = D.calendar().copy()
    cal["cyc"], _ = _cycle_index(cal["date"])
    ndates = cal.groupby(["country", "day_key", "cyc"]).size().rename("n_dates")
    gsize = biz.groupby(["country", "city_tier", "segment"]).size().rename("n_group")
    gsize2 = biz.groupby(["country", "segment"]).size().rename("n_group2")
    e = E.merge(biz[["country", "city_tier", "segment"]], left_on="entity_id", right_index=True)
    out = []
    for lvl, keys, gs in (("t", ["country", "city_tier", "segment"], gsize), ("s", ["country", "segment"], gsize2)):
        c = e.groupby(keys + ["day_key", "cyc", "state"]).size().unstack("state", fill_value=0)
        for s in STATES:
            if s not in c:
                c[s] = 0
        c = c.reset_index().merge(gs.reset_index(), on=keys).merge(ndates.reset_index(), on=["country", "day_key", "cyc"],
                                                                   how="left")
        c["n_dates"] = c["n_dates"].fillna(1)
        den = (c[gs.name] * c["n_dates"]).clip(lower=1)
        ent = c[STATES].sum(axis=1)
        c[f"peer{lvl}_closed"] = c["closed"] / den
        c[f"peer{lvl}_special"] = c["special"] / den
        c[f"peer{lvl}_regular"] = c["regular"] / den
        c[f"peer{lvl}_closed_among"] = c["closed"] / ent.clip(lower=1)
        c[f"peer{lvl}_n"] = c[gs.name]
        c["cyc"] = c["cyc"] + 1   # stats of cycle c-1 are features for cycle c
        cols = keys + ["day_key", "cyc"] + [f"peer{lvl}_{x}" for x in ("closed", "special", "regular", "closed_among", "n")]
        q = rows[["rid"] + keys + ["day_key", "cyc"]].merge(c[cols], on=keys + ["day_key", "cyc"], how="left")
        out.append(q.set_index("rid").drop(columns=keys + ["day_key", "cyc"]))
    return pd.concat(out, axis=1)


def peer_modes(ctx: Context) -> pd.DataFrame:
    """Most common entered special hours per (country, city_tier, segment, day_key) in each cycle."""
    E = ctx.entries
    biz = ctx.ents[ctx.ents.role.isna()]
    e = E[E.state == "special"].merge(biz[["country", "city_tier", "segment"]], left_on="entity_id", right_index=True)
    g = e.groupby(["country", "city_tier", "segment", "day_key", "cyc", "hours_res"]).size().rename("n").reset_index()
    tot = g.groupby(["country", "city_tier", "segment", "day_key", "cyc"]).n.transform("sum")
    g["share"] = g["n"] / tot
    g = g.sort_values("n", ascending=False).drop_duplicates(["country", "city_tier", "segment", "day_key", "cyc"])
    g["cyc"] = g["cyc"] + 1
    return g.rename(columns={"hours_res": "peer_mode", "share": "peer_mode_share", "n": "peer_mode_n"})


def _user_edit_feats(ctx: Context, rows: pd.DataFrame, prev_dates: pd.Series) -> pd.DataFrame:
    """Google/Apple/Bing user edits (publisher suggestions) created before as_of about the target date or
    about the same holiday last season."""
    ue = D.user_edits()
    ue = ue[(ue.kind == "HOLIDAY") & (ue.created < ctx.as_of)]
    ue = ue.drop_duplicates(["entity_id", "date"], keep="last")[["entity_id", "date", "hours", "status", "publisher"]]
    a = rows[["rid", "entity_id", "date"]].merge(ue, on=["entity_id", "date"], how="left").set_index("rid")
    b = pd.DataFrame({"rid": rows["rid"].values, "entity_id": rows["entity_id"].values,
                      "date": prev_dates.reindex(rows["rid"].values).values})
    b = b.merge(ue, on=["entity_id", "date"], how="left").set_index("rid")
    return pd.DataFrame({"ue_now_hours": a["hours"], "ue_prev_hours": b["hours"],
                         "ue_prev_accepted": (b["status"] == "ACCEPTED").astype(float)}, index=a.index)


def _review_feats(ctx: Context, rows: pd.DataFrame, prev_dates: pd.Series) -> pd.DataFrame:
    """Closed-door / wrong-hours reviews published within 3 days after last season's same holiday."""
    R = D.reviews()
    R = R[(R.ts < pd.Timestamp(ctx.as_of)) & R.content.str.lower().str.contains(CLOSED_DOOR_RE, regex=True)]
    R = R[["entity_id", "ts"]].copy()
    q = pd.DataFrame({"rid": rows["rid"].values, "entity_id": rows["entity_id"].values,
                      "pd": pd.to_datetime(prev_dates.reindex(rows["rid"].values).values)})
    m = q.dropna(subset=["pd"]).merge(R, on="entity_id")
    dd = (m.ts - m.pd).dt.total_seconds() / 86400
    m = m[(dd >= 0) & (dd < 3)]
    n = m.groupby("rid").size()
    allr = R.groupby("entity_id").size()
    return pd.DataFrame({"rv_closed_prev": n.reindex(rows["rid"].values).fillna(0).values,
                         "rv_closed_total": allr.reindex(rows["entity_id"].values).fillna(0).values},
                        index=rows["rid"].values)


def _event_feats(rows: pd.DataFrame) -> pd.DataFrame:
    ev = D.events().set_index("event_id")
    eid = rows["event_id"].fillna("")
    has = eid.isin(ev.index)
    endh = pd.Series(np.nan, index=rows.index)
    att = pd.Series(np.nan, index=rows.index)
    if has.any():
        sub = ev.loc[eid[has].values]
        e_end = pd.to_datetime(sub["end"].values)
        endh[has] = (e_end.hour * 60 + e_end.minute).values
        att[has] = sub["attendance"].values
    sb = D.school_breaks()
    in_break = np.zeros(len(rows), dtype=bool)
    dts = rows["date"].astype(str).values
    ctry = rows["country"].astype(str).values
    for _, b in sb.iterrows():
        in_break |= ((ctry == b["country"]) & (dts >= b["start"]) & (dts <= b["end"]))
    return pd.DataFrame({"event_end_min": endh.values, "event_att": att.values, "in_school_break": in_break.astype(float)},
                        index=rows["rid"].values)


def base_rows(rows: pd.DataFrame, as_of: str) -> pd.DataFrame:
    r = rows[["entity_id", "date", "day_key"] + (["event_id"] if "event_id" in rows else [])].copy()
    if "event_id" not in r:
        r["event_id"] = np.nan
    r = r.reset_index(drop=True)
    r["rid"] = np.arange(len(r))
    ents = D.entities().set_index("entity_id")
    for c in ["country", "segment", "city_tier", "archetype", "mall_id", "customer_since", "reopen_date"]:
        r[c] = ents.loc[r.entity_id, c].values
    cal = D.calendar()[["country", "date", "day_key", "day_type", "official_public_holiday", "day_class"]]
    cal = cal.drop_duplicates(["country", "date", "day_key"])
    r = r.merge(cal, on=["country", "date", "day_key"], how="left")
    r["day_type"] = r["day_type"].fillna(pd.Series(np.where(r.day_key == "EVENT", "LOCAL_EVENT", "SCHOOL_BREAK"),
                                                   index=r.index))
    r["cyc"], r["idx"] = _cycle_index(r["date"])
    r["weekday"] = pd.to_datetime(r["date"]).dt.day_of_week
    r["reg"] = D.regular_hours_for(r, as_of).values
    return r


def build(rows: pd.DataFrame, ctx: Context, shown: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Row features and candidate frame for the given target rows (entity_id, date, day_key[, event_id])."""
    as_of = ctx.as_of
    E = ctx.entries
    r = base_rows(rows, as_of)
    r = r.sort_values("rid").reset_index(drop=True)
    if shown is None:
        shown = D.displayed(r, as_of)
    r["shown"] = np.asarray(shown)
    r["reg_closed"] = (r["reg"] == "closed").astype(float)
    oc = np.array([D.open_close(h) for h in r["reg"].values])
    r["reg_open"], r["reg_close"] = oc[:, 0], oc[:, 1]
    r["reg_len"] = [D.minutes_open(h) for h in r["reg"].values]
    r["shown_closed"] = (r["shown"] == "closed").astype(float)
    r["shown_is_reg"] = (r["shown"] == r["reg"]).astype(float)

    # entry for the target date itself (already entered before as_of)
    cur = r[["rid", "entity_id", "date"]].merge(E[["entity_id", "date", "hours_res", "state", "source"]],
                                                on=["entity_id", "date"], how="left").set_index("rid")
    r["cur_state"] = cur["state"].map(STATE_CODE).fillna(-1).values
    r["cur_hours"] = cur["hours_res"].values

    # own entries for the same key 1 and 2 cycles back
    for lag in (1, 2):
        o = _own_same_key(r, E, lag)
        r[f"own{lag}_hours"] = o["hours_res"].reindex(r.rid).values
        r[f"own{lag}_state"] = o["state"].reindex(r.rid).map(STATE_CODE).fillna(-1).values
        r[f"own{lag}_date"] = o["date"].reindex(r.rid).values
        r[f"own{lag}_eq_reg"] = (r[f"own{lag}_hours"] == r["reg"]).astype(float)
    # the date one cycle back for the same key (for user edits / reviews), even without an entry
    cal = D.calendar().copy()
    cal["cyc"], cal["idx"] = _cycle_index(cal["date"])
    q = r[["rid", "country", "day_key", "cyc", "idx"]].copy()
    q["cyc"] -= 1
    m = q.merge(cal[["country", "day_key", "cyc", "idx", "date"]], on=["country", "day_key", "cyc"], suffixes=("", "_c"))
    m["dist"] = (m.idx - m.idx_c).abs()
    prev_date = m.sort_values(["rid", "dist"]).drop_duplicates("rid").set_index("rid")["date"]

    # business diligence and linked-holiday states (most recent cycle with an entry, before as_of)
    ent_c = E.groupby(["entity_id", "cyc"]).size().unstack(fill_value=0)
    tc = r["cyc"].values
    n_prev = np.zeros(len(r))
    n_cur = np.zeros(len(r))
    if len(ent_c):
        idx_e = ent_c.index.get_indexer(r.entity_id)
        cols = {c: i for i, c in enumerate(ent_c.columns)}
        vals = ent_c.values
        for i, (ie, c) in enumerate(zip(idx_e, tc)):
            if ie >= 0:
                n_prev[i] = vals[ie, cols[c - 1]] if (c - 1) in cols else 0
                n_cur[i] = vals[ie, cols[c]] if c in cols else 0
    r["n_entries_prev"] = n_prev
    r["n_entries_cur"] = n_cur
    tot = E.groupby("entity_id").agg(n_all=("state", "size"), share_closed=("state", lambda s: (s == "closed").mean()),
                                     share_special=("state", lambda s: (s == "special").mean()))
    r = r.join(tot, on="entity_id")
    r["n_all"] = r["n_all"].fillna(0)
    # linked-key states: latest entry state for each link key in cycles c-1 (and c for keys already passed)
    lk = E[E.day_key.isin(LINK_KEYS)][["entity_id", "day_key", "cyc", "state"]]
    lk = lk.drop_duplicates(["entity_id", "day_key", "cyc"], keep="last")
    piv = lk.assign(sc=lk.state.map(STATE_CODE)).pivot_table(index=["entity_id", "cyc"], columns="day_key",
                                                              values="sc", aggfunc="last")
    piv.columns = [f"lk_{c}" for c in piv.columns]
    piv = piv.reset_index()
    piv["cyc"] = piv["cyc"] + 1
    r = r.merge(piv, on=["entity_id", "cyc"], how="left")
    for k in LINK_KEYS:
        c = f"lk_{k}"
        if c not in r:
            r[c] = np.nan
        r[c] = r[c].fillna(-1)

    # shopping centre entry for the date
    mall = r[["rid", "mall_id", "date"]].merge(E[["entity_id", "date", "hours_res", "state"]],
                                               left_on=["mall_id", "date"], right_on=["entity_id", "date"],
                                               how="left").set_index("rid")
    r["in_mall"] = r["mall_id"].notna().astype(float)
    r["mall_hours"] = mall["hours_res"].reindex(r.rid).values
    r["mall_state"] = mall["state"].reindex(r.rid).map(STATE_CODE).fillna(-1).values
    r.loc[r["mall_hours"].notna(), "mall_state"] = [
        STATE_CODE[state_of(h, rg)] for h, rg in zip(r.loc[r["mall_hours"].notna(), "mall_hours"],
                                                     r.loc[r["mall_hours"].notna(), "reg"])]

    # peers
    r = r.join(_peer_stats(ctx, r), on="rid")
    pm = peer_modes(ctx)
    r = r.merge(pm[["country", "city_tier", "segment", "day_key", "cyc", "peer_mode", "peer_mode_share", "peer_mode_n"]],
                on=["country", "city_tier", "segment", "day_key", "cyc"], how="left")

    # user edits, reviews, events
    r = r.join(_user_edit_feats(ctx, r, prev_date), on="rid")
    r["ue_now_state"] = [STATE_CODE[state_of(h, rg)] if isinstance(h, str) else -1
                         for h, rg in zip(r.ue_now_hours, r.reg)]
    r["ue_prev_state"] = [STATE_CODE[state_of(h, rg)] if isinstance(h, str) else -1
                          for h, rg in zip(r.ue_prev_hours, r.reg)]
    r = r.join(_review_feats(ctx, r, prev_date), on="rid")
    r = r.join(_event_feats(r), on="rid")
    cs = pd.to_datetime(r["customer_since"], errors="coerce")
    r["customer_years"] = (pd.Timestamp(as_of) - cs).dt.days / 365.25
    r["seasonal"] = r["reopen_date"].notna().astype(float)
    r = r.sort_values("rid").reset_index(drop=True)
    fb = pilot_feedback(r, as_of)
    r["rejected_hours"], r["rejected_inferred"] = fb["rejected_hours"].values, fb["rejected_inferred"].values
    return r, build_candidates(r, ctx)


# ----------------------------------------------------------------------------- candidates
SRC = ["c_closed", "c_reg", "c_shown", "c_cur", "c_own1", "c_own2", "c_mall", "c_peer", "c_ue_now", "c_ue_prev",
       "c_event", "c_link", "c_shift"]

# Shifted regular hours (open shift, close shift) in minutes: "closes an hour early", "+1 h on Black Friday" ...
SHIFTS = [(0, -60), (0, -120), (0, -180), (0, -240), (0, 60), (60, 0)]


def _shift(h: str, do: int, dc: int) -> str | None:
    i = D.ivs(h)
    if not i or len(i) > 1:
        return None
    a, b = i[0][0] + do, i[0][1] + dc
    if b <= a:
        return None
    return f"{(a % 1440) // 60:02d}:{a % 60:02d}-{(b % 1440) // 60:02d}:{b % 60:02d}"


def _extend_close(h: str, minutes: int) -> str | None:
    i = D.ivs(h)
    if not i:
        return None
    a, b = i[0][0], i[-1][1] + minutes
    return f"{a // 60:02d}:{a % 60:02d}-{(b % 1440) // 60:02d}:{b % 60:02d}"


def build_candidates(r: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    """One row per (rid, distinct hours string). Columns: rid, hours, source flags, candidate features."""
    parts = []

    def add(src, hours, extra=None):
        df = pd.DataFrame({"rid": r["rid"].values, "hours": hours})
        if extra is not None:
            for k, v in extra.items():
                df[k] = v
        df = df[df["hours"].notna() & (df["hours"] != "REGULAR")]
        df[src] = 1.0
        parts.append(df)

    add("c_closed", np.full(len(r), "closed", dtype=object))
    add("c_reg", r["reg"].values)
    add("c_shown", r["shown"].values)
    add("c_cur", r["cur_hours"].values)
    add("c_own1", r["own1_hours"].values)
    add("c_own2", r["own2_hours"].values)
    add("c_mall", r["mall_hours"].values)
    add("c_peer", r["peer_mode"].values, {"peer_mode_share_c": r["peer_mode_share"].values})
    add("c_ue_now", r["ue_now_hours"].values)
    add("c_ue_prev", r["ue_prev_hours"].values)
    ev = r["day_key"].isin(["EVENT"]).values
    ext = [(_extend_close(h, 120) if (e and h != "closed") else None) for h, e in zip(r["reg"].values, ev)]
    add("c_event", np.array(ext, dtype=object))
    for do, dc in SHIFTS:
        sh = np.array([_shift(h, do, dc) for h in r["reg"].values], dtype=object)
        add("c_shift", sh, {"shift_open": float(do), "shift_close": float(dc)})

    # linked holidays: own special entries for other keys in cycles c-2..c (before as_of)
    E = ctx.entries
    le = E[(E.state == "special") & ~E.day_key.isin(["EVENT", "SCHOOL_BREAK"])][["entity_id", "day_key", "cyc", "hours_res"]]
    q = r[["rid", "entity_id", "day_key", "cyc"]].merge(le, on="entity_id", suffixes=("", "_e"))
    q["age"] = q["cyc"] - q["cyc_e"]
    q = q[(q.age >= 0) & (q.age <= 2) & ~((q.day_key == q.day_key_e) & (q.age >= 1))]
    lw = ctx.link_weights
    q["lw"] = [lw.get((a, b), 0.0) for a, b in zip(q.day_key.values, q.day_key_e.values)]
    q["lw_dec"] = q["lw"] * np.power(0.95, q["age"].clip(lower=1) - 1)
    g = q.groupby(["rid", "hours_res"]).agg(link_n=("lw", "size"), link_max=("lw", "max"), link_sum=("lw", "sum"),
                                            link_dec_max=("lw_dec", "max"), link_age_min=("age", "min")).reset_index()
    g = g.rename(columns={"hours_res": "hours"})
    g["c_link"] = 1.0
    parts.append(g)

    c = pd.concat(parts, ignore_index=True)
    agg = {s: "max" for s in SRC if s in c}
    for k in ["link_n", "link_max", "link_sum", "link_dec_max", "link_age_min", "peer_mode_share_c", "shift_open",
              "shift_close"]:
        if k in c:
            agg[k] = "max"
    c = c.groupby(["rid", "hours"], as_index=False).agg(agg)
    for s in SRC:
        if s not in c:
            c[s] = 0.0
        c[s] = c[s].fillna(0.0)
    c["link_n"] = c["link_n"].fillna(0)
    rr = r.set_index("rid")
    reg = rr.loc[c.rid, "reg"].values
    c["is_closed"] = (c["hours"] == "closed").astype(float)
    c["is_reg"] = (c["hours"].values == reg).astype(float)
    oc = np.array([D.open_close(h) for h in c["hours"].values])
    c["c_open"], c["c_close"] = oc[:, 0], oc[:, 1]
    c["c_len"] = [D.minutes_open(h) for h in c["hours"].values]
    c["d_open"] = c["c_open"] - rr.loc[c.rid, "reg_open"].values
    c["d_close"] = c["c_close"] - rr.loc[c.rid, "reg_close"].values
    c["len_ratio"] = c["c_len"] / np.maximum(rr.loc[c.rid, "reg_len"].values, 1)
    c["n_src"] = c[SRC].sum(axis=1)
    c["n_intervals"] = [0 if h == "closed" else h.count(",") + 1 for h in c["hours"].values]
    return c


# ----------------------------------------------------------------------------- learning from declines (B5)
def pilot_feedback(r: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Owner declines known at ``as_of`` (2025 pilot log, decided before as_of):

    - ``rejected_hours``: the hours the owner rejected for this holiday last season (never re-suggested);
    - ``rejected_inferred``: the owner rejected a suggestion based on peers or the shopping centre, which switches
      off inferred (non-own-history) open/closed flips for that owner.
    """
    pl = D.pilot_log()
    pl = pl[(pl.decision == "REJECTED") & pl.decided_at.notna() & (pl.decided_at < as_of)].copy()
    out = pd.DataFrame(index=r.index)
    if pl.empty:
        out["rejected_hours"] = None
        out["rejected_inferred"] = False
        return out
    pl["cyc"] = [D.cycle(d) for d in pl.date]
    rej = pl.drop_duplicates(["entity_id", "day_key", "cyc"], keep="last")[["entity_id", "day_key", "cyc", "suggested_hours"]]
    q = r[["entity_id", "day_key", "cyc"]].assign(cyc=r["cyc"] - 1).merge(rej, on=["entity_id", "day_key", "cyc"], how="left")
    out["rejected_hours"] = q["suggested_hours"].values
    inferred = set(pl[pl.basis.isin(["PEER_MAJORITY", "SHOPPING_CENTRE_HOURS"])].entity_id)
    out["rejected_inferred"] = r["entity_id"].isin(inferred).values
    return out
