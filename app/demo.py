"""Demo view (judges / video) and a thin ops console.

    GET  /demo?entity_id=FI-D0001[&engine=ml][&target=sandbox]   phone | live Yext entity | five steps
    POST /demo/send                    build the card, compose + store the message, show the link
    GET  /demo/phone|entity|steps      HTMX partials (polled)
    POST /demo/clear                   clear the demo entity's holidayHours (restore the sandbox)
    GET  /ops                          cards queue + audit trail;  GET /ops/verify  verify the chain
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from cs import audit, data, db as dbmod, season
from cs.compose_bridge import thread
from cs.domain import Card, Message
from cs.hours import DAYS, week_from_yext
from cs.suggest import engine_adapter

bp = Blueprint("demo", __name__)

SCENARIOS = [
    ("FI-D0001", "Kahvila Aurora: the full loop on the real sandbox"),
    ("FI-D0012", "Tilitoimisto Otso: autopilot next season (CRM permission)"),
    ("FI-D0011", "Pizzeria Torni: closed for good, flagged, no message"),
    ("FI-D0013", "Hammasklinikka Kallio: already right, stay quiet"),
    ("FI-D0017", "Kahvila Kipinä: new customer, no history"),
    ("SE-D0016", "Restaurang Fyren: Swedish, drift"),
]
STEPS = [
    ("Detect", ("CARD_BUILT", "AUTOPILOT_START", "EMERGENCY_RECEIVED"), "What Google shows on each special day"),
    ("Suggest", ("CARD_BUILT", "AUTOPILOT_START", "EMERGENCY_RECEIVED"), "Guarded suggestions with evidence"),
    ("Contact", ("MESSAGE_SENT",), "One message with a no-login link"),
    ("Decide", ("DECISION_RECORDED", "QUESTION_ANSWERED", "AUTOPILOT_APPLIED", "EMERGENCY_RECEIVED"),
     "Approve / edit / no thanks (or a standing permission, or the owner's own emergency)"),
    ("Apply", ("YEXT_WRITE", "CONFIRMATION", "AUTOPILOT_APPLIED", "EMERGENCY_APPLIED"),
     "Written to Yext, read back, where it shows"),
]


def _params():
    eid = (request.values.get("entity_id") or "FI-D0001").strip().split()[0].upper()
    engine = (request.values.get("engine") or ("ml" if engine_adapter.available(2026) else "baseline")).lower()
    target = (request.values.get("target") or season.default_target(eid)).lower()
    return eid, engine, target


def _latest_card(entity_id: str) -> Card | None:
    found = dbmod.find(Card, {"entity_id": entity_id}, sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


@bp.get("/demo")
def demo():
    eid, engine, target = _params()
    data.entity(eid)
    return render_template("demo.html", scenarios=SCENARIOS, entity_id=eid, engine=engine, target=target,
                           name=data.entity(eid).get("name", eid))


@bp.post("/demo/send")
def demo_send():
    eid, engine, target = _params()
    try:
        card = season.build_card(eid, 2026, engine, target=target)
    except season.NoCard as e:
        audit.log("NO_CARD", {"reason": str(e), "engine": engine}, entity_id=eid)
        return _phone(eid, note=f"No message: {e}")
    except Exception as e:  # noqa: BLE001 - e.g. the mock is not running
        return _phone(eid, note=f"Could not build the card: {e}")
    season.send_card(card, base_url=request.host_url)
    return _phone(eid)


def _phone(eid: str, note: str | None = None):
    card = _latest_card(eid)
    msgs = thread(eid)[-14:]
    token = next((m["token"] for m in reversed(msgs) if m.get("token")), None)
    return render_template("_demo_phone.html", messages=msgs, entity_id=eid, note=note,
                           link=f"/c/{token}" if token else None, status=card.status if card else None)


@bp.get("/demo/phone")
def demo_phone():
    eid, _, _ = _params()
    return _phone(eid)


def _owner_phone(eid: str) -> str | None:
    """The demo phone is the owner's registered phone (so the sender check passes)."""
    c = data.contacts()
    row = c[c["entity_id"] == eid]
    return None if row.empty else row.iloc[0]["mobile"]


@bp.post("/demo/reply")
def demo_reply():
    """The owner types or taps on the demo phone: the same path as an SMS / WhatsApp webhook."""
    from cs import decide_flow
    eid, _, _ = _params()
    text = (request.form.get("text") or "").strip() or None
    button = (request.form.get("button_id") or "").strip() or None
    card_id = (request.form.get("card_id") or "").strip() or None
    note = None
    if button and not card_id:          # a button always belongs to a message; without one, ignore it
        button = None
    if text or button:
        res = decide_flow.handle_reply(eid, card_id=card_id, button_id=button, text=None if button else text,
                                       channel="WHATSAPP" if button else "SMS", sender=_owner_phone(eid),
                                       base_url=request.host_url)
        if not res.get("handled"):
            note = f"Not applied: {res.get('reason')}"
    return _phone(eid, note)


@bp.post("/demo/emergency")
def demo_emergency():
    """Owner: closed today (from the owner's registered phone)."""
    from cs import emergency
    eid, _, target = _params()
    try:
        emergency.handle_emergency(eid, hours="closed", via="SMS", sender=_owner_phone(eid), target=target,
                                   base_url=request.host_url)
        note = None
    except emergency.EmergencyError as e:
        note = f"Not applied: {e}"
    return _phone(eid, note)


@bp.post("/demo/autopilot")
def demo_autopilot():
    """Next season: apply what the standing permission covers, inform with undo (FI-D0012: CRM 2025;
    others: the demo-only time machine, as if they had said 'every year' in 2025)."""
    from cs import autopilot
    eid, engine, target = _params()
    tm = request.form.get("time_machine", "1") == "1"
    try:
        res = autopilot.run_autopilot(eid, 2026, target=target, engine=engine, time_machine=tm,
                                      base_url=request.host_url)
    except Exception as e:  # noqa: BLE001 - e.g. the mock is not running
        return _phone(eid, f"Autopilot failed: {e}")
    parts = [f"Autopilot: applied {len(res['applied'])} date(s)"]
    if res["ask"]:
        parts.append(f"asked about {len(res['ask'])} (engine not sure)")
    if res["short_window"]:
        parts.append(f"{len(res['short_window'])} in a short window")
    if not res["permissions"]:
        parts = ["Autopilot: no standing permission for this business"]
    return _phone(eid, "; ".join(parts) + ".")


@bp.get("/demo/entity")
def demo_entity():
    eid, _, target = _params()
    try:
        with season.yext_client(target) as yc:
            ent = yc.get_entity(eid)
            yid = yc.yext_id(eid)
    except Exception as e:  # noqa: BLE001
        hint = " Start it with: .venv/bin/python scripts/run_mock.py" if target == "mock" else ""
        return render_template("_demo_entity.html", error=f"Could not read the {target}: {e}.{hint}", target=target)
    hours = ent.get("hours") or {}
    week = list(zip([d.capitalize()[:3] for d in DAYS], week_from_yext(hours)))
    from cs.yext import holiday_map
    return render_template("_demo_entity.html", error=None, target=target, yid=yid, entity_id=eid,
                           name=ent.get("name", ""), week=week, holidays=holiday_map(ent))


@bp.get("/demo/steps")
def demo_steps():
    eid, _, _ = _params()
    evs = list(reversed(audit.events(entity_id=eid, limit=80)))
    start = max((i for i, e in enumerate(evs) if e["event_type"] in ("CARD_BUILT", "NO_CARD", "AUTOPILOT_START",
                                                                         "EMERGENCY_RECEIVED")), default=None)
    evs = evs[start:] if start is not None else []
    steps = []
    for label, types, desc in STEPS:
        hit = [e for e in evs if e["event_type"] in types]
        steps.append({"label": label, "desc": desc, "done": bool(hit), "detail": _step_detail(label, hit)})
    no_card = next((e for e in evs if e["event_type"] == "NO_CARD"), None)
    return render_template("_demo_steps.html", steps=steps, no_card=no_card, events=evs[-12:])


def _step_detail(label: str, hit: list[dict]) -> str:
    if not hit:
        return ""
    p = hit[-1]["payload"]
    if p.get("summary") and label in ("Detect", "Suggest", "Decide"):
        return p["summary"]
    if label == "Decide" and hit[-1]["event_type"] == "AUTOPILOT_APPLIED":
        return f"standing permission: {len(p.get('applied') or {})} date(s)"
    if label == "Detect":
        return "; ".join(f"{i['date']}: {i['shown']}" for i in p.get("items", [])[:3])
    if label == "Suggest":
        n = len(p.get("items", []))
        return f"{n} date(s)" + (f" + one question ({p['question']['date']})" if p.get("question") else "")
    if label == "Contact":
        return f"{p.get('channel', '')} · {p.get('composer', '')}".strip(" ·")
    if label == "Decide":
        ds = p.get("decisions") or []
        return ", ".join(f"{d['date']} {d['decision'].lower()}" for d in ds[:4])
    if label == "Apply":
        w = next((e["payload"] for e in reversed(hit) if e["event_type"] == "YEXT_WRITE"), None)
        c = next((e["payload"] for e in reversed(hit) if e["event_type"] == "CONFIRMATION"), None)
        parts = []
        if w:
            parts.append(f"{w.get('target')} read-back {'OK' if w.get('readback_ok') else 'mismatch'}")
        if c and c.get("publishers"):
            parts.append(f"{c['publishers'][0]['publisher']} {c['publishers'][0]['eta']}")
        return " · ".join(parts)
    return ""


@bp.post("/demo/clear")
def demo_clear():
    """Demo reset for one business: clear holidayHours (and a reopenDate), end its emergencies so
    the daily rate limit starts over, and stop their schedules. Demo only; the audit log keeps it all."""
    eid, _, target = _params()
    with season.yext_client(target) as yc:
        yc.clear_holiday_hours(eid, decision_id="demo-reset")
        if (yc.get_entity(eid).get("hours") or {}).get("reopenDate"):
            yc.set_reopen_date(eid, None, decision_id="demo-reset")
    db = dbmod.get_db()
    ids = [e["id"] for e in db.emergencies.find({"entity_id": eid, "status": {"$ne": "DEMO_RESET"}}, {"id": 1})]
    db.emergencies.update_many({"id": {"$in": ids}}, {"$set": {"status": "DEMO_RESET"}})
    db.schedules.update_many({"emergency_id": {"$in": ids}}, {"$set": {"active": False}})
    audit.log("DEMO_RESET", {"target": target, "emergencies_reset": len(ids)}, entity_id=eid)
    return demo_entity()


@bp.get("/ops")
def ops():
    cards = dbmod.find(Card, {}, sort=[("created_at", -1)], limit=50)
    links_by_card = {}
    for m in dbmod.find(Message, {"card_id": {"$in": [c.id for c in cards]}, "token": {"$ne": None}}):
        links_by_card[m.card_id] = f"/c/{m.token}"
    names = {c.entity_id: data.entities().get(c.entity_id, {}).get("name", "") for c in cards}
    return render_template("ops.html", cards=cards, links=links_by_card, names=names,
                           events=audit.events(limit=60))


@bp.get("/ops/verify")
def ops_verify():
    return render_template("_ops_verify.html", res=audit.verify_chain())
