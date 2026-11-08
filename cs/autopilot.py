"""B1 autopilot – next season, apply what the owner allowed "every year", then inform with undo.

    run_autopilot("FI-D0012", 2026, target="mock")
    run_autopilot("FI-D0001", 2026, time_machine=True)    # demo only: as if they said "every year" in 2025

For each holiday a standing permission covers:
1. The newest learned fact for that holiday (``learned_facts``; weight 0.95 per season of age, newest
   wins, facts older than 3 seasons are ignored).
2. The agreement check (ML findings): apply only if this season's engine gives those same hours
   P >= 0.5 (``guardrails.engine_p``). Otherwise the date goes onto a normal card and the owner is asked.
3. Dates already shown correctly are skipped; the guardrails (not past, valid, listing open) apply.
4. Write to Yext now. Dates fewer than 14 days away are still applied but logged as "short window"
   (and the note says so).
5. Inform with ``cs.compose.compose_autopilot_note`` ([Undo] [Stop]). The applied dates live on an
   "autopilot card", so Undo works exactly like a normal decision; Stop revokes the permission.

Permissions come from the owner ("Approve + every year"), from the CRM flag
``standing_permission_holidays = 1`` (a scope over PUBLIC_HOLIDAY day types, ``source = CRM_2025``,
with the 2025 pilot decisions as learned facts), or from the demo-only time machine.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date

from pymongo.database import Database

from cs import audit, compose_bridge, data, db as dbmod, display, export, guardrails, learn, links, season
from cs.domain import Card, Decision, LearnedFact, Permission, new_id, utcnow
from cs.hours import REGULAR

log = logging.getLogger(__name__)

DECAY = 0.95
MAX_AGE = 3
MIN_DAYS_AHEAD = 14
CRM_SOURCE = "CRM_2025"
TIME_MACHINE_SOURCE = "TIME_MACHINE_2025"


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def _contact_flag(entity_id: str) -> bool:
    return bool(compose_bridge.contact(entity_id).get("standing_permission"))


def _record_fact_once(entity_id: str, day_key: str, d: str, value: str, season_: int, source: str, db) -> None:
    if db.learned_facts.count_documents({"entity_id": entity_id, "day_key": day_key, "source": source}, limit=1):
        return
    learn.record_fact(entity_id, day_key, d, value, season_, source=source, db=db)


def crm_permission(entity_id: str, db: Database | None = None) -> Permission | None:
    """The CRM standing permission as a scoped Permission (created once), or None if there is none
    or the owner has stopped it. Its learned values are the owner's 2025 pilot decisions."""
    db = _db(db)
    if not _contact_flag(entity_id):
        return None
    found = dbmod.find(Permission, {"entity_id": entity_id, "source": CRM_SOURCE}, db=db, limit=1)
    if found:
        return found[0] if found[0].status in ("GRANTED", "USED") else None
    pl = data.pilot_log(data.SNAPSHOT[2026])
    rows = pl[(pl["entity_id"] == entity_id) & (pl["decision"].isin(["ACCEPTED", "EDITED"]))]
    for r in rows.to_dict("records"):
        value = r["final_hours"] or r["suggested_hours"]
        if value:
            _record_fact_once(entity_id, r["day_key"], r["date"], value, 2025, "PILOT_2025", db)
    scope = {"day_types": ["PUBLIC_HOLIDAY"], "day_keys": [], "values": {}, "min_days_ahead": MIN_DAYS_AHEAD,
             "text": "Public holidays: we set the hours you told us, at least 14 days ahead, and tell you each time."}
    p = Permission(entity_id=entity_id, scope=scope, status="GRANTED", granted_via="crm", source=CRM_SOURCE)
    dbmod.save(p, db=db)
    audit.log("PERMISSION_GRANTED", {"permission_id": p.id, "scope": scope, "source": CRM_SOURCE,
                                     "facts_from_pilot": len(rows)}, entity_id=entity_id, db=db)
    return p


def seed_time_machine(entity_id: str, db: Database | None = None) -> Permission | None:
    """Demo only: pretend the owner pressed "Approve + every year" in the 2025 season, using what
    they actually entered then (holiday_hours_history, 2025 season). Clearly labelled TIME_MACHINE_2025."""
    db = _db(db)
    found = dbmod.find(Permission, {"entity_id": entity_id, "source": TIME_MACHINE_SOURCE,
                                    "status": {"$in": ["GRANTED", "USED"]}}, db=db, limit=1)
    if found:
        return found[0]
    h = data.holiday_history(data.SNAPSHOT[2026])
    h = h[(h["entity_id"] == entity_id) & (h["date"] >= "2025-11-07") & (h["date"] <= "2026-01-06")]
    values: dict[str, str] = {}
    for r in h.sort_values("entered_at").to_dict("records"):
        values[r["day_key"]] = r["hours"]
        _record_fact_once(entity_id, r["day_key"], r["date"], r["hours"], 2025, TIME_MACHINE_SOURCE, db)
    if not values:
        return None
    scope = {"day_keys": sorted(values), "values": values, "min_days_ahead": MIN_DAYS_AHEAD,
             "text": "Demo time machine: the same holidays with the same hours as entered in 2025."}
    p = Permission(entity_id=entity_id, scope=scope, status="GRANTED", granted_via="time-machine",
                   source=TIME_MACHINE_SOURCE)
    dbmod.save(p, db=db)
    audit.log("PERMISSION_GRANTED", {"permission_id": p.id, "scope": scope, "source": TIME_MACHINE_SOURCE,
                                     "demo_only": True}, entity_id=entity_id, db=db)
    return p


def newest_facts(entity_id: str, season_: int, db: Database | None = None) -> dict[str, tuple[LearnedFact, float]]:
    """{day_key: (newest active fact, weight)} with weight = 0.95 ** age; older than 3 seasons dropped."""
    out: dict[str, tuple[LearnedFact, float]] = {}
    for f in learn.active_facts(entity_id, db):
        age = season_ - int(f.season)
        if age < 0 or age > MAX_AGE or not f.day_key:
            continue
        cur = out.get(f.day_key)
        if cur is None or (f.season, f.created_at) > (cur[0].season, cur[0].created_at):
            out[f.day_key] = (f, DECAY ** age)
    return out


def _covers(p: Permission, day_key: str, day_type: str) -> bool:
    sc = p.scope or {}
    return day_key in (sc.get("day_keys") or []) or day_type in (sc.get("day_types") or [])


@audit.batched
def run_autopilot(entity_id: str, season_: int = 2026, *, target: str | None = None, today: date | None = None,
                  engine: str | None = None, db: Database | None = None, client=None, time_machine: bool = False,
                  base_url: str | None = None) -> dict:
    from cs.suggest import engine_adapter

    db = _db(db)
    today = today or date.today()
    engine = engine or ("ml" if engine_adapter.available(season_) else "baseline")
    target = target or season.default_target(entity_id)
    perms = [p for ps in learn.active_permissions(entity_id, db).values() for p in ps]
    if not any(p.source == CRM_SOURCE for p in perms):
        crm = crm_permission(entity_id, db)
        if crm:
            perms.append(crm)
    if time_machine and not any(p.source in (TIME_MACHINE_SOURCE, CRM_SOURCE) for p in perms):
        # demo only, and only for owners without a real standing permission
        tm = seed_time_machine(entity_id, db)
        if tm:
            perms.append(tm)
    audit.log("AUTOPILOT_START", {"season": season_, "summary": "standing permission: " + (
        ", ".join(sorted({p.source for p in perms})) or "none"), "permissions": [p.id for p in perms]},
        entity_id=entity_id, db=db)
    res: dict = {"entity_id": entity_id, "season": season_, "engine": engine, "target": target,
                 "permissions": [{"id": p.id, "source": p.source} for p in perms], "applied": {}, "ask": [],
                 "skipped": {}, "short_window": [], "card_id": None, "followup_card_id": None, "message": None}
    if not perms:
        res["reason"] = "no standing permission"
        return res

    facts = newest_facts(entity_id, season_, db)
    t = data.targets(season_)
    day_type = dict(zip(t.loc[t["entity_id"] == entity_id, "date"], t.loc[t["entity_id"] == entity_id, "day_type"]))
    rows = export.build(season_, engine, today=today, entity_ids=[entity_id])
    flagged = sum(1 for s in rows if s.action == "FLAG")
    if flagged:   # likely closed for good: a Fonecta person checks first, so a standing permission applies nothing
        res["reason"] = "flagged for a Fonecta person (likely closed for good); nothing applied"
        audit.log("AUTOPILOT_SKIP", {"season": season_, "reason": res["reason"], "flagged_dates": flagged},
                  entity_id=entity_id, db=db)
        return res
    ctx = guardrails.make_context(data.SNAPSHOT[season_], today)
    plan = []
    for s in sorted(rows, key=lambda r: r.date):
        perm = next((p for p in perms if _covers(p, s.day_key or "", day_type.get(s.date, ""))), None)
        if perm is None:
            continue
        hit = facts.get(s.day_key or "")
        if hit is None:
            res["skipped"][s.date] = "no learned hours for this holiday"
            continue
        fact, weight = hit
        value = fact.value
        if value not in (REGULAR,) and value.upper() == "SPECIAL":
            res["skipped"][s.date] = "the owner said 'other hours' without hours"
            continue
        shown_value = s.shown
        effective = (display.explain_shown(entity_id, s.date, data.SNAPSHOT[season_])["regular"]["hours"]
                     if value == REGULAR else value)
        if effective == shown_value:
            res["skipped"][s.date] = "already shown correctly"
            continue
        probe = dataclasses.replace(s, action="SUGGEST", value=effective)
        bad = next((f"{r.__name__}: {why}" for r in (guardrails.not_past, guardrails.valid_hours,
                                                      guardrails.listing_active)
                    for ok, why in [r(probe, ctx)] if not ok), None)
        if bad:
            res["skipped"][s.date] = bad
            continue
        p = guardrails.engine_p(s, effective)
        if p is None or p < guardrails.AUTOPILOT_MIN_P:
            why = (f"this season's engine gives {effective} P={p:.2f} < {guardrails.AUTOPILOT_MIN_P}" if p is not None
                   else "this season's engine gives no probability for it")
            res["ask"].append(s.date)
            audit.log("AUTOPILOT_ASK", {"date": s.date, "day_key": s.day_key, "value": effective, "reason": why,
                                        "permission_id": perm.id}, entity_id=entity_id, db=db)
            continue
        plan.append((s, effective, p, perm, fact, weight))

    if plan:
        _apply_plan(entity_id, season_, plan, res, db, client, today, target, engine, base_url)
    if res["ask"]:
        fu = None
        try:
            new = season.build_card(entity_id, season_, engine, target=target, db=db, ask_question=False,
                                    only_dates=res["ask"], today=today, kind="AUTOPILOT_ASK")
            _, token = season.send_card(new, db, base_url=base_url, today=today)
            fu = new.id
        except season.NoCard as e:
            res["ask_note"] = f"no card for the doubtful dates: {e}"
        res["followup_card_id"] = fu
    audit.log("AUTOPILOT_RUN", {k: res[k] for k in ("season", "engine", "target", "applied", "ask", "skipped",
                                                    "short_window", "card_id", "followup_card_id")},
              entity_id=entity_id, db=db)
    return res


def _apply_plan(entity_id, season_, plan, res, db, client, today, target, engine, base_url) -> None:
    as_of = data.SNAPSHOT[season_]
    contact = compose_bridge.contact(entity_id)
    own = client is None
    yc = client or season.yext_client(target, db)
    try:
        live = yc.get_holiday_hours(entity_id)
        items = []
        for s, v, p, perm, fact, w in plan:
            it = season._item(dataclasses.replace(s, value=v), entity_id, as_of)
            it.update(basis="OWNER_ANSWER", probability=p, flip_tick_required=False,
                      evidence=[{"source": "OWNER_ANSWER", "detail": f"You said {v} for {fact.date} ({fact.source}, "
                                 f"season {fact.season}, weight {w:.2f}); standing permission {perm.source}",
                                 "ref": f"learned_fact:{fact.id}", "weight": w},
                                {"source": "MODEL", "detail": f"This season's engine agrees: P={p:.2f}",
                                 "ref": "engine", "weight": p}])
            items.append(it)
        card = Card(entity_id=entity_id, season=season_, suggestion_ids=[i["suggestion_id"] for i in items],
                    items=items, status="DRAFT", target=target, engine=engine, owner_id=f"owner:{entity_id}",
                    lang=contact["lang"], channel=contact["channel"], dates_shown=[i["date"] for i in items],
                    expected_current={i["date"]: live.get(i["date"]) for i in items},
                    history=[{"from": None, "to": "DRAFT", "at": utcnow().isoformat(), "reason": "autopilot"}])
        dbmod.save(card, db=db)
        season.transition(card, "SENT", "autopilot: covered by a standing permission", db)
        season.transition(card, "DECIDED", "autopilot", db)
        batch = new_id("batch")
        card.batch_ids.append(batch)
        decs = {}
        for (s, v, p, perm, fact, w) in plan:
            short = (date.fromisoformat(s.date) - today).days < MIN_DAYS_AHEAD
            if short:
                res["short_window"].append(s.date)
                log.info("autopilot %s %s: short window (< %d days)", entity_id, s.date, MIN_DAYS_AHEAD)
            decs[s.date] = Decision(card_id=card.id, suggestion_id=s.id, decision="APPROVE", final_value=v,
                                    every_year=True, via="autopilot", entity_id=entity_id, date=s.date,
                                    batch_id=batch, note=f"permission {perm.id}; P={p:.2f}"
                                    + ("; short window" if short else ""))
        rec = yc.set_holiday_hours(entity_id, {d: x.final_value for d, x in decs.items()},
                                   {d: live.get(d) for d in decs}, batch, today=today)
    finally:
        if own:
            yc.close()
    applied = rec.request.get("applied") or {}
    for d, x in decs.items():
        x.write_id = rec.id
        x.status = "APPLIED" if (d in applied and rec.readback_ok) else (
            "CANCELLED" if d in (rec.request.get("cancelled") or {}) else "RECORDED")
        dbmod.save(x, db=db)
    ok = {d: x.final_value for d, x in decs.items() if x.status == "APPLIED"}
    res["applied"], res["card_id"] = ok, card.id
    if not ok:
        card.result = {"applied": {}, "error": rec.note}
        dbmod.save(card, db=db)
        return
    for perm in {id(x[3]): x[3] for x in plan}.values():
        perm.status = "USED"
        perm.uses.append({"season": season_, "dates": sorted(ok), "write_id": rec.id, "at": utcnow().isoformat()})
        dbmod.save(perm, db=db)
    season.transition(card, "APPLIED", f"autopilot wrote {len(ok)} date(s)", db)
    from cs import confirm
    where = confirm.where_it_shows(entity_id, rec.at, target=target)
    short = any(d in res["short_window"] for d in ok)
    try:
        from cs import compose
        msg = {**compose.compose_autopilot_note(compose.business_for(entity_id), ok, contact["lang"],
                                                channel=contact["channel"], where_it_shows=where, short_notice=short),
               "composer": "cs.compose"}
    except Exception:  # noqa: BLE001
        msg = {"text": "We set your holiday hours as agreed: " + ", ".join(f"{d} {v}" for d, v in ok.items())
               + ". Undo / Stop any time.", "buttons": [{"id": "UNDO", "title": "Undo"}, {"id": "STOP", "title": "Stop"}],
               "parts": {}, "composer": "fallback"}
    token = links.sign(card.id, card.owner_id)
    sent = compose_bridge.send(entity_id, msg, kind="AUTOPILOT", card_id=card.id, token=token, lang=contact["lang"],
                               channel=contact["channel"], db=db)
    audit.log("MESSAGE_SENT", {"card_id": card.id, "message_id": sent["message_id"], "kind": "AUTOPILOT",
                               "channel": contact["channel"], "delivery": sent["status"], "via": sent["delivered_via"]},
              entity_id=entity_id, db=db)
    res["message"] = msg["text"]
    res["link"] = links.link_for(token, base_url)
    card.result = {"applied": ok, "where_it_shows": [{**r, "expected_live_at": r["expected_live_at"].isoformat()
                                                      if r.get("expected_live_at") else None} for r in where],
                   "autopilot": True, "short_window": res["short_window"], "batch_id": batch}
    dbmod.save(card, db=db)
    season.transition(card, "CONFIRMED", "autopilot note sent", db)
    audit.log("AUTOPILOT_APPLIED", {"card_id": card.id, "batch_id": batch, "applied": ok, "write_id": rec.id,
                                    "short_window": res["short_window"]}, entity_id=entity_id, db=db)


def demo_reset(entity_id: str, db: Database | None = None) -> None:
    """Demo helper: forget the time-machine permission/facts so the scenario can be replayed."""
    db = _db(db)
    db.permissions.delete_many({"entity_id": entity_id, "source": TIME_MACHINE_SOURCE})
    db.learned_facts.delete_many({"entity_id": entity_id, "source": TIME_MACHINE_SOURCE})

