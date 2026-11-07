"""B10: the business memory graph, one small graph per business, stored in MongoDB.

Collections (database ``connect_suggests`` via ``cs.db.get_db()``):
- ``kg_nodes``: {id, entity_id, type, label, props, source, ref, as_of, built_at}
- ``kg_edges``: {id, entity_id, type, src, dst, weight}

Every node carries ``source`` and ``ref`` (where the fact comes from), so an answer can cite it.
Node types: BUSINESS, SPECIAL_DAY, ENTRY, LEARNED_FACT, DECISION, MALL, PEER_GROUP, REVIEW, EVENT.
Edge types: ENTERED, DECIDED, LEARNED, SUPERSEDES, FOR_DAY, IN_MALL, SIMILAR_TO, MENTIONS, LINKED_HOLIDAY, NEAR_EVENT.

Privacy: reviews keep only id, date, rating and a short quote with e-mails, phone numbers and links removed; author
names are never read into the graph. Peer groups are aggregates of at least 10 businesses; no individual peer appears.
Time: only data stamped before ``as_of`` (default: the 2026 snapshot).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from cs.engine import data as D

AS_OF = D.SNAP[2026]
MIN_PEERS = 10
NEAR_KM = 1.0
DEMO_IDS_PREFIX = ("FI-D", "SE-D", "NO-D")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w:.])(?:\+|00)\d[\d\s-]{6,16}\d")
_URL_RE = re.compile(r"https?://\S+")
REVIEW_RE = (r"kiinni|suljettu|stängt|stängd|stengt|closed|ovet|dörren|døra|door|netissä|online|google|"
             r"aukiolo|öppettider|åpningstider|hours|now only open|nykyään auki|numera öppet|nå bare åpent")


def _quote(text: str, n: int = 140) -> str:
    q = _URL_RE.sub("[link]", _PHONE_RE.sub("[phone]", _EMAIL_RE.sub("[email]", text or ""))).strip()
    return q if len(q) <= n else q[: n - 1].rstrip() + "…"


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def linked_weights(min_corr: float = 0.4) -> dict[tuple[str, str], float]:
    """Correlation of 'closed' between day keys, per business and season, measured on the dev truth before 2026."""
    from cs.engine.habits import truth_observations
    th = D.truth_history()
    obs = truth_observations(th[th.date < AS_OF])
    obs["cyc"] = [D.cycle(d) for d in obs.date]
    o = obs.drop_duplicates(["entity_id", "cyc", "day_key"]).assign(closed=lambda x: (x.state == "closed").astype(int))
    pv = o.pivot_table(index=["entity_id", "cyc"], columns="day_key", values="closed")
    corr = pv.corr(min_periods=100)
    out = {}
    for a in corr.index:
        for b in corr.columns:
            if a < b and pd.notna(corr.loc[a, b]) and corr.loc[a, b] >= min_corr:
                out[(a, b)] = round(float(corr.loc[a, b]), 2)
    return out


@lru_cache(maxsize=1)
def _engine_preds() -> pd.DataFrame:
    p = D.ARTIFACTS / "predictions" / "engine_2026.parquet"
    if not p.exists():
        return pd.DataFrame(columns=["entity_id", "date", "action", "hours", "probability", "basis"])
    return pd.read_parquet(p, columns=["entity_id", "date", "action", "hours", "probability", "basis"])


@lru_cache(maxsize=4)
def _entries(as_of: str) -> pd.DataFrame:
    e = D.entries_as_of(as_of).copy()
    e["cyc"] = [D.cycle(d) for d in e.date]
    return e


@lru_cache(maxsize=4)
def _peer_table(as_of: str) -> pd.DataFrame:
    """Per (country, city_tier, segment, day_key, cycle): group size, entries, shares, most common special hours."""
    ents = D.entities()
    biz = ents[ents.role.isna()][["entity_id", "country", "city_tier", "segment"]]
    e = _entries(as_of).merge(biz, on="entity_id")
    e = e.drop_duplicates(["entity_id", "day_key", "cyc"], keep="last")
    e["closed"] = (e.hours == "closed").astype(int)
    e["special"] = (~e.hours.isin(["closed", "REGULAR"])).astype(int)
    keys = ["country", "city_tier", "segment", "day_key", "cyc"]
    g = e.groupby(keys).agg(n_entered=("entity_id", "nunique"), n_closed=("closed", "sum"), n_special=("special", "sum"))
    top = e[e.special == 1].groupby(keys + ["hours"]).size().rename("n").reset_index()
    top = top.sort_values("n", ascending=False).drop_duplicates(keys).set_index(keys)
    g = g.join(top[["hours", "n"]].rename(columns={"hours": "top_hours", "n": "top_n"}))
    size = biz.groupby(["country", "city_tier", "segment"]).size().rename("n_group")
    tier = g.reset_index().merge(size.reset_index(), on=["country", "city_tier", "segment"])
    # country-wide fallback (all city sizes) for groups whose tier has too few entries
    e2 = e.assign(city_tier="ALL")
    g2 = e2.groupby(keys).agg(n_entered=("entity_id", "nunique"), n_closed=("closed", "sum"), n_special=("special", "sum"))
    top2 = e2[e2.special == 1].groupby(keys + ["hours"]).size().rename("n").reset_index()
    top2 = top2.sort_values("n", ascending=False).drop_duplicates(keys).set_index(keys)
    g2 = g2.join(top2[["hours", "n"]].rename(columns={"hours": "top_hours", "n": "top_n"}))
    size2 = biz.groupby(["country", "segment"]).size().rename("n_group")
    allc = g2.reset_index().merge(size2.reset_index(), on=["country", "segment"])
    return pd.concat([tier, allc], ignore_index=True)


class _G:
    def __init__(self, eid: str, as_of: str):
        self.eid, self.as_of, self.nodes, self.edges = eid, as_of, {}, []
        self.built_at = datetime.now(timezone.utc)

    def node(self, ntype: str, key: str, label: str, props: dict, source: str, ref: str) -> str:
        nid = f"{self.eid}:{ntype}:{key}"
        self.nodes[nid] = {"id": nid, "entity_id": self.eid, "type": ntype, "label": label, "props": props,
                           "source": source, "ref": ref, "as_of": self.as_of, "built_at": self.built_at}
        return nid

    def edge(self, etype: str, src: str, dst: str, weight: float = 1.0):
        if src in self.nodes and dst in self.nodes:
            self.edges.append({"id": f"{src}>{etype}>{dst}", "entity_id": self.eid, "type": etype, "src": src,
                               "dst": dst, "weight": float(weight)})


def build_graph(entity_id: str, as_of: str = AS_OF, db=None) -> tuple[list[dict], list[dict]]:
    """Build (but don't store) the graph documents for one business."""
    ents = D.entities().set_index("entity_id")
    if entity_id not in ents.index:
        raise KeyError(entity_id)
    e = ents.loc[entity_id]
    g = _G(entity_id, as_of)
    country = e.country

    # BUSINESS (the name is stored for the app, never sent to an LLM)
    reg = D.regular_at(as_of)
    week = {d: reg.loc[entity_id, d] for d in D.DAYS} if entity_id in reg.index else {}
    biz = g.node("BUSINESS", entity_id, e["name"], {"name": e["name"], "segment": e.segment, "city": e.city,
                                                    "country": country, "city_tier": e.city_tier,
                                                    "customer_since": e.customer_since, "regular_week": week},
                 "entities.json", f"entities.json:{entity_id}")

    # SPECIAL_DAY: the country's calendar (from the first entries in Dec 2023 to the end of the 2026 season)
    cal = D.calendar()
    cal = cal[(cal.country == country) & (cal.date >= "2023-12-01")]
    preds = _engine_preds()
    preds = preds[preds.entity_id == entity_id].set_index("date")
    season_rows = cal[(cal.date >= "2026-11-07") & (cal.date <= "2027-01-06")]
    shown = D.displayed(season_rows.assign(entity_id=entity_id)[["entity_id", "date", "day_key"]], as_of) \
        if as_of >= D.SNAP[2026] and len(season_rows) else pd.Series(dtype=object)
    shown = dict(zip(season_rows.date, shown.values)) if len(shown) else {}
    day_ids: dict[str, str] = {}
    for _, c in cal.iterrows():
        props = {"date": c.date, "day_key": c.day_key, "name_en": c.name_en, "name_local": c.name_local,
                 "day_type": c.day_type, "official_public_holiday": int(c.official_public_holiday),
                 "season": D.cycle(c.date)}
        if c.date in shown:
            props["shown_now"] = shown[c.date]
            props["shown_ref"] = f"display:{entity_id}:{c.date}"
        if c.date in preds.index:
            p = preds.loc[c.date]
            props.update({"suggestion_action": p.action, "suggestion_hours": p.hours,
                          "suggestion_probability": round(float(p.probability), 2), "suggestion_basis": p.basis,
                          "suggestion_ref": f"engine:{entity_id}:{c.date}"})
        day_ids[c.date] = g.node("SPECIAL_DAY", c.date, f"{c.name_en} {c.date}", props, "calendar_special_days.csv",
                                 f"calendar:{country}:{c.date}")
    # LINKED_HOLIDAY between the upcoming season's days
    lw = linked_weights()
    sdays = season_rows[["date", "day_key"]].values.tolist()
    for i, (d1, k1) in enumerate(sdays):
        for d2, k2 in sdays[i + 1:]:
            w = lw.get((min(k1, k2), max(k1, k2)))
            if w and k1 != k2:
                g.edge("LINKED_HOLIDAY", day_ids[d1], day_ids[d2], w)

    # ENTRY: what the business entered before as_of
    ent = _entries(as_of)
    for _, r in ent[ent.entity_id == entity_id].iterrows():
        nid = g.node("ENTRY", r.date, f"entry {r.date}", {"date": r.date, "day_key": r.day_key, "hours": r.hours,
                                                          "entered_at": r.entered_at[:10], "entered_by": r.source},
                     "holiday_hours_history.jsonl", f"holiday_hours_history:{entity_id}:{r.date}")
        g.edge("ENTERED", biz, nid)
        if r.date in day_ids:
            g.edge("FOR_DAY", nid, day_ids[r.date])

    # MALL and its entries
    if isinstance(e.mall_id, str):
        m = ents.loc[e.mall_id]
        mall = g.node("MALL", e.mall_id, m["name"], {"name": m["name"], "city": m.city}, "entities.json",
                      f"entities.json:{e.mall_id}")
        g.edge("IN_MALL", biz, mall)
        for _, r in ent[(ent.entity_id == e.mall_id) & (ent.date >= "2024-11-01")].iterrows():
            nid = g.node("ENTRY", f"{e.mall_id}:{r.date}", f"mall entry {r.date}",
                         {"date": r.date, "day_key": r.day_key, "hours": r.hours, "entered_at": r.entered_at[:10],
                          "entered_by": "SHOPPING_CENTRE"}, "holiday_hours_history.jsonl",
                         f"holiday_hours_history:{e.mall_id}:{r.date}")
            g.edge("ENTERED", mall, nid)
            if r.date in day_ids:
                g.edge("FOR_DAY", nid, day_ids[r.date])

    # PEER_GROUP: aggregates of >= MIN_PEERS similar businesses, previous season, per day key of the upcoming season
    pt = _peer_table(as_of)
    prev_cycle = D.cycle(as_of) - 1
    cand = pt[(pt.country == country) & (pt.segment == e.segment) & (pt.cyc == prev_cycle) &
              (pt.n_group >= MIN_PEERS) & (pt.n_entered >= MIN_PEERS) & pt.city_tier.isin([e.city_tier, "ALL"])]
    # prefer the same city size; fall back to the whole country when that group has fewer than MIN_PEERS entries
    cand = cand.assign(_o=(cand.city_tier == "ALL").astype(int)).sort_values("_o").drop_duplicates("day_key")
    for _, p in cand.iterrows():
        if p.day_key not in set(season_rows.day_key) and p.day_key not in ("EASTER_SUNDAY", "GOOD_FRIDAY", "EASTER_MONDAY",
                                                                            "MIDSUMMER_EVE", "MAY_DAY"):
            continue
        key = f"{country}/{p.city_tier}/{e.segment}/{p.day_key}/{prev_cycle}"
        where = {"ALL": "all city sizes", "capital": "the capital", "large_city": "large cities", "town": "towns",
                 "ski_resort": "ski resorts"}.get(p.city_tier, p.city_tier)
        cats = D._p("data", "categories.csv")
        seg_name = dict(pd.read_csv(cats)[["segment", "name_en"]].values).get(e.segment, e.segment.replace("_", " "))
        props = {"group": f"{seg_name} businesses in {country}, {where}",
                 "n_businesses": int(p.n_group), "day_key": p.day_key, "season": prev_cycle,
                 "n_entered": int(p.n_entered),
                 "share_closed_of_entered": round(p.n_closed / max(1, p.n_entered), 2),
                 "share_special_of_entered": round(p.n_special / max(1, p.n_entered), 2),
                 "top_special_hours": p.top_hours if isinstance(p.top_hours, str) else None,
                 "top_special_share_of_entered": round((p.top_n or 0) / max(1, p.n_entered), 2)
                 if isinstance(p.top_hours, str) else None}
        nid = g.node("PEER_GROUP", key, f"peers {p.day_key}", props, "holiday_hours_history.jsonl (aggregated)",
                     f"peers:{key}")
        g.edge("SIMILAR_TO", biz, nid)
        for d, k in sdays:
            if k == p.day_key:
                g.edge("FOR_DAY", nid, day_ids[d])

    # REVIEW: hours-related reviews before as_of (no author names), MENTIONS -> the special day just before
    R = D.reviews()
    R = R[(R.entity_id == entity_id) & (R.ts < pd.Timestamp(as_of)) & (R.content != "")]
    R = R[R.content.str.lower().str.contains(REVIEW_RE, regex=True)].sort_values("ts").tail(30)
    cal_dates = sorted(day_ids)
    for _, rv in R.iterrows():
        nid = g.node("REVIEW", str(rv.review_id), f"review {rv.day}", {"date": rv.day, "rating": float(rv.rating),
                                                                         "quote": _quote(rv.content)},
                     "reviews.json", f"reviews:{rv.review_id}")
        before = [d for d in cal_dates if d <= rv.day and (pd.Timestamp(rv.day) - pd.Timestamp(d)).days <= 3]
        if before:
            g.edge("MENTIONS", nid, day_ids[before[-1]])

    # EVENT: within NEAR_KM during the upcoming season
    ev = D.events()
    ev = ev[(ev.start >= "2026-11-01") & (ev.start <= "2027-01-07")]
    if pd.notna(e.lat):
        dist = np.array([_km(e.lat, e.lon, la, lo) for la, lo in zip(ev.lat, ev.lon)])
        for (_, x), km in zip(ev.iterrows(), dist):
            if km <= NEAR_KM:
                nid = g.node("EVENT", x.event_id, x.name_en, {"name": x.name_en, "start": x.start, "end": x.end,
                                                              "km": round(float(km), 1),
                                                              "expected_attendance": int(x.attendance or 0)},
                             "local_events.json", f"local_events:{x.event_id}")
                g.edge("NEAR_EVENT", biz, nid, 1.0 / max(km, 0.1))
                d = x.start[:10]
                if d in day_ids:
                    g.edge("FOR_DAY", nid, day_ids[d])

    # DECISION and LEARNED_FACT from MongoDB (what the owner told Connect)
    if db is not None:
        for dcs in db.decisions.find({"entity_id": entity_id}, {"_id": 0}):
            nid = g.node("DECISION", dcs["id"], f"decision {dcs.get('date')}",
                         {"date": dcs.get("date"), "decision": dcs.get("decision"), "final_value": dcs.get("final_value"),
                          "every_year": bool(dcs.get("every_year")), "status": dcs.get("status"), "via": dcs.get("via"),
                          "at": str(dcs.get("at"))[:10]}, "mongo:decisions", f"decisions:{dcs['id']}")
            g.edge("DECIDED", biz, nid)
            if dcs.get("date") in day_ids:
                g.edge("FOR_DAY", nid, day_ids[dcs["date"]])
        facts = list(db.learned_facts.find({"entity_id": entity_id}, {"_id": 0}))
        for f in facts:
            nid = g.node("LEARNED_FACT", f["id"], f"fact {f.get('date')}",
                         {"date": f.get("date"), "day_key": f.get("day_key"), "value": f.get("value"),
                          "season": f.get("season"), "superseded_by": f.get("superseded_by"),
                          "current": not f.get("superseded_by"), "created_at": str(f.get("created_at"))[:10]},
                         "mongo:learned_facts", f"learned_facts:{f['id']}")
            g.edge("LEARNED", biz, nid)
            if f.get("date") in day_ids:
                g.edge("FOR_DAY", nid, day_ids[f["date"]])
        for f in facts:
            if f.get("superseded_by"):
                g.edge("SUPERSEDES", f"{entity_id}:LEARNED_FACT:{f['superseded_by']}", f"{entity_id}:LEARNED_FACT:{f['id']}")
    return list(g.nodes.values()), g.edges


def ensure_indexes(db) -> None:
    db.kg_nodes.create_index("id", unique=True, name="id_unique")
    db.kg_nodes.create_index("entity_id", name="entity")
    db.kg_edges.create_index("entity_id", name="entity")


def build(entity_id: str, as_of: str = AS_OF, db=None) -> dict:
    """Build and store one business's graph (replacing the old one). Returns counts by node and edge type."""
    from cs import db as cdb
    db = db if db is not None else cdb.get_db()
    nodes, edges = build_graph(entity_id, as_of, db=db)
    ensure_indexes(db)
    db.kg_nodes.delete_many({"entity_id": entity_id})
    db.kg_edges.delete_many({"entity_id": entity_id})
    if nodes:
        db.kg_nodes.insert_many([dict(n) for n in nodes])
    if edges:
        db.kg_edges.insert_many([dict(x) for x in edges])
    return {"entity_id": entity_id, "nodes": pd.Series([n["type"] for n in nodes]).value_counts().to_dict(),
            "edges": pd.Series([x["type"] for x in edges]).value_counts().to_dict() if edges else {}}


def demo_ids() -> list[str]:
    ents = D.entities()
    return sorted(e for e in ents.entity_id if e.startswith(DEMO_IDS_PREFIX))


def build_demo(db=None) -> list[dict]:
    return [build(e, db=db) for e in demo_ids()]


def ensure_built(entity_id: str, db=None) -> None:
    """Build on demand if the business has no graph yet."""
    from cs import db as cdb
    db = db if db is not None else cdb.get_db()
    if db.kg_nodes.count_documents({"entity_id": entity_id}, limit=1) == 0:
        build(entity_id, db=db)
