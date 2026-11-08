"""Fonecta's side of Connect: every business, what Connect will do for it this season, and doing it.

Nothing here is scripted per business. The list comes from the engine's predictions for all
businesses; the business page shows that business's own plan and offers the one action that
fits it, which ``run_check`` then carries out exactly as production would:

- a standing permission ("every year", or Fonecta's CRM flag)  -> Autopilot sets the dates and
  sends the owner a note with Undo;
- hours that look wrong                                          -> one card to the owner;
- everything already right                                       -> stay quiet;
- probably closed for good                                       -> flag for a person, no message;
- weekly hours that changed                                      -> also ask about those.

The owner's phone sits next to it: replies, taps, "closed today" and questions go through the same
code as the WhatsApp/SMS webhooks.

    GET  /                         all businesses (search, filter by what Connect will do)
    GET  /b/<id>                   one business: its season, the next action, the owner's phone
    POST /b/<id>/lang              demo only: messages in English or in the owner's language
    POST /demo/check               the next action (the business page's button)
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

from flask import Blueprint, abort, make_response, redirect, render_template, request

from cs import audit, compose_bridge, data, db as dbmod, season
from cs.compose_bridge import thread
from cs.domain import Card, Message
from cs.hours import DAYS, week_from_yext
from cs.suggest import engine_adapter

bp = Blueprint("demo", __name__)

SEASON = 2026
DEFAULT_BUSINESS = "FI-D0001"
LANG_COOKIE = "connect_msg_lang"          # "en" (default for the app pages) or "owner"
CHANNEL_COOKIE = "connect_msg_channel"    # "owner" (default), "WHATSAPP" or "EMAIL": to test real delivery
LANG_NAMES = {"fi": "Finnish", "sv": "Swedish", "nb": "Norwegian", "en": "English"}
CHANNEL_NAMES = {"WHATSAPP": "WhatsApp", "SMS": "SMS", "EMAIL": "email", "APP_PUSH": "the Connect app",
                 "OUTBOX": "the Connect app"}
# What Connect will do this season, from the engine's predictions (the business page then does it for real).
GROUPS = [
    ("ask", "Needs the owner's OK", "Some special days look wrong on Google: Connect sends the owner one message."),
    ("autopilot", "Autopilot", "The owner said “every year”: Connect sets the hours and sends a note with Undo."),
    ("flag", "Flagged for a person", "Probably closed for good: a Fonecta person checks, and the owner isn't messaged."),
    ("quiet", "Already right", "Google already shows the right hours: Connect stays quiet."),
    ("optout", "Opted out", "The owner opted out of suggestions: Connect sends nothing."),
]
GROUP_NAMES = {k: name for k, name, _ in GROUPS}
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
def _predictions():
    """The engine's plan for every target day of the season (one row per business and special day)."""
    import pandas as pd
    from cs.config import ROOT
    path = ROOT / "artifacts" / "predictions" / f"engine_{SEASON}.parquet"
    cols = ["entity_id", "date", "day_key", "action", "hours", "probability", "basis", "evidence"]
    if path.exists():
        return pd.read_parquet(path, columns=cols)
    from cs import export      # no engine file: Fonecta's baseline, through the same guardrails
    rows = export.build(SEASON, "baseline")
    return pd.DataFrame([{"entity_id": s.entity_id, "date": s.date, "day_key": s.day_key, "action": s.action,
                          "hours": s.value, "probability": s.probability, "basis": s.basis,
                          "evidence": [e.to_doc() for e in s.evidence]} for s in rows])


@lru_cache(maxsize=1)
def overview() -> list[dict]:
    """One row per business (shopping centres excluded): what Connect will do this season and how many
    customer lookups those days get (last season's Google search + map views on the same days)."""
    import pandas as pd
    p = _predictions()
    g = p.groupby("entity_id")["action"].agg(n_suggest=lambda s: int((s == "SUGGEST").sum()),
                                              n_flag=lambda s: int((s == "FLAG").sum())).reset_index()
    ents = data.entity_index()
    ents = ents[~ents["is_mall"].astype(bool)][["entity_id", "name", "city", "segment", "split"]]
    c = data.contacts()[["entity_id", "preferred_channel", "preferred_language", "standing_permission_holidays",
                         "opted_out_of_suggestions"]]
    df = ents.merge(g, on="entity_id", how="left").merge(c, on="entity_id", how="left")
    df[["n_suggest", "n_flag"]] = df[["n_suggest", "n_flag"]].fillna(0).astype(int)
    try:    # average daily lookups during last year's holiday season
        a = pd.read_parquet(data.dataset_path("data", "analytics_daily.parquet"),
                            columns=["date", "entity_id", "GOOGLE_SEARCH_VIEWS", "GOOGLE_MAP_VIEWS"])
        a = a[(a["date"].astype(str) >= f"{SEASON - 1}-11-07") & (a["date"].astype(str) <= f"{SEASON}-01-06")]
        per_day = (a["GOOGLE_SEARCH_VIEWS"] + a["GOOGLE_MAP_VIEWS"]).groupby(a["entity_id"]).mean()
        df["per_day"] = df["entity_id"].map(per_day).fillna(0.0)
    except Exception:  # noqa: BLE001 - the list works without it
        df["per_day"] = 0.0

    def status(r) -> str:
        if r.n_flag > 0:
            return "flag"
        if r.n_suggest == 0:
            return "quiet"
        if r.opted_out_of_suggestions == 1:
            return "optout"
        return "autopilot" if r.standing_permission_holidays == 1 else "ask"

    df["status"] = df.apply(status, axis=1)
    df["at_risk"] = (df["per_day"] * (df["n_suggest"] + df["n_flag"])).round().astype(int)
    df = df.sort_values(["at_risk", "n_suggest", "name"], ascending=[False, False, True])
    return df.to_dict("records")


def _business_row(eid: str) -> dict | None:
    return next((r for r in overview() if r["entity_id"] == eid), None)


def _msg_lang() -> str | None:
    """The tester's choice from the business page: "en", "owner", or None (not chosen: the owner's language)."""
    v = request.cookies.get(LANG_COOKIE)
    return v if v in ("en", "owner") else None


def _msg_channel() -> str | None:
    """The tester's "Send by" choice: "WHATSAPP", "EMAIL", "owner", or None (not chosen: the owner's channel)."""
    v = request.cookies.get(CHANNEL_COOKIE)
    return v if v in ("WHATSAPP", "EMAIL", "owner") else None


def _apply_lang(eid: str) -> None:
    """Demo only: the tester's choices from the business page (message language, "Send by"), applied to this
    business. Without a choice nothing is overridden, so API calls and tests keep the owner's own settings."""
    lang, channel = _msg_lang(), _msg_channel()
    if lang is None and channel is None:
        return
    compose_bridge.DEMO_OVERRIDES = True
    fields = {}
    if lang is not None:
        fields["lang"] = "en" if lang == "en" else None
    if channel is not None:
        fields["channel"] = None if channel == "owner" else channel
    compose_bridge.set_demo_override(eid, **fields)


def _latest_card(entity_id: str) -> Card | None:
    found = dbmod.find(Card, {"entity_id": entity_id}, sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def _changed(body, status: int = 200):
    """Tell the phone, the steps and the listing to refresh."""
    r = make_response(body, status)
    r.headers["HX-Trigger"] = "connect-changed"
    return r


# ---------------------------------------------------------------------------
# All businesses, and one business
# ---------------------------------------------------------------------------
PAGE = 40


@bp.get("/businesses")
def businesses_page():
    q = (request.args.get("q") or "").strip()
    group = request.args.get("group") or ""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    rows = overview()
    counts = {k: sum(1 for r in rows if r["status"] == k) for k, _, _ in GROUPS}
    counts["demo"] = sum(1 for r in rows if r["split"] == "demo")
    if group == "demo":
        rows = [r for r in rows if r["split"] == "demo"]
    elif group in GROUP_NAMES:
        rows = [r for r in rows if r["status"] == group]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["name"].lower() or ql in str(r["city"]).lower() or ql in r["entity_id"].lower()]
    total = len(rows)
    shown = rows[(page - 1) * PAGE: page * PAGE]
    return render_template("ui/businesses.html", rows=shown, total=total, page=page, pages=max(1, -(-total // PAGE)),
                           q=q, group=group, groups=GROUPS, group_names=GROUP_NAMES, counts=counts,
                           all_count=len(overview()))


@bp.get("/demo")
def demo_redirect():
    eid = request.args.get("entity_id") or request.args.get("b")
    return redirect(f"/b/{eid.strip().upper()}" if eid else "/")


def _plain(text: str) -> str:
    """Evidence for people: holiday codes become names ("BOXING_DAY" -> "Boxing Day"), ISO dates become
    "26 Dec 2025" and "07:30-13:00" gets a proper dash."""
    import re
    from datetime import date as _date
    from app.app import _holiday_names
    names = _holiday_names()
    text = re.sub(r"\b[A-Z][A-Z0-9_]{3,}\b", lambda m: names.get(m.group(0), m.group(0).replace("_", " ").title()), text)
    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                  .strftime("%-d %b %Y"), text)
    return re.sub(r"\b(\d{2}:\d{2})-(\d{2}:\d{2})\b", "\\1\u2013\\2", text)


def _plan_rows(eid: str) -> tuple[list[dict], int]:
    """This business's special days that need something (what Google shows -> Connect's plan), and how
    many others are already right."""
    from app.app import BASIS_TEXT
    from cs import display
    p = _predictions()
    p = p[p["entity_id"] == eid].sort_values("date")
    as_of = data.SNAPSHOT[SEASON]
    out, quiet = [], 0
    for r in p.to_dict("records"):
        if r["action"] == "NO_ACTION":
            quiet += 1
            continue
        try:
            shown = display.explain_shown(eid, r["date"], as_of)["shown"]
        except Exception:  # noqa: BLE001
            shown = None
        ev = r.get("evidence")
        evidence = [_plain(e.get("detail")) for e in (list(ev) if ev is not None else [])
                    if e.get("detail") and e.get("source") != "MODEL"][:3]
        out.append({"date": r["date"], "day_key": r["day_key"], "shown": shown,
                    "value": r["hours"] if r["action"] == "SUGGEST" else None, "flag": r["action"] == "FLAG",
                    "why": "Looks closed for good" if r["action"] == "FLAG" else BASIS_TEXT.get(r["basis"], r["basis"]),
                    "sure": r["probability"], "evidence": evidence})
    return out, quiet


@bp.get("/b/<eid>")
def business_page(eid: str):
    eid = eid.strip().upper()
    try:
        ent = data.entity(eid)
    except KeyError:
        abort(404)
    row = _business_row(eid) or {"status": "quiet", "n_suggest": 0, "n_flag": 0, "at_risk": 0,
                                 "city": "", "segment": "", "split": ""}
    plan, quiet = _plan_rows(eid)
    contact = compose_bridge._contact_base(eid)
    waiting = _waiting_card(eid)
    latest = _latest_card(eid)
    try:
        from cs.profile import drift
        d = drift.drift_suggestion(eid)
        drift_changes = drift.changed_days(drift.week_of(d.shown), drift.week_of(d.value)) if d else []
    except Exception:  # noqa: BLE001
        drift_changes = []
    try:
        from cs.profile import review_reply
        complaints = review_reply.entities_with_complaints([eid]).get(eid, 0)
    except Exception:  # noqa: BLE001
        complaints = 0
    _, engine, _ = _params()
    target = (request.args.get("target") or season.default_target(eid)).lower()   # this business's own listing
    choice = _msg_lang() or "en"          # the app pages show English unless the tester switches
    send_by = _msg_channel() or "owner"
    from cs.channels import email as email_ch, whatsapp as wa_ch
    owner_channel = CHANNEL_NAMES.get(contact.get("channel"), contact.get("channel", ""))
    shown_channel = owner_channel if send_by == "owner" else CHANNEL_NAMES[send_by]
    resp = make_response(render_template(
        "ui/business.html", entity_id=eid, name=ent.get("name", eid), row=row, plan=plan, quiet=quiet, join_steps=_join_steps(),
        group_name=GROUP_NAMES.get(row["status"], ""), waiting=waiting, latest=latest, engine=engine, target=target,
        channel=shown_channel, owner_channel=owner_channel, send_by=send_by,
        real_email=email_ch.is_configured(), real_whatsapp=wa_ch.is_configured(),
        owner_lang=LANG_NAMES.get(contact.get("lang"), contact.get("lang", "")), msg_lang=choice,
        drift_changes=drift_changes, complaints=complaints))
    if _msg_lang() is None:
        resp.set_cookie(LANG_COOKIE, choice, max_age=30 * 86400, samesite="Lax")
    return resp


@bp.post("/b/<eid>/lang")
def business_lang(eid: str):
    eid = eid.strip().upper()
    lang = "owner" if request.form.get("lang") == "owner" else "en"
    r = redirect(f"/b/{eid}")
    r.set_cookie(LANG_COOKIE, lang, max_age=30 * 86400, samesite="Lax")
    compose_bridge.DEMO_OVERRIDES = True
    compose_bridge.set_demo_override(eid, lang=None if lang == "owner" else "en")
    return r


@bp.post("/b/<eid>/channel")
def business_channel(eid: str):
    """Demo only: "Send by" WhatsApp or email (to test real delivery), or the owner's own channel."""
    eid = eid.strip().upper()
    ch = request.form.get("channel")
    ch = ch if ch in ("WHATSAPP", "EMAIL") else "owner"
    r = redirect(f"/b/{eid}")
    r.set_cookie(CHANNEL_COOKIE, ch, max_age=30 * 86400, samesite="Lax")
    compose_bridge.DEMO_OVERRIDES = True
    compose_bridge.set_demo_override(eid, channel=None if ch == "owner" else ch)
    return r


def _card_rows(card: Card) -> list[dict]:
    from app.app import BASIS_TEXT
    rows = [{"date": i["date"], "day_key": i["day_key"], "shown": i["shown"], "value": i["value"],
             "why": BASIS_TEXT.get(i["basis"], i["basis"]), "question": False} for i in card.items]
    if card.question:
        q = card.question
        rows.append({"date": q["date"], "day_key": q.get("day_key"), "shown": q.get("shown"), "value": None,
                     "why": "One question for the owner", "question": True})
    return sorted(rows, key=lambda r: r["date"])


def run_check(eid: str, engine: str, target: str, base_url: str, *, offers: bool = True) -> dict:
    """The one button. Returns what happened, for the result panel. ``offers=False`` leaves out the
    weekly-hours and review-reply offers (the owner's season view sends those on a later day, so the
    season check stays one message)."""
    from cs import autopilot, learn
    name = _name(eid)
    out: dict = {"entity_id": eid, "name": name, "kind": "", "headline": "", "text": "", "rows": [],
                 "link": None, "extra": []}
    with_drift = _with_drift if offers else (lambda o, _eid: o)
    offer_review_replies = _offer_review_replies if offers else (lambda _o, _eid: None)

    # 1. A standing permission: Autopilot applies what it covers (and asks about the rest).
    has_permission = bool(autopilot.crm_permission(eid)) or bool(learn.active_permissions(eid).get(eid))
    if has_permission:
        res = autopilot.run_autopilot(eid, SEASON, target=target, engine=engine, base_url=base_url)
        if res["applied"]:
            out.update(kind="autopilot", headline=f"Set {_n(len(res['applied']), 'date')} automatically",
                       text=f"{name} gave Connect a standing permission (“every year”), so nothing had to be "
                            "asked. The owner got a short note with Undo.",
                       rows=[{"date": d, "day_key": None, "shown": None, "value": h, "why": "Standing permission",
                              "question": False} for d, h in sorted(res["applied"].items())],
                       asked=len(res["ask"] or []))
            if res["ask"]:
                out["extra"].append(f"Not sure about {_n(len(res['ask']), 'other date')}: asked the owner about those.")
            offer_review_replies(out, eid)
            return with_drift(out, eid)

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
        return with_drift(out, eid)
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
               text=f"By {channel}, the owner's own channel. Nothing changes until the owner answers{by}. "
                    "Answer as the owner on the phone, or open the card.",
               rows=_card_rows(card), link=f"/c/{token}")
    return with_drift(out, eid)


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


def _join_steps():
    from app.journey import join_steps
    return join_steps(len(overview()))


def forget_graph(eid: str) -> None:
    """Drop the business's memory graph (cs.kg). Ask Connect rebuilds it on the next question, so the
    owner's newest answers and decisions are in it (the graph is otherwise built once and kept)."""
    db = dbmod.get_db()
    db.kg_nodes.delete_many({"entity_id": eid})
    db.kg_edges.delete_many({"entity_id": eid})


def after_decision(eid: str) -> None:
    """Called after the owner answered: the memory graph is refreshed; once the hours are applied, the
    review reply is offered."""
    from cs import decide_flow
    forget_graph(eid)
    card = _latest_card(eid)
    if not card or card.status not in decide_flow.APPLIED_STATES:
        return
    # In the owner's season (app/journey.py), a card approved in an earlier run doesn't count: otherwise the
    # review-reply offer arrives right after the onboarding taps, long before this season's card exists.
    j = dbmod.get_db()["journeys"].find_one({"entity_id": eid}, {"created_at": 1})
    if j and j.get("created_at") and card.created_at and _utc(card.created_at) < _utc(j["created_at"]):
        return
    _offer_review_replies(None, eid)


def _utc(t):
    from datetime import timezone
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@bp.post("/demo/check")
def demo_check():
    eid, engine, target = _params()
    _apply_lang(eid)
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
    _apply_lang(eid)
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
    _apply_lang(eid)
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


def reset_business(eid: str, target: str, *, clear_thread: bool = False) -> None:
    """Demo reset for one business: clear holidayHours (and a reopenDate), end its emergencies so the daily
    rate limit starts over, stop their schedules, forget the answers to offers, and expire a waiting card.
    ``clear_thread`` also empties the on-screen phone. Learned facts are kept (that is how learning shows).
    Demo only; the audit log keeps it all."""
    from cs import profile_flow
    with season.yext_client(target) as yc:
        yc.clear_holiday_hours(eid, decision_id="demo-reset")
        if (yc.get_entity(eid).get("hours") or {}).get("reopenDate"):
            yc.set_reopen_date(eid, None, decision_id="demo-reset")
    db = dbmod.get_db()
    ids = [e["id"] for e in db.emergencies.find({"entity_id": eid, "status": {"$ne": "DEMO_RESET"}}, {"id": 1})]
    db.emergencies.update_many({"id": {"$in": ids}}, {"$set": {"status": "DEMO_RESET"}})
    db.schedules.update_many({"emergency_id": {"$in": ids}}, {"$set": {"active": False}})
    offers = profile_flow.reset_offers(eid, db)
    closed = _close_waiting(eid)          # a reset business must not stay stuck on "waiting for the owner"
    if clear_thread and target == "mock":  # a fresh start also undoes a weekly-hours change from an earlier run
        week = dict(zip(DAYS, week_from_yext(data.entity(eid).get("hours") or {})))
        with season.yext_client(target) as yc:
            if yc.get_week(eid) != week:
                yc.set_regular_hours(eid, week, decision_id="demo-reset")
    if clear_thread:
        from cs.channels import outbox
        outbox.clear_thread(eid, db=db)
        db.reply_context.delete_many({"entity_id": eid})
    audit.log("DEMO_RESET", {"target": target, "emergencies_reset": len(ids), "offers_reset": offers,
                             "cards_closed": closed, "thread_cleared": clear_thread}, entity_id=eid)


@bp.post("/demo/clear")
def demo_clear():
    eid, _, target = _params()
    reset_business(eid, target)
    return _changed(demo_entity())


@bp.post("/b/<eid>/reset")
def business_reset(eid: str):
    """"Start over" on the business page: a full demo reset, including the phone."""
    eid = eid.strip().upper()
    reset_business(eid, (request.form.get("target") or season.default_target(eid)).lower(), clear_thread=True)
    return redirect(f"/b/{eid}")


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
