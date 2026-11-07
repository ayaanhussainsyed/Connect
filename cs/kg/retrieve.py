"""B10 retrieval: follow the graph's links from the days a question is about. No embeddings.

    resolve_question("FI-D0001", "Mitä tein viime pääsiäisenä?")  -> {"dates": [...], "day_keys": [...], "topics": [...]}
    subgraph("FI-D0001", question_dates=[...], day_keys=[...], hops=2) -> {"nodes": [...], "edges": [...]}

Question rules (fi / sv / nb / en): holiday names -> day keys; explicit dates (26.12., 26/12, 24 Dec) -> dates;
"last / viime / förra / i fjor" -> the most recent past occurrence, otherwise the upcoming one; words for shopping
centre and similar businesses switch on those parts of the graph.
"""
from __future__ import annotations

import re
from collections import deque
from datetime import date

from cs.engine import data as D

AS_OF = D.SNAP[2026]

# holiday words -> day keys (stems; matched case-insensitively)
HOLIDAY_WORDS: list[tuple[str, tuple[str, ...]]] = [
    (r"pääsiäi\w*|påsk\w*|påske\w*|easter", ("GOOD_FRIDAY", "EASTER_SATURDAY", "EASTER_SUNDAY", "EASTER_MONDAY")),
    (r"pitkäperjant\w*|långfredag\w*|langfredag\w*|good\s+friday", ("GOOD_FRIDAY",)),
    (r"kiirastorst\w*|skärtorsdag\w*|skjærtorsdag\w*|maundy", ("MAUNDY_THURSDAY",)),
    (r"aatonaat\w*|lille\s*julaft\w*|lillejulaft\w*", ("DEC23",)),
    (r"jouluaat\w*|julafton\w*|julaften\w*|christmas\s+eve", ("XMAS_EVE",)),
    (r"joulupäiv\w*|juldag\w*|første\s+juledag\w*|christmas\s+day", ("XMAS_DAY",)),
    (r"tapanin\w*|annandag\s+jul\w*|annandagen|andre\s+juledag\w*|boxing\s+day|st\.?\s+stephen", ("BOXING_DAY",)),
    (r"välipäiv\w*|mellandag\w*|romjul\w*|between\s+christmas", ("INTERDAY",)),
    (r"uudenvuodenaat\w*|uuden\s+vuoden\s+aat\w*|nyårsafton\w*|nyttårsaft\w*|new\s+year'?s\s+eve", ("NYE",)),
    (r"uudenvuodenpäiv\w*|uuden\s+vuoden\s+päiv\w*|nyårsdag\w*|nyttårsdag\w*|new\s+year'?s\s+day", ("NEW_YEARS_DAY",)),
    (r"loppiaisaat\w*|trettondagsafton\w*|epiphany\s+eve", ("EPIPHANY_EVE",)),
    (r"loppiai(?!saat)\w*|trettondag(?!safton)\w*|epiphany(?!\s+eve)", ("EPIPHANY",)),
    (r"itsenäisyyspäiv\w*|independence\s+day", ("INDEPENDENCE_DAY",)),
    (r"isänpäiv\w*|fars\s*dag\w*|farsdag\w*|father'?s\s+day", ("FATHERS_DAY",)),
    (r"äitienpäiv\w*|mors\s*dag\w*|morsdag\w*|mother'?s\s+day", ("MOTHERS_DAY",)),
    (r"black\s+friday|musta\w*\s+perjant\w*", ("BLACK_FRIDAY",)),
    (r"lucia\w*", ("LUCIA",)),
    (r"juhannusaat\w*|midsommarafton\w*|sankthansaft\w*|midsummer\s+eve", ("MIDSUMMER_EVE",)),
    (r"juhannu(?!saat)\w*|midsommar(?!afton)\w*|sankthans(?!aft)\w*|midsummer(?!\s+eve)", ("MIDSUMMER_EVE", "MIDSUMMER_DAY")),
    (r"vapu\w*|vappu\w*|valborg\w*|första\s+maj|1\.\s*mai|may\s+day", ("MAY_EVE", "MAY_DAY")),
    (r"helatorst\w*|kristi\s+himmelf\w*|ascension", ("ASCENSION",)),
    (r"helluntai\w*|pingst\w*|pinse\w*|pentecost|whitsun\w*", ("PENTECOST_EVE", "PENTECOST", "WHIT_MONDAY")),
    (r"pyhäinpäiv\w*|alla\s+helgon\w*|all\s+saints", ("ALL_SAINTS",)),
    (r"joulu\w*|jul(?!i)\w*|christmas", ("XMAS_EVE", "XMAS_DAY", "BOXING_DAY")),
]
_HOL = [(re.compile(r"\b(?:" + p + r")", re.I), keys) for p, keys in HOLIDAY_WORDS]
_PAST = re.compile(r"\b(viime\w*|edellis\w*|last|previous|förra|i\s+fjol|i\s+fjor|forrige|ifjor|ifjol)\b", re.I)
_MALL = re.compile(r"kauppakeskus\w*|ostoskesku\w*|köpcent\w*|kjøpesent\w*|shopping\s*(?:centre|center|mall)|\bmall\b", re.I)
_PEERS = re.compile(r"samanlais\w*|muut\s+\w+|vastaav\w*|liknande|andra\s+\w+|lignende|andre\s+\w+|similar|other\s+\w+|peers?|"
                    r"kilpailij\w*|konkurrent\w*|competitor\w*", re.I)
_DATE = re.compile(r"(?<![\d.:/])(\d{1,2})\s*[./]\s*(\d{1,2})\.?(?:\s*[./]?\s*(\d{4}))?(?![\d:])")


def resolve_question(entity_id: str, question: str, as_of: str = AS_OF) -> dict:
    """Dates, day keys and topics a question is about, resolved against the business's calendar."""
    ents = D.entities().set_index("entity_id")
    country = ents.loc[entity_id, "country"]
    cal = D.calendar()
    cal = cal[cal.country == country]
    q = question or ""
    past = bool(_PAST.search(q))
    keys: list[str] = []
    matched_specific = False
    for rx, ks in _HOL:
        if rx.search(q):
            if ks == ("XMAS_EVE", "XMAS_DAY", "BOXING_DAY") and matched_specific:
                continue   # a specific Christmas day was named; don't widen to all three
            keys += [k for k in ks if k not in keys]
            matched_specific = matched_specific or len(ks) <= 2
    dates: list[str] = []
    for m in _DATE.finditer(q):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        cands = cal[(cal.date.str[5:7] == f"{mo:02d}") & (cal.date.str[8:10] == f"{d:02d}")].date.tolist()
        if y:
            cands = [c for c in cands if c.startswith(y)]
        if not cands:   # not a special day: still keep the upcoming occurrence of that day and month
            yr = int(as_of[:4]) + (1 if (mo, d) < (int(as_of[5:7]), int(as_of[8:10])) else 0)
            try:
                cands = [date(int(y) if y else yr, mo, d).isoformat()]
            except ValueError:
                continue
        future = [c for c in cands if c >= as_of]
        pastc = [c for c in cands if c < as_of]
        pick = (pastc[-1:] if past and pastc else future[:1] or pastc[-1:])
        dates += [c for c in pick if c not in dates]
    # day keys -> concrete dates: the most recent past cycle with that key if "last", else the upcoming one
    for k in keys:
        rows = cal[cal.day_key == k].date.tolist()
        if not rows:
            continue
        future = [c for c in rows if c >= as_of]
        pastc = [c for c in rows if c < as_of]
        if past and pastc:
            last_cycle = D.cycle(pastc[-1])
            pick = [c for c in pastc if D.cycle(c) == last_cycle]
        elif future:
            first_cycle = D.cycle(future[0])
            pick = [c for c in future if D.cycle(c) == first_cycle]
        else:
            pick = pastc[-1:]
        dates += [c for c in pick if c not in dates]
    topics = []
    if _MALL.search(q):
        topics.append("MALL")
    if _PEERS.search(q):
        topics.append("PEERS")
    return {"dates": sorted(dates), "day_keys": keys, "topics": topics, "past": past}


def _load(entity_id: str, db) -> tuple[dict, list]:
    nodes = {n["id"]: n for n in db.kg_nodes.find({"entity_id": entity_id}, {"_id": 0})}
    edges = list(db.kg_edges.find({"entity_id": entity_id}, {"_id": 0}))
    return nodes, edges


def subgraph(entity_id: str, question_dates=None, day_keys=None, hops: int = 2, db=None, topics=()) -> dict:
    """Nodes and edges within ``hops`` links of the asked days (plus the business node).

    The business and shopping-centre nodes are hubs: they are included but not expanded, so a question about
    one day doesn't pull in every entry the business ever made. Topic MALL adds the centre and its entries for the
    asked days; topic PEERS keeps the peer groups (otherwise they are kept only when linked to an asked day)."""
    from cs import db as cdb
    from .build import ensure_built
    db = db if db is not None else cdb.get_db()
    ensure_built(entity_id, db=db)
    nodes, edges = _load(entity_id, db)
    biz = f"{entity_id}:BUSINESS:{entity_id}"
    adj: dict[str, list[tuple[str, dict]]] = {}
    for e in edges:
        adj.setdefault(e["src"], []).append((e["dst"], e))
        adj.setdefault(e["dst"], []).append((e["src"], e))
    seeds = set()
    for d in question_dates or []:
        nid = f"{entity_id}:SPECIAL_DAY:{d}"
        if nid in nodes:
            seeds.add(nid)
    for k in day_keys or []:
        if not question_dates:
            seeds |= {n for n, v in nodes.items() if v["type"] == "SPECIAL_DAY" and v["props"].get("day_key") == k}
    hubs = {n for n, v in nodes.items() if v["type"] in ("BUSINESS", "MALL")}
    keep, frontier = set(seeds) | {biz}, deque((s, 0) for s in seeds)
    while frontier:
        n, h = frontier.popleft()
        if h >= hops or n in hubs:
            continue
        for m, e in adj.get(n, []):
            t = nodes.get(m, {}).get("type")
            # a linked holiday is a day of its own: keep it, but don't walk on from it
            if e["type"] == "LINKED_HOLIDAY":
                keep.add(m)
                continue
            if t == "PEER_GROUP" and "PEERS" not in topics and h > 0:
                continue
            if m not in keep:
                keep.add(m)
                frontier.append((m, h + 1))
    if "MALL" in topics:
        keep |= {n for n, v in nodes.items() if v["type"] == "MALL"}
    if "PEERS" in topics and not seeds:
        keep |= {n for n, v in nodes.items() if v["type"] == "PEER_GROUP"}
    sub_edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    order = {"BUSINESS": 0, "SPECIAL_DAY": 1, "LEARNED_FACT": 2, "DECISION": 3, "ENTRY": 4, "MALL": 5, "PEER_GROUP": 6,
             "REVIEW": 7, "EVENT": 8}
    out_nodes = sorted((nodes[n] for n in keep if n in nodes), key=lambda v: (order.get(v["type"], 9), v["id"]))
    return {"nodes": out_nodes, "edges": sub_edges, "seeds": sorted(seeds)}
