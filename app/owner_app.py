"""The landing page and the owner app (one screen per business, a stand-in for the Connect customer's view).

    GET  /                            landing: what Connect is, how it works, what it can do
    GET  /app?b=FI-D0001              the owner app
    GET  /app/needs?b=                what needs the owner's OK (builds the season card if none is open)
    POST /app/check                   look again: build a fresh card
    POST /app/decide                  approve / edit / decline (same handler as the no-login card)
    POST /app/undo                    undo the last applied answer
    GET  /app/preview?card_id=&answer=   what the one-question answer means (read-only)
    GET  /app/listing?b=              the live listing (Yext) and where it shows
    GET  /app/upcoming?b=             every upcoming special day and its status
    POST /app/closed-today            the owner's emergency, applied at once (signed-in owner)
    POST /app/closed-today/undo
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request

from cs import data, decide_flow, display, emergency, season
from cs import db as dbmod
from cs.domain import Card
from cs.hours import DAYS, week_from_yext
from cs.suggest import engine_adapter

bp = Blueprint("owner_app", __name__)

DEFAULT_BUSINESS = "FI-D0001"
SEASON = 2026
TZ = {"FI": "Europe/Helsinki", "SE": "Europe/Stockholm", "NO": "Europe/Oslo"}
SHOWCASE = [
    ("FI-D0001", "Christmas hours are wrong on Google. The full loop."),
    ("FI-D0017", "Brand new, no history. Connect asks just one question."),
    ("FI-D0012", "Said “every year” last season. Nothing to do this time."),
    ("FI-D0013", "Every app already shows the right hours. Connect stays quiet."),
    ("FI-D0011", "Looks closed for good. A Fonecta person checks first."),
    ("SE-D0016", "Swedish. Reviews say the weekly hours changed."),
]


def _open_now(eid: str, week: list[str], holidays: dict[str, str]) -> dict:
    """Is the listing showing the business as open at this moment (its own time zone)?"""
    now = datetime.now(ZoneInfo(TZ.get(eid[:2], "Europe/Helsinki")))
    today = now.date().isoformat()
    shown = holidays.get(today)
    if shown in (None, "REGULAR"):
        shown = week[now.weekday()]
    if not shown or shown == "closed":
        return {"open": False, "today": shown or "closed", "special": today in holidays}
    hm = now.hour * 60 + now.minute
    try:
        spans = [tuple(int(p[:2]) * 60 + int(p[3:5]) for p in part.strip().split("-")) for part in shown.split(",")]
    except (ValueError, IndexError):
        return {"open": None, "today": shown, "special": today in holidays}
    return {"open": any(a <= hm < b for a, b in spans), "today": shown, "special": today in holidays}


@lru_cache(maxsize=1)
def businesses() -> list[dict]:
    df = data.entity_index()
    rows = df[["entity_id", "name", "city", "split"]].to_dict("records")
    return sorted(rows, key=lambda r: (r["split"] != "demo", r["name"]))


def _eid() -> str:
    raw = (request.values.get("b") or DEFAULT_BUSINESS).strip().split()[0].upper()
    data.entity(raw)
    return raw


def _engine() -> str:
    return "ml" if engine_adapter.available(SEASON) else "baseline"


def _latest(eid: str) -> Card | None:
    found = dbmod.find(Card, {"entity_id": eid}, sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def _needs(eid: str, card: Card | None = None, *, errors: dict | None = None, notice: str | None = None,
           build: bool = False, fresh: bool = False):
    """HTMX fragment, always 200 so htmx swaps it. Only ``build``/``fresh`` (POST) may create a card."""
    if card is None and not fresh:
        card = _latest(eid)
    if fresh or (card is None and build):
        try:
            card = season.build_card(eid, SEASON, _engine())
        except season.NoCard as e:
            return render_template("app/_needs.html", state="quiet", reason=str(e), eid=eid, notice=notice)
        except Exception as e:  # noqa: BLE001 - e.g. the Yext mock is not running
            return render_template("app/_needs.html", state="error", reason=str(e), eid=eid, notice=notice)
    if card is None:
        state = "unchecked"
    elif card.status in decide_flow.OPEN_STATES:
        state = "open"
    elif card.status in decide_flow.APPLIED_STATES:
        state = "applied"
    elif card.status == "UNDONE":
        state = "undone"
    else:
        state = "closed"
    return render_template("app/_needs.html", state=state, card=card, eid=eid, errors=errors or {},
                           notice=notice, result=(card.result if card else None) or {})


@bp.get("/")
def landing():
    shops = []
    for eid, story in SHOWCASE:
        ent = data.entity(eid)
        shops.append({"eid": eid, "name": ent.get("name", eid), "city": ent.get("city", ""), "story": story})
    return render_template("app/landing.html", shops=shops)


@bp.get("/app")
def home():
    eid = _eid()
    ent = data.entity(eid)
    return render_template("app/home.html", eid=eid, name=ent.get("name", eid), city=ent.get("city", ""),
                           businesses=businesses())


@bp.get("/app/needs")
def needs():
    return _needs(_eid())


@bp.post("/app/needs")
def needs_or_check():
    return _needs(_eid(), build=True)


@bp.post("/app/check")
def check():
    return _needs(_eid(), fresh=True)


@bp.post("/app/decide")
def decide():
    from app.owner import _decision_from_form
    card = season.load_card(request.form.get("card_id", ""))
    if card is None:
        return _needs(_eid(), notice="That suggestion is gone. Here is the latest.")
    try:
        res = decide_flow.handle_decision(card.id, _decision_from_form(card, request.form), via="web")
    except decide_flow.DecisionError as e:
        return _needs(card.entity_id, season.load_card(card.id), errors={"_": str(e)})
    card = season.load_card(card.id)
    if res.get("status") in decide_flow.OPEN_STATES:
        errors = {**{d: "Tick the box to confirm this change between open and closed." for d in res["needs_tick"]},
                  **res["invalid"]}
        return _needs(card.entity_id, card, errors=errors)
    resp = _needs(card.entity_id, card)
    return _changed(resp)


@bp.post("/app/undo")
def undo():
    card = season.load_card(request.form.get("card_id", ""))
    if card is None or not card.batch_ids:
        return _needs(_eid(), notice="There is nothing to undo.")
    try:
        decide_flow.undo_decision(card.batch_ids[-1], via="web")
    except decide_flow.DecisionError as e:
        return _needs(card.entity_id, season.load_card(card.id), notice=str(e))
    return _changed(_needs(card.entity_id, season.load_card(card.id)))


@bp.get("/app/preview")
def preview():
    card = season.load_card(request.args.get("card_id", ""))
    if card is None:
        return ""
    qd = (card.question or {}).get("date")
    return render_template("app/_hints.html", card=card, p=season.preview_answer(card, qd, request.args.get("answer")))


def _changed(body):
    from flask import make_response
    r = make_response(body)
    r.headers["HX-Trigger"] = "listing-changed"
    return r


@bp.get("/app/listing")
def listing():
    from cs import confirm
    from cs.yext import holiday_map
    eid = _eid()
    target = season.default_target(eid)
    where = confirm.where_it_shows(eid, target=target)
    try:
        with season.yext_client(target) as yc:
            ent = yc.get_entity(eid)
    except Exception as e:  # noqa: BLE001
        return render_template("app/_listing.html", error=str(e), where=where, target=target)
    today = date.today().isoformat()
    week_hours = week_from_yext(ent.get("hours") or {})
    week = list(zip([d.capitalize()[:3] for d in DAYS], week_hours))
    all_holidays = holiday_map(ent)
    holidays = {d: h for d, h in sorted(all_holidays.items()) if d >= today}
    return render_template("app/_listing.html", error=None, week=week, holidays=holidays, where=where,
                           target=target, reopen=(ent.get("hours") or {}).get("reopenDate"),
                           now=_open_now(eid, week_hours, all_holidays))


@bp.get("/app/upcoming")
def upcoming():
    eid = _eid()
    as_of = data.SNAPSHOT[SEASON]
    today = date.today().isoformat()
    t = data.targets(SEASON)
    t = t[(t["entity_id"] == eid) & (t["date"] >= today)].sort_values("date")
    card = _latest(eid)
    pending = {i["date"]: i["value"] for i in card.items} if card and card.status in decide_flow.OPEN_STATES else {}
    applied = (card.result or {}).get("applied", {}) if card and card.status in decide_flow.APPLIED_STATES else {}
    rows = []
    for r in t.to_dict("records"):
        ex = display.explain_shown(eid, r["date"], as_of)
        d = r["date"]
        state = "updated" if d in applied else ("pending" if d in pending else "none")
        rows.append({"date": d, "day_key": r["day_key"], "shown": ex["shown"], "state": state,
                     "value": applied.get(d) or pending.get(d)})
    return render_template("app/_upcoming.html", rows=rows)


@bp.post("/app/closed-today")
def closed_today():
    eid = _eid()
    try:
        em = emergency.handle_emergency(eid, hours="closed", via="web", verified=True,
                                        target=season.default_target(eid), base_url=request.host_url)
    except emergency.EmergencyError as e:
        return render_template("app/_closed_today.html", error=str(e), em=None, eid=eid)
    return _changed(render_template("app/_closed_today.html", error=None, em=em, eid=eid))


@bp.post("/app/closed-today/undo")
def closed_today_undo():
    eid = _eid()
    try:
        emergency.undo_emergency(request.form.get("em_id", ""))
    except emergency.EmergencyError as e:
        return render_template("app/_closed_today.html", error=str(e), em=None, eid=eid)
    return _changed(render_template("app/_closed_today.html", error=None, em=None, undone=True, eid=eid))
