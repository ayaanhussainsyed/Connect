"""Owner routes (no login): the season card O2, the decision -> confirmation O3, undo.

    GET  /c/<token>                 the card (or its confirmation, once decided). Never changes state.
    GET  /c/<token>/preview?answer= HTMX: what the answer means for that date (read-only)
    POST /c/<token>/decide          consumes the link's "decide" use, handles the decision, shows O3
    POST /c/<token>/undo            consumes the link's "undo" use, restores the previous hours
    POST /c/<token>/closed-today    B8 stub (changes nothing yet)
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from cs import data, decide_flow, links, season
from cs.hours import normalise

bp = Blueprint("owner", __name__)


def _load(token: str):
    p = links.verify(token)
    card = season.load_card(p.card_id)
    if card is None or p.owner_id != card.owner_id:
        raise links.TokenError("wrong_card", "This link is not valid.")
    return p, card


def _error(e: links.TokenError):
    code = {"expired": 410, "used": 409}.get(e.kind, 400)
    return render_template("card_error.html", message=str(e), kind=e.kind), code


def _ctx(card, token: str, **extra) -> dict:
    ent = data.entity(card.entity_id)
    return {"card": card, "token": token, "name": ent.get("name", card.entity_id),
            "open": card.status in decide_flow.OPEN_STATES, "result": card.result, **extra}


@bp.get("/c/<token>")
def card_page(token: str):
    try:
        _, card = _load(token)
    except links.TokenError as e:
        return _error(e)
    if card.status in decide_flow.OPEN_STATES:
        return render_template("card.html", **_ctx(card, token, hints={}, errors={}))
    return render_template("card_done.html", **_ctx(card, token))


@bp.get("/c/<token>/preview")
def card_preview(token: str):
    try:
        _, card = _load(token)
    except links.TokenError as e:
        return _error(e)
    answer = request.args.get("answer")
    qd = (card.question or {}).get("date")
    return render_template("_card_hints.html", card=card, p=season.preview_answer(card, qd, answer))


def _decision_from_form(card, form) -> dict:
    action = form.get("action", "approve")
    per_date: dict[str, str] = {}
    for item in card.items:
        d = item["date"]
        if action == "decline" or form.get(f"skip_{d}"):
            per_date[d] = "DECLINE"
            continue
        raw = (form.get(f"hours_{d}") or "").strip()
        try:
            same = raw == "" or normalise(raw) == item["value"]
        except ValueError:
            same = False
        per_date[d] = "APPROVE" if same else f"EDIT:{raw}"
    answer = form.get("answer")
    qd = (card.question or {}).get("date")
    return {"per_date": per_date, "ticks": [i["date"] for i in card.items if form.get(f"tick_{i['date']}")],
            "every_year": action == "approve_every_year",
            "answer": {qd: answer} if (answer and qd) else None,
            "answer_hours": (form.get("answer_hours") or "").strip() or None}


@bp.post("/c/<token>/decide")
def card_decide(token: str):
    try:
        _, card = _load(token)
        links.consume(token, "decide")
    except links.TokenError as e:
        return _error(e)
    decision = _decision_from_form(card, request.form)
    try:
        res = decide_flow.handle_decision(card.id, decision, via="web")
    except decide_flow.DecisionError as e:
        links.release(token, "decide")
        card = season.load_card(card.id)
        if card.status in decide_flow.OPEN_STATES:
            return render_template("card.html", **_ctx(card, token, hints={}, errors={"_": str(e)})), 422
        return render_template("card_done.html", **_ctx(card, token, notice=str(e))), 409
    card = season.load_card(card.id)
    if res.get("status") in decide_flow.OPEN_STATES:   # nothing recorded yet: show what is missing
        links.release(token, "decide")
        errors = {**{d: "Tick the box to confirm this change between open and closed." for d in res["needs_tick"]},
                  **res["invalid"]}
        return render_template("card.html", **_ctx(card, token, hints={}, errors=errors)), 422
    return render_template("card_done.html", **_ctx(card, token))


@bp.post("/c/<token>/undo")
def card_undo(token: str):
    try:
        _, card = _load(token)
        links.consume(token, "undo")
    except links.TokenError as e:
        return _error(e)
    if not card.batch_ids:
        links.release(token, "undo")
        return render_template("card_done.html", **_ctx(card, token, notice="There is nothing to undo.")), 409
    try:
        decide_flow.undo_decision(card.batch_ids[-1], via="web")
    except decide_flow.DecisionError as e:
        links.release(token, "undo")
        return render_template("card_done.html", **_ctx(season.load_card(card.id), token, notice=str(e))), 409
    return render_template("card_done.html", **_ctx(season.load_card(card.id), token))


@bp.post("/c/<token>/closed-today")
def card_closed_today(token: str):
    """B8 from the card page: the signed link proves it is the owner, so it applies at once."""
    from cs import emergency
    try:
        _, card = _load(token)
        links.consume(token, "emergency")
    except links.TokenError as e:
        return render_template("_emergency_result.html", error=str(e), em=None)
    try:
        em = emergency.handle_emergency(card.entity_id, hours="closed", via="web", verified=True, target=card.target,
                                        base_url=request.host_url)
    except emergency.EmergencyError as e:
        links.release(token, "emergency")
        return render_template("_emergency_result.html", error=str(e), em=None)
    return render_template("_emergency_result.html", error=None, em=em,
                           undo_token=links.sign(em["id"], card.owner_id))


def _load_em(token: str):
    from cs import emergency
    p = links.verify(token)
    em = emergency.get(p.card_id)
    if em is None or p.owner_id != f"owner:{em['entity_id']}":
        raise links.TokenError("wrong_card", "This link is not valid.")
    return em


@bp.get("/em/<token>")
def em_page(token: str):
    """Confirm page for an emergency that came from an unregistered sender (GET changes nothing)."""
    try:
        em = _load_em(token)
    except links.TokenError as e:
        return _error(e)
    return render_template("emergency_confirm.html", em=em, token=token, name=data.entity(em["entity_id"]).get("name"))


@bp.post("/em/<token>/confirm")
def em_confirm(token: str):
    from cs import emergency
    try:
        em = _load_em(token)
        links.consume(token, "emergency")
        res = emergency.confirm_emergency(em["id"])
    except links.TokenError as e:
        return _error(e)
    except emergency.EmergencyError as e:
        return render_template("emergency_confirm.html", em=em, token=token, error=str(e),
                               name=data.entity(em["entity_id"]).get("name")), 409
    return render_template("emergency_confirm.html", em=res, token=token, done=True,
                           name=data.entity(em["entity_id"]).get("name"))


@bp.post("/em/<token>/undo")
def em_undo(token: str):
    from cs import emergency
    try:
        em = _load_em(token)
        links.consume(token, "undo")
        emergency.undo_emergency(em["id"])
    except links.TokenError as e:
        return render_template("_emergency_result.html", error=str(e), em=None)
    except emergency.EmergencyError as e:
        return render_template("_emergency_result.html", error=str(e), em=None)
    return render_template("_emergency_result.html", error=None, em=None, undone=True)
