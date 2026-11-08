"""A11 (v0) – what owners told us: learned facts and standing permissions in MongoDB.

Learned facts (``learned_facts``):
- ``record_fact`` saves an owner answer; a newer fact for the same business and holiday
  (``day_key``) supersedes every older active one (``superseded_by`` = the new fact's id).
- ``revert_batch`` (used by undo) retires the facts of one decision batch and re-activates the
  facts they had superseded.
- ``active_facts`` = facts with ``superseded_by is None``.
- ``declined_values``: per holiday, the values the owner said no to (``cs.season`` never suggests
  them again).

Permissions (``permissions``): ``grant_permission`` (scope = the approved holiday keys and their
values), ``revoke_permission`` (UNDO / STOP / OPT_OUT), ``active_permissions`` (for the guardrails'
``permission_scope``). Ageing and decay by season (B5) come later.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from cs import audit, db as dbmod
from cs.domain import LearnedFact, Permission, utcnow


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


# ---------------------------------------------------------------------------
# Learned facts
# ---------------------------------------------------------------------------
def record_fact(entity_id: str, day_key: str, date: str, value: str, season: int, *,
                source: str = "OWNER_ANSWER", batch_id: str | None = None,
                db: Database | None = None) -> LearnedFact:
    db = _db(db)
    fact = LearnedFact(entity_id=entity_id, day_key=day_key, date=date, value=value, season=season,
                       source=source, batch_id=batch_id)
    dbmod.save(fact, db=db)
    res = db.learned_facts.update_many(
        {"entity_id": entity_id, "day_key": day_key, "superseded_by": None, "id": {"$ne": fact.id}},
        {"$set": {"superseded_by": fact.id}})
    audit.log("LEARNED_FACT", {"fact_id": fact.id, "day_key": day_key, "date": date, "value": value,
                               "source": source, "batch_id": batch_id, "superseded": res.modified_count},
              entity_id=entity_id, db=db)
    return fact


def active_facts(entity_id: str, db: Database | None = None) -> list[LearnedFact]:
    return dbmod.find(LearnedFact, {"entity_id": entity_id, "superseded_by": None}, db=_db(db),
                      sort=[("created_at", -1)])


def declined_values(entity_id: str, db: Database | None = None) -> dict[str, dict]:
    """{day_key: {"values": {hours, …}, "dates": [...], "at": newest decline}}: what the owner said
    no to on a card (DECLINE decisions, joined to the card item they declined)."""
    db = _db(db)
    decs = list(db.decisions.find({"entity_id": entity_id, "decision": "DECLINE", "status": "DECLINED"},
                                  {"_id": 0, "card_id": 1, "date": 1, "at": 1}))
    if not decs:
        return {}
    cards = {c["id"]: c for c in db.cards.find({"id": {"$in": sorted({d["card_id"] for d in decs})}},
                                                {"_id": 0, "id": 1, "items": 1})}
    out: dict[str, dict] = {}
    for d in decs:
        items = (cards.get(d["card_id"]) or {}).get("items") or []
        item = next((i for i in items if i.get("date") == d.get("date")), None)
        if not item or not item.get("day_key") or not item.get("value"):
            continue
        e = out.setdefault(item["day_key"], {"values": set(), "dates": [], "at": d["at"]})
        e["values"].add(item["value"])
        e["dates"].append(d["date"])
        e["at"] = max(_naive(e["at"]), _naive(d["at"]))
    return out


def _naive(t: datetime) -> datetime:
    return t.replace(tzinfo=None) if t.tzinfo else t


def revert_batch(batch_id: str, db: Database | None = None) -> int:
    """Retire the facts of a decision batch and bring back what they superseded."""
    db = _db(db)
    facts = dbmod.find(LearnedFact, {"batch_id": batch_id}, db=db)
    for f in facts:
        db.learned_facts.update_many({"superseded_by": f.id}, {"$set": {"superseded_by": None}})
        db.learned_facts.update_one({"id": f.id}, {"$set": {"superseded_by": f"undo:{batch_id}"}})
    return len(facts)


# ---------------------------------------------------------------------------
# Standing permissions
# ---------------------------------------------------------------------------
def grant_permission(entity_id: str, values_by_day_key: dict[str, str], *, via: str = "web",
                     batch_id: str | None = None, db: Database | None = None) -> Permission:
    db = _db(db)
    scope = {"day_keys": sorted(values_by_day_key), "values": dict(values_by_day_key), "min_days_ahead": 14,
             "text": "The same holidays with the same hours every year; anything new is still asked."}
    p = Permission(entity_id=entity_id, scope=scope, status="GRANTED", granted_via=via, source_decision_id=batch_id)
    dbmod.save(p, db=db)
    audit.log("PERMISSION_GRANTED", {"permission_id": p.id, "scope": scope, "batch_id": batch_id, "via": via},
              entity_id=entity_id, db=db)
    return p


def revoke_permission(permission_id: str, reason: str = "UNDO", db: Database | None = None) -> bool:
    db = _db(db)
    p = dbmod.get(Permission, permission_id, db=db)
    if p is None or p.status == "REVOKED":
        return False
    p.status, p.revoked_reason, p.revoked_at = "REVOKED", reason, utcnow()
    dbmod.save(p, db=db)
    audit.log("PERMISSION_REVOKED", {"permission_id": p.id, "reason": reason}, entity_id=p.entity_id, db=db)
    return True


def active_permissions(entity_id: str | None = None, db: Database | None = None) -> dict[str, list[Permission]]:
    """{entity_id: [GRANTED/USED permissions]} for ``guardrails.make_context(permissions=…)``."""
    q: dict = {"status": {"$in": ["GRANTED", "USED"]}}
    if entity_id:
        q["entity_id"] = entity_id
    out: dict[str, list[Permission]] = {}
    for p in dbmod.find(Permission, q, db=_db(db)):
        out.setdefault(p.entity_id, []).append(p)
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)
