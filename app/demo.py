"""The console (judges, the video, Fonecta staff) and the ops page.

One screen, one button: pick a business and press "Check this business". Connect then does
whatever is right for that business, exactly as it would in production:

- a standing permission ("every year", or Fonecta's CRM flag)  -> Autopilot sets the dates and
  sends the owner a note with Undo (FI-D0012);
- hours that look wrong                                          -> one card to the owner (FI-D0001);
- everything already right                                       -> stay quiet (FI-D0013);
- probably closed for good                                       -> flag for a person, no message (FI-D0011);
- weekly hours that changed                                      -> also ask about those (SE-D0016).

The owner's phone sits next to it: replies, taps, "closed today" and questions go through the same
code as the WhatsApp/SMS webhooks.

    GET  /  (and /demo)            the console
    POST /demo/check               the one button
    GET  /demo/phone               the owner's phone (HTMX, refreshed on change)
    POST /demo/reply               the owner types or taps on the phone
    GET  /demo/steps               the five steps for this business
    GET  /demo/entity              the live listing (Yext sandbox or mock)
    POST /demo/clear               demo reset for one business
    POST /demo/send | /demo/autopilot | /demo/emergency   single steps (API and tests)
    GET  /ops, /ops/verify         staff: messages, cards and the tamper-evident audit log
"""
from __future__ import annotations

from functools import lru_cache

from flask import Blueprint, make_response, render_template, request

from cs import audit, data, db as dbmod, season
from cs.compose_bridge import thread
from cs.domain import Card, Message
from cs.hours import DAYS, week_from_yext
from cs.suggest import engine_adapter

bp = Blueprint("demo", __name__)

SEASON = 2026
DEFAULT_BUSINESS = "FI-D0001"
# What each demo business shows (from the dataset's DEMO_SCENARIOS.md), shown in the picker.
STORIES = {
    "FI-D0001": "Christmas hours wrong on Google",
    "FI-D0017": "new customer, no history",
    "FI-D0012": "said “every year”: Autopilot",
    "FI-D0013": "already right: stays quiet",
    "FI-D0011": "closed for good: flagged",
    "SE-D0016": "weekly hours changed",
    "FI-D0004": "inside a shopping centre",
    "FI-D0007": "closes between Christmas and New Year",
    "SE-D0008": "closed on Christmas Eve",
    "NO-D0009": "no history to copy",
    "FI-D0010": "next to the arena",
}
STEPS = [
    ("Detect", ("CARD_BUILT", "AUTOPILOT_START", "EMERGENCY_RECEIVED", "NO_CARD"), "What Google shows on each special day"),
    ("Suggest", ("CARD_BUILT", "AUTOPILOT_START", "EMERGENCY_RECEIVED"), "The right hours, with the reason"),
    ("Contact", ("MESSAGE_SENT",), "One message, a link that needs no login"),
    ("Decide", ("DECISION_RECORDED", "QUESTION_ANSWERED", "AUTOPILOT_APPLIED", "EMERGENCY_RECEIVED"),
     "The owner approves, edits or says no"),
    ("Apply", ("YEXT_WRITE", "CONFIRMATION", "AUTOPILOT_APPLIED", "EMERGENCY_APPLIED"),
     "Written to Yext, read back, where it shows"),
]
START_EVENTS = ("CARD_BUILT", "NO_CARD", "AUTOPILOT_START", "EMERGENCY_RECEIVED")


def _params():
    raw = request.values.get("entity_id") or request.values.get("b") or DEFAULT_BUSINESS
    eid = raw.strip().split()[0].upper()
    engine = (request.values.get("engine") or ("ml" if engine_adapter.available(SEASON) else "baseline")).lower()
    target = (request.values.get("target") or season.default_target(eid)).lower()
    return eid, engine, target


def _name(eid: str) -> str:
    return data.entity(eid).get("name", eid)


@lru_cache(maxsize=1)
def businesses() -> list[dict]:
    """The demo businesses for the picker: the main storylines first."""
    df = data.entity_index()
    rows = df[df["split"] == "demo"][["entity_id", "name", "city"]].to_dict("records")
    order = list(STORIES)
    rows.sort(key=lambda r: (order.index(r["entity_id"]) if r["entity_id"] in order else len(order), r["name"]))
    return [{**r, "story": STORIES.get(r["entity_id"], "")} for r in rows]


def _latest_card(entity_id: str) -> Card | None:
    found = dbmod.find(Card, {"entity_id": entity_id}, sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def _changed(body, status: int = 200):
    """Tell the phone, the steps and the listing to refresh."""
    r = make_response(body, status)
    r.headers["HX-Trigger"] = "connect-changed"
    return r


# ---------------------------------------------------------------------------
# The console
# ---------------------------------------------------------------------------
@bp.get("/")
@bp.get("/demo")
def console():
    eid, engine, target = _params()
    data.entity(eid)
    known = {b["entity_id"] for b in businesses()}
    choices = businesses() if eid in known else [{"entity_id": eid, "name": _name(eid), "city": "", "story": ""},
                                                 *businesses()]
    return render_template("ui/console.html", entity_id=eid, engine=engine, target=target, name=_name(eid),
                           businesses=choices)


def _card_rows(card: Card) -> list[dict]:
    from app.app import BASIS_TEXT
    rows = [{"date": i["date"], "day_key": i["day_key"], "shown": i["shown"], "value": i["value"],
             "why": BASIS_TEXT.get(i["basis"], i["basis"]), "question": False} for i in card.items]
    if card.question:
        q = card.question
        rows.append({"date": q["date"], "day_key": q.get("day_key"), "shown": q.get("shown"), "value": None,
                     "why": "One question for the owner", "question": True})
    return sorted(rows, key=lambda r: r["date"])


def run_check(eid: str, engine: str, target: str, base_url: str) -> dict:
    """The one button. Returns what happened, for the result panel."""
    from cs import autopilot, learn
    name = _name(eid)
    out: dict = {"entity_id": eid, "name": name, "kind": "", "headline": "", "text": "", "rows": [],
                 "link": None, "extra": []}

    # 1. A standing permission: Autopilot applies what it covers (and asks about the rest).
    has_permission = bool(autopilot.crm_permission(eid)) or bool(learn.active_permissions(eid).get(eid))
    if has_permission:
        res = autopilot.run_autopilot(eid, SEASON, target=target, engine=engine, base_url=base_url)
        if res["applied"]:
            out.update(kind="autopilot", headline=f"Set {_n(len(res['applied']), 'date')} automatically",
                       text=f"{name} gave Connect a standing permission (“every year”), so nothing had to be "
                            "asked. The owner got a short note with Undo.",
                       rows=[{"date": d, "day_key": None, "shown": None, "value": h, "why": "Standing permission",
                              "question": False} for d, h in sorted(res["applied"].items())])
            if res["ask"]:
                out["extra"].append(f"Not sure about {_n(len(res['ask']), 'other date')}: asked the owner about those.")
            _offer_review_replies(out, eid)
            return _with_drift(out, eid)

    # 2. One card per business and season: if one is still waiting for the owner, never send a second.
    waiting = _waiting_card(eid)
    if waiting is not None:
        return _waiting(out, waiting)

    # 3. Otherwise: one card, or a good reason not to send anything.
    try:
        card = season.build_card(eid, SEASON, engine, target=target)
    except season.NoCard as e:
        reason = str(e)
        if getattr(e, "card", None) is not None:          # season.CardWaiting (raised by build_card itself)
            return _waiting(out, e.card)
        audit.log("NO_CARD", {"reason": reason, "engine": engine}, entity_id=eid)
        if "already said no" in reason:
            out.update(kind="learned", headline="Learned: Connect won't ask that again",
                       text=f"{name} said no to these hours before. Connect remembers, so it sends nothing: "
                            "only the owner's own new information can bring a date back.")
        elif "flagged" in reason:
            out.update(kind="flagged", headline="Flagged for a Fonecta person: no message",
                       text=f"{name} looks closed for good. A person checks before anything changes; "
                            "the owner is not messaged.")
        elif "opted out" in reason:
            out.update(kind="optout", headline="The owner opted out: nothing is sent",
                       text="Connect respects the opt-out and sends nothing.")
        elif "right hours" in reason:
            out.update(kind="quiet", headline="Already right: Connect stays quiet",
                       text="Google already shows the right hours on every upcoming special day, "
                            "so there is nothing to ask.")
        else:
            out.update(kind="none", headline="Nothing to check", text=reason)
        return _with_drift(out, eid)
    except Exception as e:  # noqa: BLE001 - e.g. the Yext mock is not running
        out.update(kind="error", headline="Could not check this business", text=str(e))
        return out
    _msg, token = season.send_card(card, base_url=base_url)
    card = season.load_card(card.id) or card
    n = len(card.items)
    channel = {"WHATSAPP": "WhatsApp", "SMS": "SMS", "EMAIL": "email", "APP_PUSH": "the Connect app"}.get(
        card.channel, card.channel.lower())
    from app.app import nice_date
    what = (f"{_n(n, 'date')} {'looks' if n == 1 else 'look'} wrong on Google" if n
            else "One date is worth asking about")
    by = f" (answer by {nice_date(card.approve_by)})" if card.approve_by else ""
    out.update(kind="card", headline=f"{what}: sent the owner one message",
               text=f"By {channel}, in the owner's language. Nothing changes until the owner answers{by}.",
               rows=_card_rows(card), link=f"/c/{token}")
    return _with_drift(out, eid)


def _n(k: int, word: str) -> str:
    """1 date, 3 dates."""
    return f"{k} {word}" if k == 1 else f"{k} {word}s"


WAITING_STATES = ("SCHEDULED", "SENT", "QUESTION_ANSWERED", "NO_REPLY", "REMINDED")


def _waiting_card(eid: str) -> Card | None:
    found = dbmod.find(Card, {"entity_id": eid, "season": SEASON, "status": {"$in": list(WAITING_STATES)}},
                       sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def _waiting(out: dict, card: Card) -> dict:
    msg = dbmod.find(Message, {"card_id": card.id, "token": {"$ne": None}}, sort=[("sent_at", -1)], limit=1)
    out.update(kind="waiting", headline="Waiting for the owner: nothing new is sent",
               text="The owner already has this season's card. Connect sends one message per season, "
                    "so it waits for the answer instead of asking twice.",
               rows=_card_rows(card), link=f"/c/{msg[0].token}" if msg else None)
    return out


def _with_drift(out: dict, eid: str) -> dict:
    """Weekly hours that changed (A13): ask about those too, in the same thread."""
    try:
        from cs import profile_flow
        res = profile_flow.offer_drift(eid)
    except Exception:  # noqa: BLE001 - the holiday result stands on its own
        res = None
    if res:
        out["extra"].append(res["summary"])
        if not out["kind"] or out["kind"] in ("quiet", "none"):
            out.update(kind="drift", headline="The weekly hours look out of date: asked the owner",
                       text=res["summary"])
    return out


def _offer_review_replies(out: dict | None, eid: str) -> None:
    """After hours are set: offer a reply to customers who found the door closed (A13)."""
    try:
        from cs import profile_flow
        res = profile_flow.offer_review_replies(eid)
    except Exception:  # noqa: BLE001 - optional
        res = None
    if res and out is not None:
        out["extra"].append(res["summary"])


def after_decision(eid: str) -> None:
    """Called after the owner answered: once the hours are applied, offer the review reply."""
    from cs import decide_flow
    card = _latest_card(eid)
    if card and card.status in decide_flow.APPLIED_STATES:
        _offer_review_replies(None, eid)


@bp.post("/demo/check")
def demo_check():
    eid, engine, target = _params()
    return _changed(render_template("ui/_result.html", r=run_check(eid, engine, target, request.host_url)))


# ---------------------------------------------------------------------------
# The owner's phone
# ---------------------------------------------------------------------------
def _phone(eid: str, note: str | None = None):
    card = _latest_card(eid)
    msgs = thread(eid)[-16:]
    token = next((m["token"] for m in reversed(msgs) if m.get("token")), None)
    return render_template("ui/_phone.html", messages=msgs, entity_id=eid, note=note, name=_name(eid),
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
        else:
            after_decision(eid)
    return _changed(_phone(eid, note))


@bp.post("/demo/send")
def demo_send():
    """Build and send the card only (no Autopilot, no drift)."""
    eid, engine, target = _params()
    channel = (request.values.get("channel") or "").strip().upper() or None
    try:
        card = season.build_card(eid, SEASON, engine, target=target)
    except season.NoCard as e:
        audit.log("NO_CARD", {"reason": str(e), "engine": engine}, entity_id=eid)
        return _changed(_phone(eid, note=f"No message: {e}"))
    except Exception as e:  # noqa: BLE001 - e.g. the mock is not running
        return _phone(eid, note=f"Could not build the card: {e}")
    if channel in ("WHATSAPP", "EMAIL", "SMS", "OUTBOX"):
        card.channel = channel
        dbmod.save(card)
    season.send_card(card, base_url=request.host_url)
    return _changed(_phone(eid))


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
    return _changed(_phone(eid, note))


@bp.post("/demo/autopilot")
def demo_autopilot():
    """Next season: apply what the standing permission covers, inform with undo (FI-D0012: CRM 2025;
    others: the demo-only time machine, as if they had said 'every year' in 2025)."""
    from cs import autopilot
    eid, engine, target = _params()
    tm = request.form.get("time_machine", "1") == "1"
    try:
        res = autopilot.run_autopilot(eid, SEASON, target=target, engine=engine, time_machine=tm,
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
    return _changed(_phone(eid, "; ".join(parts) + "."))


# ---------------------------------------------------------------------------
# The five steps and the live listing
# ---------------------------------------------------------------------------
@bp.get("/demo/steps")
def demo_steps():
    eid, _, _ = _params()
    evs = list(reversed(audit.events(entity_id=eid, limit=80)))
    start = max((i for i, e in enumerate(evs) if e["event_type"] in START_EVENTS), default=None)
    evs = evs[start:] if start is not None else []
    steps, now_set = [], False
    for label, types, desc in STEPS:
        hit = [e for e in evs if e["event_type"] in types and not (e["event_type"] == "NO_CARD" and label != "Detect")]
        done = bool(hit)
        state = "done" if done else ("now" if evs and not now_set else "todo")
        now_set = now_set or state == "now"
        steps.append({"label": label, "desc": desc, "state": state, "detail": _step_detail(label, hit)})
    no_card = next((e for e in evs if e["event_type"] == "NO_CARD"), None)
    if no_card:
        steps = [{**s, "state": "done" if s["label"] == "Detect" else "todo"} for s in steps]
    return render_template("ui/_steps.html", steps=steps, no_card=no_card, events=evs[-14:], started=bool(evs))


def _step_detail(label: str, hit: list[dict]) -> str:
    if not hit:
        return ""
    p = hit[-1]["payload"]
    if hit[-1]["event_type"] == "NO_CARD":
        return p.get("reason", "")
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
        return f"{p.get('channel', '')} · {p.get('composer', '')}".strip(" ·").lower()
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


@bp.get("/demo/entity")
def demo_entity():
    from cs.yext import holiday_map
    eid, _, target = _params()
    try:
        with season.yext_client(target) as yc:
            ent = yc.get_entity(eid)
            yid = yc.yext_id(eid)
    except Exception as e:  # noqa: BLE001
        hint = " Start it with: .venv/bin/python scripts/run_mock.py" if target == "mock" else ""
        return render_template("ui/_listing.html", error=f"Could not read the {target}: {e}.{hint}", target=target)
    hours = ent.get("hours") or {}
    week = list(zip([d.capitalize()[:3] for d in DAYS], week_from_yext(hours)))
    return render_template("ui/_listing.html", error=None, target=target, yid=yid, entity_id=eid,
                           name=ent.get("name", ""), week=week, holidays=holiday_map(ent),
                           reopen=hours.get("reopenDate"))


@bp.post("/demo/clear")
def demo_clear():
    """Demo reset for one business: clear holidayHours (and a reopenDate), end its emergencies so
    the daily rate limit starts over, and stop their schedules. Demo only; the audit log keeps it all."""
    eid, _, target = _params()
    with season.yext_client(target) as yc:
        yc.clear_holiday_hours(eid, decision_id="demo-reset")
        if (yc.get_entity(eid).get("hours") or {}).get("reopenDate"):
            yc.set_reopen_date(eid, None, decision_id="demo-reset")
    from cs import profile_flow
    db = dbmod.get_db()
    ids = [e["id"] for e in db.emergencies.find({"entity_id": eid, "status": {"$ne": "DEMO_RESET"}}, {"id": 1})]
    db.emergencies.update_many({"id": {"$in": ids}}, {"$set": {"status": "DEMO_RESET"}})
    db.schedules.update_many({"emergency_id": {"$in": ids}}, {"$set": {"active": False}})
    offers = profile_flow.reset_offers(eid, db)
    closed = _close_waiting(eid)          # a reset business must not stay stuck on "waiting for the owner"
    audit.log("DEMO_RESET", {"target": target, "emergencies_reset": len(ids), "offers_reset": offers,
                             "cards_closed": closed}, entity_id=eid)
    return _changed(demo_entity())


def _close_waiting(eid: str) -> int:
    """Expire this season's waiting cards (demo reset). Learned facts are kept: the reset is what shows learning."""
    closer = getattr(season, "close_waiting", None)
    if closer is not None:
        return closer(eid, SEASON, "demo reset")
    path = {"SCHEDULED": ("SENT", "NO_REPLY", "EXPIRED"), "SENT": ("NO_REPLY", "EXPIRED"),
            "QUESTION_ANSWERED": ("NO_REPLY", "EXPIRED"), "NO_REPLY": ("EXPIRED",), "REMINDED": ("EXPIRED",)}
    n = 0
    for c in dbmod.find(Card, {"entity_id": eid, "season": SEASON, "status": {"$in": list(WAITING_STATES)}}):
        try:
            for step in path.get(c.status, ()):
                season.transition(c, step, "demo reset")
            n += 1
        except ValueError:
            pass
    return n


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
@bp.get("/ops")
def ops():
    cards = dbmod.find(Card, {}, sort=[("created_at", -1)], limit=50)
    links_by_card = {}
    for m in dbmod.find(Message, {"card_id": {"$in": [c.id for c in cards]}, "token": {"$ne": None}}):
        links_by_card[m.card_id] = f"/c/{m.token}"
    names = {c.entity_id: data.entities().get(c.entity_id, {}).get("name", "") for c in cards}
    flagged = [e for e in audit.events(limit=400) if e["event_type"] == "NO_CARD"
               and "flagged" in (e["payload"].get("reason") or "")]
    seen, flags = set(), []
    for e in flagged:
        if e["entity_id"] not in seen:
            seen.add(e["entity_id"])
            flags.append({"entity_id": e["entity_id"], "name": data.entities().get(e["entity_id"], {}).get("name", ""),
                          "reason": e["payload"].get("reason", ""), "at": e.get("at")})
    return render_template("ui/ops.html", cards=cards, links=links_by_card, names=names, flags=flags,
                           events=audit.events(limit=60))


@bp.get("/ops/verify")
def ops_verify():
    return render_template("ui/_verify.html", res=audit.verify_chain())
