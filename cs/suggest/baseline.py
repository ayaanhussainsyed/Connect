"""A3 v0 – Fonecta's baseline as Suggestions: copy last year -> shopping centre -> peers,
plus closed-for-good signals.

The logic follows ``examples/baseline.py`` (``Baseline.explain``) step by step, with two
deliberate differences:
- "what Google shows now" comes from **our** ``cs.display`` (100% match with the labels),
  not ``Baseline.displayed`` (which misses 86 dev rows in 2026);
- time-stamped data is read through ``cs.data`` with ``as_of`` (entered before 00:00 on the
  snapshot), and the ``closed`` flag is only trusted from the entities snapshot on.

Order of the rules (as in Fonecta's code):
1. Closed signals (a review in the last 150 days with closure words, or a Google/Apple/Bing
   "closed" edit) and the listing is not already closed -> FLAG.
2. The shopping centre entered special hours for the date -> MALL.
3. Otherwise the business's own entry for the same day_key last season -> OWN_LAST_YEAR
   (an "isRegularHours" entry means this year's regular hours for the date).
4. Otherwise peers: >= 70% of peers that entered something were closed and >= 15% of all
   peers entered closed, and Google does not already show closed -> PEERS ("closed").
SUGGEST if there is a candidate and it differs from what Google shows; else NO_ACTION.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from functools import lru_cache

from cs import data, display
from cs.domain import Evidence, Suggestion, suggestion_id
from cs.hours import REGULAR

MODEL_VERSION = "baseline-v0"
PERM_WORDS = ("lopettanut", "pysyvästi", "stängt för gott", "permanent", "lagt ned", "stengt for godt",
              "closed down")
SIGNAL_WINDOW_DAYS = 150


def cycle(d: date) -> int:
    """Holiday season a date belongs to (Feb–Jan): 2026-01-06 -> 2025."""
    return d.year if d.month >= 2 else d.year - 1


@lru_cache(maxsize=1)
def _dates_by_key() -> dict[tuple[str, str], list[date]]:
    out: dict[tuple[str, str], list[date]] = defaultdict(list)
    for c, d, k in zip(data.calendar()["country"], data.calendar()["date"], data.calendar()["day_key"]):
        out[(c, k)].append(date.fromisoformat(d))
    return dict(out)


@lru_cache(maxsize=1)
def _tiers() -> dict[str, str]:
    return dict(zip(data.cities()["city"], data.cities()["city_tier"]))


@lru_cache(maxsize=1)
def _bench() -> dict[tuple[str, str, str, str], dict]:
    p = data.peers()
    return {(r["country"], r["city_tier"], r["segment"], r["day_key"]): r for r in p.to_dict("records")}


@lru_cache(maxsize=4)
def closed_signals(as_of_iso: str) -> dict[str, tuple[Evidence, ...]]:
    """entity_id -> evidence that the business has closed for good (visible at the snapshot)."""
    cut = data.cutoff_epoch_ms(as_of_iso)
    lo = cut - SIGNAL_WINDOW_DAYS * 86_400_000
    sig: dict[str, list[Evidence]] = defaultdict(list)
    for r in data.reviews(as_of_iso):
        text = (r.get("content") or "")
        if r["publisherDate"] > lo and any(w in text.lower() for w in PERM_WORDS):
            when = date.fromtimestamp(r["publisherDate"] / 1000).isoformat()
            snippet = text if len(text) <= 120 else text[:117] + "…"
            sig[r["locationId"]].append(Evidence(
                "REVIEW", f"Review on {when} ({r.get('publisherId', '')}): \"{snippet}\"",
                f"review:{r['id']}", 1.0))
    for s in data.publisher_suggestions(as_of_iso):
        if s["fieldName"] == "closed":
            sig[s["locationId"]].append(Evidence(
                "USER_EDIT", f"A {s.get('publisherId', '')} user marked the business closed on {s['dateCreated']} "
                             f"(status {s.get('status', '')})",
                f"publisher_suggestion:{s['id']}", 0.8))
    return {k: tuple(v) for k, v in sig.items()}


def _hours_text(h: str) -> str:
    return "regular hours" if h == REGULAR else h


def suggest_row(entity_id: str, d: date | str, day_key: str, as_of: date | str, season: int | None = None) -> Suggestion:
    d, as_of = data.to_date(d), data.to_date(as_of)
    ent = data.entity(entity_id)
    ex = display.explain_shown(entity_id, d, as_of)
    shown = ex["shown"]
    base = dict(entity_id=entity_id, kind="HOLIDAY_HOURS", date=d.isoformat(), day_key=day_key, shown=shown,
                model_version=MODEL_VERSION, season=season if season is not None else data.season_of(as_of),
                id=suggestion_id(MODEL_VERSION, entity_id, d.isoformat()))

    sig = closed_signals(as_of.isoformat()).get(entity_id)
    if sig and not ex["closed_for_good"]:
        return Suggestion(**base, action="FLAG", value=None, basis="CLOSED_SIGNALS", evidence=list(sig[:5]))

    cand: str | None = None
    basis = ""
    evidence: list[Evidence] = []
    country = ent["meta"]["countryCode"]

    mall = ent.get("c_shoppingCenterEntityId")
    if mall:
        entries = data.holiday_entries(mall, d, as_of)
        if entries and entries[-1]["hours"] != REGULAR:
            m = entries[-1]
            cand, basis = m["hours"], "MALL"
            mall_name = data.entities().get(mall, {}).get("name", mall)
            evidence.append(Evidence("MALL", f"{mall_name} entered {m['hours']} for {d.isoformat()} "
                                             f"(on {m['entered_at'][:10]})",
                                     f"holiday_hours_history:{mall}:{d.isoformat()}", 1.0))

    if cand is None:
        prev = [x for x in _dates_by_key().get((country, day_key), []) if cycle(x) == cycle(d) - 1]
        for p in prev:  # as in Fonecta's code, the last matching date with an entry wins
            entries = data.holiday_entries(entity_id, p, as_of)
            if entries:
                last = entries[-1]
                cand = ex["regular"]["hours"] if last["hours"] == REGULAR else last["hours"]
                basis = "OWN_LAST_YEAR"
                evidence = [Evidence("OWN_LAST_YEAR",
                                     f"On {p.isoformat()} ({day_key}) you entered {_hours_text(last['hours'])}"
                                     + (f" -> this year's regular hours {cand}" if last["hours"] == REGULAR else ""),
                                     f"holiday_hours_history:{entity_id}:{p.isoformat()}", 1.0)]

    if cand is None:
        tier = _tiers().get((ent.get("address") or {}).get("city", ""), "")
        b = _bench().get((country, tier, ent.get("c_segment", ""), day_key))
        if b:
            closed_share = b.get("closed_share_among_entered")
            share_closed = b.get("share_entered_closed")
            if (closed_share is not None and not math.isnan(closed_share) and closed_share >= 0.7
                    and share_closed is not None and not math.isnan(share_closed) and share_closed >= 0.15
                    and shown != "closed"):
                cand, basis = "closed", "PEERS"
                evidence = [Evidence("PEERS",
                                     f"{closed_share:.0%} of similar {ent.get('c_segment', '').replace('_', ' ')} "
                                     f"businesses ({country}, {tier}) that entered hours for {day_key} were closed "
                                     f"(n={int(b['n_entity_days'])})",
                                     f"peer_benchmarks:{country}:{tier}:{ent.get('c_segment', '')}:{day_key}",
                                     float(closed_share))]

    if cand is not None and cand != shown:
        return Suggestion(**base, action="SUGGEST", value=cand, basis=basis, evidence=evidence)
    if cand is not None:
        evidence.append(Evidence(basis, f"Google already shows {shown}: nothing to change", None, 0.0))
    return Suggestion(**base, action="NO_ACTION", value=None, basis=basis, evidence=evidence)


def suggestions(season: int = 2026, entity_ids: list[str] | set[str] | None = None,
                as_of: date | str | None = None) -> list[Suggestion]:
    """One Suggestion per target row of the season (optionally only some businesses)."""
    as_of = data.to_date(as_of or data.SNAPSHOT[season])
    t = data.targets(season)
    if entity_ids is not None:
        t = t[t["entity_id"].isin(set(entity_ids))]
    return [suggest_row(e, d, k, as_of, season) for e, d, k in zip(t["entity_id"], t["date"], t["day_key"])]
