"""The owner's experience: join Connect, onboarding on your own channel, and a season that unfolds by itself.

You play the owner of any business in Fonecta's directory. Connect does what it would do in production,
on its own schedule; the one "Continue" button only moves time forward (in production a daily job does
this, ``scripts/tick.py``). You answer on your phone, exactly as an owner would.

    GET  /             landing: what Connect is; start as a business owner
    GET  /start        join: your business, how Connect reaches you, the language
    POST /start        your season starts: a fresh listing, and the welcome (onboarding) on your channel
    GET  /me           your season: where you are, what Connect did and why, your phone, your Google listing
    POST /me/continue  time moves on; Connect does what it would do by itself at that point
    POST /me/say       a suggested reply ("closed today", a question), as if typed on your phone
    POST /me/restart   start your season again
    GET  /me/listing   your listing as Google shows it (HTMX)
    GET  /how          how Connect decides: the engine, the safety rules, the measured results
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from flask import Blueprint, redirect, render_template, request

from cs import compose_bridge, data, db as dbmod, season
from cs.domain import utcnow

bp = Blueprint("journey", __name__)

OWNER_COOKIE = "connect_owner"
COLLECTION = "journeys"
STAGES = [
    {"key": "welcome", "when": "Today", "day": None, "title": "Welcome to Connect",
     "about": "You join Connect"},
    {"key": "season", "when": "1 Dec 2026", "day": "Tuesday 1 December 2026", "title": "Connect checked your holiday hours",
     "about": "Connect's pre-season check, three weeks before the holidays"},
    {"key": "life", "when": "12 Dec 2026", "day": "Saturday 12 December 2026", "title": "Life happens",
     "about": "An ordinary Saturday: Connect watches the rest of your profile too"},
    {"key": "next", "when": "Nov 2027", "day": "Monday 1 November 2027", "title": "Next season",
     "about": "A year later: Connect checks again, and remembers what you told it"},
    {"key": "year", "when": "Jan 2028", "day": "Friday 7 January 2028", "title": "Your year with Connect",
     "about": "The season is over: what Connect did for you"},
]
LANG_NAMES = {"fi": "Finnish", "sv": "Swedish", "nb": "Norwegian", "en": "English"}

# What Connect does when the story reaches each day, in order, each with the model or rule behind it.
# The page shows these one by one while the server really does the work (app/static/connect.js).
def next_steps(key: str, channel: str) -> list[list[str]]:
    return {
        "season": [["Reading your listing from Yext", "Yext Knowledge API"],
                   ["Working out what Google and every app show on each special day",
                    "display rules · 100% match with Fonecta's labels"],
                   ["Loading this season's predictions for your business",
                    "made by the trained models in the nightly batch: gradient-boosted trees, habit types, "
                    "hours ranker, calibrated"],
                   ["Applying the safety rules and what you told Connect",
                    "guardrails · your answers · expected points on Fonecta's scoring table"],
                   ["Choosing the one question worth asking", "value of information: re-runs the models for each "
                    "possible answer"],
                   [f"Deciding whether to message you {_via(channel)}, and writing it",
                    "answer-by date from each app's update speed"]],
        "life": [["Comparing your weekly hours with reviews and map-user edits",
                  "drift detector · logistic regression"],
                 ["Looking for customers who found the door closed", "reviews in four languages"],
                 ["Drafting replies in each reviewer's language", "facts from your confirmed hours only"]],
        "next": [["Starting next season: the listing's holiday entries are cleared",
                  "the dataset covers one season, so its calendar is replayed"],
                 ["Reading your listing from Yext", "Yext Knowledge API"],
                 ["Remembering what you told Connect", "learned facts · the newest answer wins"],
                 ["Loading the season's predictions", "the trained models' batch output, calibrated"],
                 ["If you said “every year”: checking the model agrees with your answers",
                  "standing permission × model agreement (at least 50% likely)"],
                 ["Setting what the model agrees with, asking about the rest", "owner wins · read-back"]],
        "year": [["Counting what Connect fixed and who saw it", "search views from Yext analytics"]],
    }.get(key, [])


def join_steps(n_businesses: int) -> list[list[str]]:
    return [["Finding your business in Fonecta's directory", f"{n_businesses:,} businesses"],
            ["Starting your season with a fresh listing", "Yext Knowledge API"],
            ["Checking which special days look wrong", "display rules + the trained models' predictions"],
            ["Saying hello on your channel", "automated-assistant notice · STOP · one question"]]


@bp.app_template_global("phone_steps")
def phone_steps(button_id: str, card_id: str | None = None) -> list[list[str]]:
    """What happens after a tap on the phone, shown while it happens."""
    b, c = (button_id or "").upper(), (card_id or "")
    if b in ("OB_CLOSED", "OB_SHORT", "OB_NORMAL"):
        return [["Saving your answer for every public holiday", "learned facts · source: onboarding"]]
    if b in ("OB_EVERY_YEAR", "OB_ASK_EACH"):
        return [["Remembering your choice", "standing permission: scoped, and you can undo it"]]
    if b == "STOP":
        return [["Stopping all messages", "opt-out respected everywhere"]]
    if b == "DECLINE":
        return [["Remembering your no", "never suggested again"]]
    if b == "EDIT":
        return [["Preparing your card", "signed, single-use link · no login"]]
    if b == "UNDO":
        return [["Removing what Connect set", "Yext API · read-back"], ["Forgetting that answer", "learned facts retired"]]
    if c.startswith("rr:") and b.startswith("APPROVE"):
        return [["Saving your reply to the reviews", "would post through Yext Reviews · not posted in this demo"]]
    if c.startswith("drift:") and b.startswith("APPROVE"):
        return [["Re-reading your listing first", "owner wins"], ["Writing your weekly hours to Yext", "weekly hours only"],
                ["Reading it back", "a tick only when Yext confirms"]]
    if b.startswith("APPROVE") or b == "YES" or b.startswith("Q_"):
        return [["Re-reading your listing first", "owner wins: your own edits are never overwritten"],
                ["Writing your holiday hours to Yext", "only holiday hours · the full future list"],
                ["Reading it back", "a tick only when Yext confirms"],
                ["Working out when each app shows it", "each app's update speed"],
                ["Remembering your answer for next year", "learned facts"]]
    return [["Sending your answer", None]]


READ_STEPS = [["Claude reads your message", "Claude Opus 5 · strict format · rule-based fallback"],
              ["Checking the days and hours it found", "read back to you before anything is saved"]]
ASK_STEPS = [["Finding the days in your question", "holiday names and dates in four languages"],
             ["Following your memory graph", "graph RAG · links between your days, answers and decisions"],
             ["Claude answers only from those facts", "Claude Opus 5 · a source on every sentence"],
             ["Checking every date and time against the facts", "or it says it doesn't know"]]
CLOSED_TODAY_STEPS = [["Checking it's really you", "your registered number · rate limit"],
                      ["Writing today's closure to Yext", "ends by itself tonight"],
                      ["Telling you which apps show it fast", "Google in about 2 minutes"],
                      ["Keeping no reason", "nothing about why is stored (GDPR)"]]
# "Today" inside the story, where it matters to what the owner types: "closed today" on 12 December
# closes 12 December. Elsewhere the real date is used.
STORY_DAY = {"life": date(2026, 12, 12)}
EXAMPLES = [("FI-D0001", "Kahvila Aurora", "Helsinki"), ("FI-D0017", "Kahvila Kipinä", "Turku"),
            ("SE-D0016", "Restaurang Fyren", "Malmö"), ("FI-D0012", "Tilitoimisto Otso", "Jyväskylä")]


def _db():
    return dbmod.get_db()


def _demo():
    from app import demo
    return demo


def _current() -> dict | None:
    eid = (request.cookies.get(OWNER_COOKIE) or "").strip().upper()
    if not eid:
        return None
    return _db()[COLLECTION].find_one({"entity_id": eid}, {"_id": 0})


def _resolve(raw: str) -> str | None:
    """A business from what the owner typed or picked: "Name, City · ID", an ID, or a name (and city).
    Names repeat across the directory, so the ID wins whenever it is there."""
    import re
    raw = (raw or "").strip()
    if not raw:
        return None
    for cand in re.findall(r"\b[A-Z]{2}-[A-Z0-9]{4,6}\b", raw.upper()):
        try:
            data.entity(cand)
            return cand
        except KeyError:
            continue
    name, _, city = (s.strip().lower() for s in raw.split("·")[0].partition(","))
    rows = _demo().overview()
    same_name = [r for r in rows if r["name"].lower() == name]
    if city:
        same_name = [r for r in same_name if str(r["city"]).lower() == city] or same_name
    part = same_name or [r for r in rows if name and name in r["name"].lower()]
    return part[0]["entity_id"] if part else None


def _channels_status() -> dict:
    from cs.channels import email as email_ch, whatsapp as wa_ch
    import os
    v = os.environ.get("WHATSAPP_TEST_TO", "").strip()
    masked = (v[:4] + "•" * max(0, len(v) - 7) + v[-3:]) if len(v) > 7 else v
    return {"real_email": email_ch.is_configured(), "real_whatsapp": wa_ch.is_configured(), "wa_to": masked,
            "email_to": os.environ.get("SMTP_TEST_TO", "")}


# ---------------------------------------------------------------------------
# Landing, join
# ---------------------------------------------------------------------------
@bp.get("/")
def landing():
    j = _current()
    return render_template("ui/landing.html", journey=j, name=data.entity(j["entity_id"]).get("name") if j else None)


def _label(eid: str) -> str:
    row = _demo()._business_row(eid) or {}
    return f"{data.entity(eid).get('name', eid)}, {row.get('city', '')} · {eid}"


def _start_page(picked: str | None, error: str | None = None, status: int = 200):
    contact = compose_bridge._contact_base(picked) if picked else {}
    businesses = _demo().overview()
    return render_template("ui/start.html", businesses=businesses, examples=EXAMPLES, picked=picked,
                           picked_name=_label(picked) if picked else "",
                           owner_channel=contact.get("channel"), error=error, join_steps=join_steps(len(businesses)),
                           **_channels_status()), status


@bp.get("/start")
def start():
    return _start_page(_resolve(request.args.get("b", "")))


def _fresh_start(eid: str, target: str) -> None:
    """A new owner joins: an empty listing for the season, no earlier answers, an empty phone. What Fonecta
    already knew before you joined (its CRM's standing permission and the 2025 pilot) is kept."""
    from cs import audit
    db = _db()
    _demo().reset_business(eid, target, clear_thread=True)
    retired = db.learned_facts.update_many(
        {"entity_id": eid, "superseded_by": None, "source": {"$nin": ["PILOT_2025"]}},
        {"$set": {"superseded_by": "journey-start"}}).modified_count
    revoked = db.permissions.update_many(
        {"entity_id": eid, "status": {"$in": ["GRANTED", "USED"]}, "source": {"$ne": "CRM_2025"}},
        {"$set": {"status": "REVOKED", "revoked_reason": "DEMO_RESET", "revoked_at": utcnow()}}).modified_count
    db["onboarding"].update_many({"entity_id": eid, "status": {"$ne": "DEMO_RESET"}},
                                 {"$set": {"status": "DEMO_RESET"}})
    _demo().forget_graph(eid)    # the memory graph is rebuilt from what is true now, on the first question
    audit.log("JOURNEY_START", {"facts_retired": retired, "permissions_revoked": revoked}, entity_id=eid)


@bp.post("/start")
def start_post():
    eid = _resolve(request.form.get("business", ""))
    if eid is None:
        return _start_page(None, "We couldn't find that business. Pick one from the list.", 400)
    channel = request.form.get("channel") or "owner"
    channel = channel if channel in ("WHATSAPP", "SMS", "EMAIL") else "owner"
    lang = "owner" if request.form.get("lang") == "owner" else "en"
    target = season.default_target(eid)
    _fresh_start(eid, target)
    compose_bridge.DEMO_OVERRIDES = True
    override = None if channel in ("owner", "SMS") else channel   # SMS is simulated on the phone either way
    compose_bridge.set_demo_override(eid, lang="en" if lang == "en" else None, channel=override)
    from cs.suggest import engine_adapter
    _db()[COLLECTION].replace_one({"entity_id": eid}, {
        "entity_id": eid, "stage": 0, "channel": override or compose_bridge._contact_base(eid).get("channel"),
        "lang": lang, "target": target, "engine": "ml" if engine_adapter.available(2026) else "baseline",
        "results": {}, "created_at": utcnow(), "updated_at": utcnow()}, upsert=True)
    _welcome(eid)
    demo = _demo()
    r = redirect("/me")
    r.set_cookie(OWNER_COOKIE, eid, max_age=7 * 86400, samesite="Lax")
    # keep the switches the phone's buttons read in step with this choice
    r.set_cookie(demo.LANG_COOKIE, lang, max_age=30 * 86400, samesite="Lax")
    r.set_cookie(demo.CHANNEL_COOKIE, override or "owner", max_age=30 * 86400, samesite="Lax")
    return r


def _welcome(eid: str) -> None:
    """Onboarding on the owner's own channel (cs.onboarding). A plain welcome if that isn't available yet."""
    try:
        from cs import onboarding
        onboarding.send_welcome(eid)
        return
    except Exception:  # noqa: BLE001 - the module may still be in progress
        pass
    c = compose_bridge.contact(eid)
    name = data.entity(eid).get("name", eid)
    text = (f"Connect Suggests · automated assistant\nWelcome to Connect, {name}! Before each holiday season I'll "
            "check what Google, Apple Maps and AI assistants will show, and message you here only if something "
            "is wrong. One tap to approve, no login. Reply STOP any time.")
    compose_bridge.send(eid, {"text": text, "buttons": [], "parts": {}, "composer": "journey"}, kind="FOLLOWUP",
                        lang=c["lang"], channel=c["channel"])


# ---------------------------------------------------------------------------
# Your season
# ---------------------------------------------------------------------------
@bp.get("/me")
def me():
    j = _current()
    if j is None:
        return redirect("/start")
    eid = j["entity_id"]
    now = min(j["stage"], len(STAGES) - 1)
    keys = [st["key"] for st in STAGES]
    want = request.args.get("day", "")
    view = keys.index(want) if want in keys and keys.index(want) <= now else now
    days = [dict(st, day=st["day"] or date.today().strftime("%A %-d %B %Y")) for st in STAGES]
    stage = days[view]
    ctx = _stage_context(j, stage["key"])
    looking_back = view < now
    if looking_back:
        ctx["chips"] = []                 # you can look back, but you answer on the day you're at
    c = compose_bridge.contact(eid)
    demo = _demo()
    channel = demo.CHANNEL_NAMES.get(c["channel"], c["channel"])
    lang_name = LANG_NAMES.get(c["lang"], "English")
    nxt = days[now + 1] if now + 1 < len(days) else None
    return render_template("ui/me.html", j=j, stages=days, stage=stage, idx=view, now_idx=now, eid=eid,
                           looking_back=looking_back, name=data.entity(eid).get("name", eid),
                           row=demo._business_row(eid) or {}, channel=channel, lang_name=lang_name,
                           next_stage=nxt, next_steps=next_steps(nxt["key"], channel) if nxt else [],
                           chain=_chain(j, stage["key"], ctx, channel, lang_name), read_steps=READ_STEPS,
                           memory_steps=MEMORY_STEPS,
                           last=now >= len(STAGES) - 1, **_channels_status(), **ctx)


@bp.post("/me/continue")
def me_continue():
    j = _current()
    if j is None:
        return redirect("/start")
    nxt = min(j["stage"] + 1, len(STAGES) - 1)
    if nxt != j["stage"]:
        result = _run_stage(j, STAGES[nxt]["key"])
        if isinstance(result, dict):
            card = _db()["cards"].find_one({"entity_id": j["entity_id"]}, {"id": 1}, sort=[("created_at", -1)])
            result.setdefault("card_id", (card or {}).get("id"))
        _db()[COLLECTION].update_one({"entity_id": j["entity_id"]}, {"$set": {
            "stage": nxt, f"results.{STAGES[nxt]['key']}": result, "updated_at": utcnow()}})
    return redirect("/me")


@bp.post("/me/restart")
def me_restart():
    j = _current()
    r = redirect("/start" + (f"?b={j['entity_id']}" if j else ""))
    if j:
        _db()[COLLECTION].delete_one({"entity_id": j["entity_id"]})
    r.delete_cookie(OWNER_COOKIE)
    return r


@bp.post("/me/say")
def me_say():
    """A suggested reply, as if the owner typed it on the phone."""
    from cs import decide_flow
    j = _current()
    if j is None:
        return ("", 204)
    eid = j["entity_id"]
    demo = _demo()
    text = (request.form.get("text") or "").strip()[:300]
    note = None
    if text:
        stage = STAGES[min(j["stage"], len(STAGES) - 1)]["key"]
        channel = j["channel"] if j["channel"] in ("WHATSAPP", "SMS", "EMAIL") else "SMS"
        res = decide_flow.handle_reply(eid, text=text, channel=channel, sender=demo._owner_phone(eid),
                                       base_url=request.host_url, today=STORY_DAY.get(stage))
        if not res.get("handled"):
            note = f"Not applied: {res.get('reason')}"
        else:
            demo.after_decision(eid)
    return demo._changed(demo._phone(eid, note))


def _run_stage(j: dict, key: str) -> dict:
    """What Connect does by itself when the season reaches this point."""
    from cs import profile_flow
    demo = _demo()
    eid, target, engine = j["entity_id"], j["target"], j["engine"]
    base = request.host_url
    if key == "season":
        return demo.run_check(eid, engine, target, base, offers=False)   # one message: the season card
    if key == "life":
        out = {}
        for k, fn in (("drift", profile_flow.offer_drift), ("reviews", profile_flow.offer_review_replies)):
            try:
                out[k] = fn(eid)
            except Exception:  # noqa: BLE001
                out[k] = None
        return out
    if key == "next":
        demo.reset_business(eid, target)   # a new season: nothing entered on the listing yet
        return demo.run_check(eid, engine, target, base, offers=False)
    if key == "year":
        return _summary(j)
    return {}


# ---------------------------------------------------------------------------
# What the page shows at each point (and the machine learning, visibly)
# ---------------------------------------------------------------------------
def _stage_context(j: dict, key: str) -> dict:
    demo = _demo()
    eid = j["entity_id"]
    res = (j.get("results") or {}).get(key) or {}
    ctx: dict = {"result": res, "chips": [], "insight": None}
    # Suggested replies under the phone, as (label, what gets sent). Message buttons are tapped in the
    # thread itself; these are for what an owner would type.
    sms = compose_bridge.contact(eid)["channel"] == "SMS"
    approve_chip = ([{"label": "Reply 1 = Approve", "said": "1", "steps": phone_steps("APPROVE")}]
                    if sms and res.get("kind") == "card" else [])
    if key == "welcome":
        plan, quiet = demo._plan_rows(eid)
        ob = _db()["onboarding"].find_one({"entity_id": eid}, {"status": 1}, sort=[("created_at", -1)]) or {}
        ctx.update(insight={"kind": "welcome", "days": len(plan) + quiet, "fix": len(plan), "quiet": quiet},
                   chips=[{"label": "10–14", "said": "10-14", "steps": READ_STEPS}] if ob.get("status") == "HOURS" else [])
    elif key == "season":
        plan, quiet = demo._plan_rows(eid)
        row = demo._business_row(eid) or {}
        on_card = len(res.get("rows") or [])            # the card's dates and its one question
        ctx.update(insight={"kind": "season", "plan": _card_insight(eid, res, plan), "quiet": quiet,
                            "total": len(plan) + quiet, "right": max(len(plan) + quiet - on_card, 0),
                            "per_day": round(row.get("per_day") or 0)},
                   chips=approve_chip + [{"label": "Why these hours?", "said": "Why do you suggest these hours?",
                                          "steps": ASK_STEPS}])
    elif key == "life":
        try:
            from cs.profile import drift
            d = drift.drift_suggestion(eid)
            changes = drift.changed_days(drift.week_of(d.shown), drift.week_of(d.value)) if d else []
        except Exception:  # noqa: BLE001
            changes = []
        offer_id = ((res or {}).get("reviews") or {}).get("offer_id")
        offer = _db()["profile_offers"].find_one({"id": offer_id}, {"drafts": 1}) if offer_id else None
        ctx.update(insight={"kind": "life", "drift": changes, "reviews": len((offer or {}).get("drafts") or [])},
                   chips=[{"label": "Closed today", "said": "closed today", "steps": CLOSED_TODAY_STEPS},
                          {"label": "Ask: Christmas Eve hours?", "said": "What hours do we have on Christmas Eve?",
                           "steps": ASK_STEPS}])
    elif key == "next":
        from cs import learn
        facts = [{"date": f.date, "day_key": f.day_key, "value": f.value, "source": f.source}
                 for f in learn.active_facts(eid)]
        perms = [p for ps in learn.active_permissions(eid).values() for p in ps]
        try:
            from cs import onboarding
            said = onboarding.prefers_every_year(eid)
        except Exception:  # noqa: BLE001
            said = False
        ctx.update(insight={"kind": "next", "facts": facts[:8], "every_year": bool(perms), "said_every_year": said},
                   chips=approve_chip)
    elif key == "year":
        ctx.update(insight={"kind": "year", "summary": _summary(j)})     # live: later answers count too
    return ctx


TOLD_BY = {"ONBOARDING": "You told Connect when you joined", "OWNER_ANSWER": "You told Connect",
           "PILOT_2025": "Your answer in Fonecta's 2025 pilot"}


def _card_insight(eid: str, res: dict, plan: list[dict]) -> list[dict]:
    """The dates on the card Connect actually sent, each with its evidence: for a date the owner told us
    about, where that came from and whether the model agrees (the owner's answer wins either way); for the
    rest, the engine's evidence and confidence. The engine's plan when no card was sent."""
    from app.app import hours_text
    from cs import learn
    rows = [r for r in (res or {}).get("rows") or [] if not r.get("question")]
    if not rows:
        return plan
    by_date = {p["date"]: p for p in plan}
    facts = {f.date: f for f in learn.active_facts(eid)}
    out = []
    for r in rows:
        p = by_date.get(r["date"]) or {}
        told = r.get("why") == "What you told us"
        if told:
            evidence = [TOLD_BY.get(getattr(facts.get(r["date"]), "source", ""), "You told Connect earlier")]
            same = str(p.get("value") or "").lower() == str(r.get("value") or "").lower()
            if p and not p.get("flag") and same:
                pct = f" ({round(p['sure'] * 100)}% sure)" if p.get("sure") is not None else ""
                evidence.append(f"Connect's model agrees{pct}")
            elif p and not p.get("flag"):
                evidence.append(f"Connect's model expected {hours_text(p.get('value'))}; your answer wins")
            else:
                evidence.append("Connect's model alone wouldn't have changed this day; your answer wins")
        else:
            evidence = p.get("evidence") or [r.get("why") or ""]
        out.append({"date": r["date"], "day_key": r["day_key"], "shown": r.get("shown"), "value": r.get("value"),
                    "flag": False, "why": r.get("why"), "sure": None if told else p.get("sure"),
                    "evidence": evidence})
    return out


def _summary(j: dict) -> dict:
    """The season in numbers, from what actually happened (the outbox, the cards, Autopilot)."""
    from cs.domain import Card
    db = _db()
    eid = j["entity_id"]
    since = j.get("created_at")
    q = {"entity_id": eid, **({"at": {"$gte": since}} if since else {})}
    from cs.channels.base import PROACTIVE_KINDS
    out_msgs = db.outbox.count_documents({**q, "direction": {"$ne": "in"}})
    started = db.outbox.count_documents({**q, "direction": {"$ne": "in"}, "kind": {"$in": sorted(PROACTIVE_KINDS)}})
    taps = db.outbox.count_documents({**q, "direction": "in"})
    cards = dbmod.find(Card, {"entity_id": eid, **({"created_at": {"$gte": since}} if since else {})})
    fixed = set()
    for c in cards:
        fixed.update(((c.result or {}).get("applied") or {}).keys())
    for k in ("season", "next"):
        r = (j.get("results") or {}).get(k) or {}
        if r.get("kind") == "autopilot":
            fixed.update(row["date"] for row in r.get("rows", []))
    row = _demo()._business_row(eid) or {}
    per_day = row.get("per_day") or 0
    return {"messages": out_msgs, "started": started, "replies": taps, "fixed": len(fixed),
            "seen": round(per_day * len(fixed)),
            "dates": sorted(fixed)}


@bp.get("/me/listing")
def me_listing():
    """The owner's listing as Google and the other apps show it on the coming special days."""
    from cs.hours import week_from_yext
    from cs.yext import holiday_map
    j = _current()
    if j is None:
        return ""
    eid, target = j["entity_id"], j["target"]
    try:
        with season.yext_client(target) as yc:
            ent = yc.get_entity(eid)
    except Exception as e:  # noqa: BLE001
        return render_template("ui/_google.html", error=str(e))
    week = week_from_yext(ent.get("hours") or {})
    hol = holiday_map(ent)
    t = data.targets(2026)
    t = t[t["entity_id"] == eid].sort_values("date")
    # Always show the days Connect set or asked about (they may be late in the season), then fill up with
    # the next special days.
    asked = {r["date"] for k in ("season", "next") for r in ((j.get("results") or {}).get(k) or {}).get("rows") or []}
    recs = t.to_dict("records")
    picked = [r for r in recs if r["date"] in hol or r["date"] in asked]
    for r in recs:
        if len(picked) >= 8:
            break
        if r not in picked:
            picked.append(r)
    rows = []
    for r in sorted(picked, key=lambda r: r["date"]):
        d = r["date"]
        set_by_connect = d in hol
        hours = hol.get(d) if set_by_connect else week[datetime.strptime(d, "%Y-%m-%d").weekday()]
        rows.append({"date": d, "day_key": r["day_key"], "hours": "regular" if hours == "REGULAR" else hours,
                     "set": set_by_connect})
    return render_template("ui/_google.html", error=None, name=ent.get("name", ""), rows=rows,
                           updated=datetime.now(timezone.utc).strftime("%H:%M UTC"), target=target)


@bp.get("/how")
def how():
    return render_template("ui/how.html")


# ---------------------------------------------------------------------------
# The five steps, as they happened on the day you're looking at
# ---------------------------------------------------------------------------
OPEN_CARD = ("DRAFT", "SCHEDULED", "SENT", "QUESTION_ANSWERED", "NO_REPLY", "REMINDED")
DETECT_TECH = "display rules · 100% match with Fonecta's labels"
SUGGEST_TECH = "gradient-boosted trees · calibrated confidence · expected points on Fonecta's scoring table"
APPLY_TECH = "Yext API · owner wins: re-read before writing · read-back"


def _card_of(res: dict, eid: str) -> dict:
    q = {"id": res["card_id"]} if (res or {}).get("card_id") else {"entity_id": eid}
    return _db()["cards"].find_one(q, {"_id": 0, "status": 1, "approve_by": 1, "result": 1},
                                   sort=[("created_at", -1)]) or {}


def _shows_on(card: dict) -> str:
    rows = ((card or {}).get("result") or {}).get("where_it_shows") or []
    out = []
    for name in ("Google", "Apple Maps", "AI assistants"):
        r = next((w for w in rows if str(w.get("publisher", "")).startswith(name) and w.get("status") == "LIVE"), None)
        if r:
            out.append(f"{name} {r.get('eta_text')}")
    return ", ".join(out)


def _short(d: str | None) -> str:
    try:
        return date.fromisoformat(str(d)[:10]).strftime("%a %-d %b")
    except ValueError:
        return str(d or "")


def _via(channel: str) -> str:
    return "by email" if str(channel).lower() == "email" else f"on {channel}"


def _chain(j: dict, key: str, ctx: dict, channel: str, lang_name: str) -> list[dict]:
    """What Connect did that day, step by step, each with the model or rule behind it.
    ``ours`` marks what we added beyond what Fonecta's brief asked for."""
    from app.app import hours_text
    eid = j["entity_id"]
    res = ctx.get("result") or {}
    ins = ctx.get("insight") or {}
    rows: list[dict] = []

    def add(step, text, tech=None, ours=False, wait=False):
        rows.append({"step": step, "text": text, "tech": tech, "ours": ours, "wait": wait})

    def n_days(n: int) -> str:
        return f"{n} special {'day' if n == 1 else 'days'}"

    if key == "welcome":
        add("1 · Detect", f"Connect read your listing and checked all {n_days(ins.get('days', 0))} of this season: "
            f"{ins.get('fix', 0)} will be wrong on Google, {ins.get('quiet', 0)} are already right.", DETECT_TECH, ours=True)
        add("3 · Contact", f"It said hello {_via(channel)}: that it's an automated assistant, how to stop, and one "
            "question it can't know yet, how you handle public holidays.", "onboarding in your own chat · no form, no login",
            ours=True)
        ob = _db()["onboarding"].find_one({"entity_id": eid, "status": {"$ne": "DEMO_RESET"}},
                                          sort=[("created_at", -1)]) or {}
        habit = {"OB_CLOSED": "closed", "OB_SHORT": "shorter hours", "OB_NORMAL": "open as normal"}.get(ob.get("habit"), "")
        st = ob.get("status")
        if st == "DONE":
            add("Learn", f"Saved: {habit} on public holidays, " + ("every year. " if ob.get("every_year") else
                "and Connect asks each time. ") + "Your first card will say “as you told us”.",
                "learned facts" + (" · standing permission, which you can undo" if ob.get("every_year") else ""), ours=True)
        elif st == "YEAR":
            add("Learn", f"Saved: {habit} on public holidays. One more tap: every year, or ask each time?",
                "learned facts", ours=True, wait=True)
        elif st == "STOPPED":
            add("Learn", "You replied STOP, so Connect won't message you.", "opt-out respected everywhere")
        else:
            add("Learn", "Waiting for your answer on the phone. It becomes Connect's memory.", "learned facts",
                ours=True, wait=True)
        return rows

    if key in ("season", "next"):
        kind = res.get("kind")
        items = [r for r in res.get("rows") or [] if not r.get("question")]
        question = next((r for r in res.get("rows") or [] if r.get("question")), None)
        card = _card_of(res, eid) if kind in ("card", "autopilot", "waiting") else {}
        if key == "next":
            facts = ins.get("facts") or []
            add("Learn", f"Connect remembered {len(facts)} {'answer' if len(facts) == 1 else 'answers'} you gave it."
                if facts else "Connect had no answers from you to remember.",
                "learned facts · the newest answer wins · older ones count less each season")
        if kind == "flagged":
            add("1 · Detect", "Reviews and map users suggest this business may have closed for good.",
                "closure words in reviews, four languages · map users marking it closed", ours=True)
            add("Flag", "So Connect sends no message: a Fonecta person checks first.", "FLAG for a person")
            return rows
        if kind in ("quiet", "learned"):
            add("1 · Detect", "Every special day is already right on Google and every other app.", DETECT_TECH, ours=True)
            add("3 · Contact", "So Connect sends nothing. Staying quiet is part of the job.",
                "expected points: a needless message costs more than it gains", ours=True)
            return rows
        if kind == "waiting":
            add("3 · Contact", "Connect is waiting for your answer to the card it already sent: it never asks twice.")
            return rows
        if kind == "error":
            add("1 · Detect", f"Connect couldn't reach your listing just now: {res.get('text')}", wait=True)
            return rows
        n_wrong = len(items) + (res.get("asked") or 0 if kind == "autopilot" else 0)
        if key == "season" and ins.get("total"):
            by_you = sum(1 for p in ins.get("plan") or [] if any("wouldn't have changed" in e for e in p.get("evidence") or []))
            detect = (f"Connect read your listing from Yext and checked all {n_days(ins['total'])}: {n_wrong} will be "
                      "wrong on Google" + (f" ({n_wrong - by_you} found by the model, {by_you} from what you told it)"
                                           if by_you else "")
                      + (", 1 it isn't sure about" if question else "")
                      + f", and {ins.get('right', 0)} are already right, so they're never mentioned.")
        else:
            detect = ("A new season, nothing entered yet. Connect read your listing from Yext and predicted the season "
                      f"again: {n_days(n_wrong)} will be wrong on Google.")
        add("1 · Detect", detect, DETECT_TECH, ours=True)
        told = any("told" in " ".join(p.get("evidence") or []) for p in ins.get("plan") or [])
        if kind == "autopilot":
            n, asked = len(items), res.get("asked") or 0
            add("Agreement check", f"You said “every year”. This year's model agrees on {n_days(n)}, so Connect set "
                f"{'it' if n == 1 else 'them'} itself" + (f"; it asks you about the {asked} it isn't sure of." if asked else "."),
                "standing permission × model agreement: blind re-use is wrong 10.1% of the time, with the check 3.5%",
                ours=True)
            shows = _shows_on(card)
            add("5 · Apply", "Re-read your listing first, wrote it to Yext and read it back."
                + (f" Shows on {shows}." if shows else ""), APPLY_TECH, ours=True)
            add("Contact", f"Then a short note {_via(channel)} with Undo and Stop: you stay in control.",
                "standing permission you can undo")
            return rows
        add("2 · Suggest", "It predicted what you'll really do on each day, and how sure it is (below)."
            + (" Your own answers come first." if told else ""), SUGGEST_TECH, ours=True)
        by = _short(card.get("approve_by")) if card.get("approve_by") else None
        contact = f"One message {_via(channel)}, in {lang_name}: the fix and the reason"
        contact += f", and “answer by {by}” so even AI assistants update in time" if by else ""
        contact += f". One question: {_short(question.get('date'))}." if question else "."
        add("3 · Contact", contact, "answer-by date from each app's update speed"
            + (" · the question picked by value of information" if question else ""), ours=True)
        status = card.get("status")
        if status in OPEN_CARD or not status:
            add("4 · Decide", "Waiting for you: one tap, your own words, or the link. No login.",
                "signed single-use link · Claude reads free text and reads it back", ours=True, wait=True)
            add("5 · Apply", "Nothing is written until you say yes.", APPLY_TECH, wait=True)
        else:
            applied = ((card.get("result") or {}).get("applied") or {})
            shows = _shows_on(card)
            add("4 · Decide", f"You answered {_via(channel)}.", "signed single-use link · Claude reads free text and reads it back",
                ours=True)
            add("5 · Apply", f"Re-read your listing first (your own edits win), wrote {n_days(len(applied))} to Yext and "
                "read them back." + (f" Shows on {shows}." if shows else ""), APPLY_TECH, ours=True)
        return rows

    if key == "life":
        drift = ins.get("drift") or []
        if drift:
            parts = [f"{d[0][:3].title()}{('–' + d[-1][:3].title()) if len(d) > 1 else ''} "
                     f"{hours_text(before)} → {hours_text(after)}" for d, before, after in drift]
            add("All year · hours", "Reviews and Google users suggest your weekly hours changed: " + "; ".join(parts)
                + ". Connect asked you on the phone.",
                "drift detector: reviews + map-user edits · logistic regression · every week it proposed in testing was right",
                ours=True)
        else:
            add("All year · hours", "Your weekly hours look right: nothing to ask.",
                "drift detector: reviews + map-user edits · logistic regression", ours=True)
        n = ins.get("reviews") or 0
        if n:
            add("All year · reviews", f"{n} {'customer says' if n == 1 else 'customers say'} they found the door closed. "
                "Connect drafted a reply in each reviewer's language for you to approve.",
                "facts from your confirmed hours only · not posted in this demo", ours=True)
        add("All year · closures", "Suddenly closed? Text “closed today”: fast apps show it within minutes, and it "
            "ends by itself. No reason is stored.", "sender check · rate limit · GDPR", ours=True)
        add("All year · questions", "Ask anything about your hours: Connect answers only from your own memory graph "
            "and names its sources.", "graph RAG · Claude Opus 5 · every date and time checked", ours=True)
        return rows

    if key == "year":
        add("Trust", "Every message, decision and write this season is in a tamper-evident audit log.",
            "hash-chained log · Verify on the staff page", ours=True)
    return rows


# ---------------------------------------------------------------------------
# Connect's memory: the business's knowledge graph, with the model's predictions on it
# ---------------------------------------------------------------------------
MEMORY_STEPS = [["Rebuilding your memory graph", "your days, answers, decisions, similar businesses, reviews (MongoDB)"],
                ["Adding the trained models' predictions", "the nightly batch: gradient-boosted trees, calibrated"],
                ["Working out next season's decision for each day", "your answer × the model's probability"]]


@bp.get("/me/memory")
def me_memory():
    j = _current()
    if j is None:
        return redirect("/start")
    eid = j["entity_id"]
    return render_template("ui/memory.html", name=data.entity(eid).get("name", eid), eid=eid, mem=_memory(j))


def _memory(j: dict) -> dict:
    """The graph drawn around the business: this season's special days in a ring; on each day the model's
    prediction, your newest answer and your decision this season; linked holidays inside the ring; similar
    businesses and reviews as context. For each day, next season's rule: your answer is applied only if the
    model gives it at least 50% (the agreement check), otherwise Connect asks."""
    import math
    from cs import display, learn
    from cs.engine.api import predict
    from cs.kg.build import ensure_built
    from app.app import holiday_name, hours_text
    eid = j["entity_id"]
    db = _db()
    ensure_built(eid)
    nodes = {n["id"]: n for n in db.kg_nodes.find({"entity_id": eid}, {"_id": 0})}
    edges = list(db.kg_edges.find({"entity_id": eid}, {"_id": 0}))
    df = predict(2026)
    rows = df[df.entity_id == eid].sort_values("date")
    facts: dict = {}
    for f in learn.active_facts(eid):                      # newest first
        facts.setdefault(str(f.date)[:10], f)
    started = j.get("created_at")
    decs: dict = {}
    q = {"entity_id": eid, **({"at": {"$gte": started}} if started else {})}
    for d in db.decisions.find(q, {"_id": 0}).sort("at", 1):
        decs[str(d.get("date"))[:10]] = d
    peers: dict = {}
    for n in nodes.values():
        p = n.get("props") or {}
        if n["type"] == "PEER_GROUP" and p.get("day_key") and p.get("season", 0) >= peers.get(p["day_key"], {}).get("season", 0):
            peers[p["day_key"]] = p
    reviews = [n.get("props") or {} for n in nodes.values() if n["type"] == "REVIEW"]
    by_id_date = {nid: (n.get("props") or {}).get("date") for nid, n in nodes.items() if n["type"] == "SPECIAL_DAY"}

    cx, cy, n_days = 380, 360, max(len(rows), 1)
    days, index = [], {}
    for i, r in enumerate(rows.itertuples()):
        date = str(r.date)[:10]
        a = math.radians(-90 + i * 360 / n_days)
        pos = lambda rad: (round(cx + rad * math.cos(a), 1), round(cy + rad * math.sin(a), 1))   # noqa: E731
        cands = list(r.candidates) if r.candidates is not None else []
        prob = None if r.probability != r.probability else float(r.probability)

        def p_for(v, r=r, cands=cands, prob=prob):
            if r.action == "SUGGEST" and r.hours == v and prob is not None:
                return prob
            for c in cands:
                if c.get("hours") == v and c.get("prob") is not None:
                    return float(c["prob"])
            return 0.0 if cands else None
        shown = display.shown_hours(eid, date, as_of="2026-11-06")
        fact, dec = facts.get(date), decs.get(date)
        agree = None
        if fact is not None:
            pv = p_for(fact.value)
            agree = {"p": pv, "apply": pv is not None and pv >= 0.5}
        peer = peers.get(r.day_key)
        model_value = r.hours if r.action == "SUGGEST" else shown
        day = {"i": i, "date": date, "short": date[8:10] + "." + date[5:7] + ".", "name": holiday_name(r.day_key),
               "xy": pos(150), "label_xy": pos(276), "anchor": "middle" if abs(math.cos(a)) < 0.3 else ("end" if math.cos(a) < 0 else "start"),
               "m_xy": pos(186), "f_xy": pos(214), "d_xy": pos(240),
               "shown": hours_text(shown), "action": r.action, "basis": str(r.basis or ""),
               "model": hours_text(model_value), "p": None if prob is None else round(prob * 100),
               "fact": hours_text(fact.value) if fact else None,
               "fact_source": {"ONBOARDING": "when you joined", "OWNER_ANSWER": "you approved it",
                               "OWNER_QUESTION": "you answered the question", "PILOT_2025": "Fonecta's 2025 pilot"}.get(
                                   fact.source, "you told Connect") if fact else None,
               "decision": {"APPROVE": "approved", "EDIT": "edited", "DECLINE": "declined"}.get(
                   dec.get("decision"), (dec.get("decision") or "").lower()) if dec else None,
               "agree_p": None if not agree or agree["p"] is None else round(agree["p"] * 100),
               "apply": bool(agree and agree["apply"]),
               "peer": None if not peer else {"n": peer.get("n_businesses"), "group": peer.get("group"),
                                              "closed": round(100 * (peer.get("share_closed_of_entered") or 0))}}
        index[date] = day
        days.append(day)
    links = []
    for e in edges:
        if e["type"] == "LINKED_HOLIDAY":
            a_, b_ = index.get(by_id_date.get(e["src"])), index.get(by_id_date.get(e["dst"]))
            if a_ and b_ and a_["i"] < b_["i"]:
                links.append({"a": a_["xy"], "b": b_["xy"], "w": round(float(e.get("weight") or 0.5), 2)})
    n_facts = sum(1 for d in days if d["fact"])
    return {"center": (cx, cy), "days": days, "links": links, "reviews": reviews[:3], "n_reviews": len(reviews),
            "n_peers": len(peers), "n_facts": n_facts, "n_decisions": sum(1 for d in days if d["decision"]),
            "n_apply": sum(1 for d in days if d["fact"] and d["apply"]),
            "n_ask": sum(1 for d in days if d["fact"] and not d["apply"])}
