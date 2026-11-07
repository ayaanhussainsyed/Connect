"""Flask app. Run:  .venv/bin/python -m flask --app app.app run --port 5050
(or  .venv/bin/python -m app.app).

A1, "what Google shows now":
    GET /api/shown?entity_id=FI-D0001&date=2026-12-24[&as_of=2026-11-06]   JSON
    GET /a1                                                                 page (HTMX)
    GET /a1/result?entity_id=…&date=…[&as_of=…]                             HTMX partial
Owner card (A8/B1), demo and ops: see app/owner.py and app/demo.py.
A3/A4, suggestions after the guardrails:
    GET /api/suggestions?entity_id=FI-D0001[&engine=baseline|ml][&season=2026]   JSON
    GET /suggest?entity_id=…[&engine=…][&season=…]                               page (HTMX)
    GET /suggest/result?…                                                         HTMX partial
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, url_for

from cs import data, display, export, guardrails
from cs.suggest import ENGINES, engine_adapter, get_suggestions

app = Flask(__name__)

DEFAULT_DATE = "2026-12-24"
AS_OF_CHOICES = [
    (data.SNAPSHOT[2026].isoformat(), "6 Nov 2026 (the scenario's today)"),
    (data.SNAPSHOT[2025].isoformat(), "6 Nov 2025 (back-test)"),
]
# Dev rows that show each display rule (see cs/display.py).
RULE_EXAMPLES = [
    ("FI-00330", "2026-12-21", "2026-11-06", "Seasonal reopening restores earlier hours"),
    ("FI-00848", "2025-12-25", "2025-11-06", "Version effective on the snapshot day"),
    ("FI-01794", "2026-12-24", "2026-11-06", "Closed for good"),
    ("FI-00026", "2026-12-24", "2026-11-06", "Temporarily closed (reopenDate)"),
]


SOURCE_NAMES = {
    display.REGULAR_HOURS: "Regular hours",
    display.HOLIDAY_ENTRY: "Holiday exception",
    display.CLOSED_FOR_GOOD: "Closed for good",
    display.TEMPORARILY_CLOSED: "Temporarily closed",
}


class BadRequest(ValueError):
    pass


@app.template_filter("nice_date")
def nice_date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{d:%a} {d.day} {d:%b %Y}"


BASIS_TEXT = {
    "OWN_LAST_YEAR": "You entered this last year",
    "OWN_2_SEASONS": "You entered this two seasons ago",
    "LINKED_HOLIDAY": "Like a related holiday you entered",
    "MALL": "Your shopping centre's hours",
    "PEERS": "Similar businesses nearby",
    "REVIEW": "What customers wrote",
    "USER_EDIT": "A Google user's edit",
    "MODEL": "Our model's estimate",
    "OWNER_ANSWER": "What you told us",
}


@app.context_processor
def _globals() -> dict:
    return {"source_names": SOURCE_NAMES, "basis_text": BASIS_TEXT}


@lru_cache(maxsize=1)
def entity_options() -> list[dict]:
    """All businesses and centres for the picker, demo storylines first."""
    df = data.entity_index()
    rows = df[["entity_id", "name", "split", "city", "segment"]].to_dict("records")
    return sorted(rows, key=lambda r: (r["split"] != "demo", r["entity_id"]))


def _params(args) -> tuple[str, str, str]:
    raw = (args.get("entity_id") or "").strip()
    entity_id = raw.split()[0].upper() if raw else ""
    d = (args.get("date") or "").strip()
    as_of = (args.get("as_of") or data.DEFAULT_AS_OF.isoformat()).strip()
    if not entity_id:
        raise BadRequest("Choose a business (entity_id).")
    for name, v in (("date", d), ("as_of", as_of)):
        try:
            date.fromisoformat(v)
        except ValueError:
            raise BadRequest(f"Enter {name} as YYYY-MM-DD.") from None
    return entity_id, d, as_of


def _label_view(lab: dict | None, shown: str) -> dict | None:
    if not lab:
        return None
    return {
        "displayed": lab["displayed_hours_at_snapshot"],
        "truth": lab["truth_hours"],
        "expected_action": lab["expected_action"],
        "mismatch_type": lab["mismatch_type"],
        "match": lab["displayed_hours_at_snapshot"] == shown,
    }


def _upcoming(entity_id: str, as_of: str, selected: str) -> list[dict]:
    season = data.season_of(as_of)
    t = data.targets(season)
    t = t[(t["entity_id"] == entity_id) & (t["date"] >= as_of)].sort_values("date")
    rows = []
    for r in t.to_dict("records"):
        ex = display.explain_shown(entity_id, r["date"], as_of)
        rows.append({
            "date": r["date"],
            "weekday": ex["weekday"][:3].capitalize(),
            "day_key": r["day_key"],
            "day_type": r["day_type"],
            "event_id": r.get("event_id", ""),
            "shown": ex["shown"],
            "source": ex["source"],
            "label": _label_view(display.label_for(entity_id, r["date"], as_of), ex["shown"]),
            "selected": r["date"] == selected,
        })
    return rows


def _result(entity_id: str, d: str, as_of: str) -> dict:
    ex = display.explain_shown(entity_id, d, as_of)
    info = data.entity_index().set_index("entity_id").loc[entity_id].to_dict()
    return {
        "ex": ex,
        "info": info,
        "label": _label_view(display.label_for(entity_id, d, as_of), ex["shown"]),
        "upcoming": _upcoming(entity_id, as_of, d),
    }


@app.get("/")
def index():
    return redirect(url_for("a1"))


@app.get("/api/shown")
def api_shown():
    try:
        entity_id, d, as_of = _params(request.args)
        ex = display.explain_shown(entity_id, d, as_of)
    except BadRequest as e:
        return jsonify(error=str(e)), 400
    except KeyError:
        return jsonify(error="unknown entity_id"), 404
    lab = display.label_for(entity_id, d, as_of)
    return jsonify(
        entity_id=entity_id,
        date=ex["date"],
        as_of=ex["as_of"],
        shown=ex["shown"],
        source=ex["source"],
        explanation=ex,
        label=None if lab is None else {
            "truth_hours": lab["truth_hours"],
            "displayed_hours_at_snapshot": lab["displayed_hours_at_snapshot"],
            "needs_action": int(lab["needs_action"]),
            "expected_action": lab["expected_action"],
            "mismatch_type": lab["mismatch_type"],
        },
        label_match=None if lab is None else lab["displayed_hours_at_snapshot"] == ex["shown"],
    )


@app.get("/a1")
def a1():
    ctx = {
        "options": entity_options(),
        "as_of_choices": AS_OF_CHOICES,
        "rule_examples": RULE_EXAMPLES,
        "form": {
            "entity_id": request.args.get("entity_id", "FI-D0001"),
            "date": request.args.get("date", DEFAULT_DATE),
            "as_of": request.args.get("as_of", data.DEFAULT_AS_OF.isoformat()),
        },
        "result": None,
        "error": None,
    }
    if request.args.get("entity_id"):
        try:
            ctx["result"] = _result(*_params(request.args))
        except BadRequest as e:
            ctx["error"] = str(e)
        except KeyError:
            ctx["error"] = "We don't know that business. Pick one from the list."
    return render_template("a1.html", **ctx)


@app.get("/a1/result")
def a1_result():
    try:
        entity_id, d, as_of = _params(request.args)
        ctx = {"result": _result(entity_id, d, as_of), "error": None}
        status = 200
    except BadRequest as e:
        ctx, status = {"result": None, "error": str(e)}, 200
        entity_id = d = as_of = None
    except KeyError:
        ctx, status = {"result": None, "error": "We don't know that business. Pick one from the list."}, 200
        entity_id = d = as_of = None
    resp = app.make_response((render_template("_a1_result.html", **ctx), status))
    if entity_id:
        resp.headers["HX-Push-Url"] = url_for("a1") + "?" + urlencode(
            {"entity_id": entity_id, "date": d, "as_of": as_of})
    return resp


# ---------------------------------------------------------------------------
# A3/A4: suggestions
# ---------------------------------------------------------------------------
SEASON_CHOICES = [(2026, "2026 season (snapshot 6 Nov 2026)"), (2025, "2025 back-test (dev only)")]


def _suggest_params(args) -> tuple[str, str, int]:
    raw = (args.get("entity_id") or "").strip()
    if not raw:
        raise BadRequest("Choose a business (entity_id).")
    entity_id = raw.split()[0].upper()
    engine = (args.get("engine") or "baseline").strip().lower()
    if engine not in ENGINES:
        raise BadRequest(f"engine must be one of {', '.join(ENGINES)}.")
    try:
        season = int(args.get("season") or 2026)
    except ValueError:
        raise BadRequest("season must be 2026 or 2025.") from None
    if season not in (2026, 2025):
        raise BadRequest("season must be 2026 or 2025.")
    return entity_id, engine, season


def _suggestion_rows(entity_id: str, engine: str, season: int) -> list[dict]:
    data.entity(entity_id)  # KeyError if unknown
    as_of = data.SNAPSHOT[season]
    ctx = guardrails.make_context(as_of)
    rows = []
    for s in get_suggestions(season, engine, [entity_id]):
        g = guardrails.apply_guardrails(s, ctx)
        doc = g.to_doc()
        doc["created_at"] = g.created_at.isoformat()
        lab = display.label_for(entity_id, g.date, as_of)
        if lab:
            pts, outcome = export.points(g.action, g.value, lab)
            doc["label"] = {"truth_hours": lab["truth_hours"], "expected_action": lab["expected_action"],
                            "mismatch_type": lab["mismatch_type"], "points": pts, "outcome": outcome,
                            "ok": pts > 0 or outcome == "correct_silence"}
        else:
            doc["label"] = None
        rows.append(doc)
    return rows


def _suggest_summary(rows: list[dict]) -> dict:
    from collections import Counter
    labelled = [r["label"] for r in rows if r["label"]]
    return {
        "actions": dict(Counter(r["action"] for r in rows)),
        "messageable": sum(1 for r in rows if r["message_ok"]),
        "downgraded": sum(1 for r in rows if r["original_action"]),
        "dev_rows": len(labelled),
        "dev_points": round(sum(x["points"] for x in labelled), 2),
        "dev_ok": sum(1 for x in labelled if x["ok"]),
    }


@app.get("/api/suggestions")
def api_suggestions():
    try:
        entity_id, engine, season = _suggest_params(request.args)
        rows = _suggestion_rows(entity_id, engine, season)
    except BadRequest as e:
        return jsonify(error=str(e)), 400
    except KeyError:
        return jsonify(error="unknown entity_id"), 404
    return jsonify(entity_id=entity_id, engine=engine, season=season,
                   model_versions=sorted({r["model_version"] for r in rows}),
                   engine_artifact=engine_adapter.artifact_info(season) if engine == "ml" else None,
                   summary=_suggest_summary(rows), suggestions=rows)


def _suggest_ctx(args) -> dict:
    entity_id, engine, season = _suggest_params(args)
    rows = _suggestion_rows(entity_id, engine, season)
    info = data.entity_index().set_index("entity_id").loc[entity_id].to_dict()
    fallback = engine == "ml" and not engine_adapter.available(season)
    return {"entity_id": entity_id, "engine": engine, "season": season, "rows": rows, "info": info,
            "summary": _suggest_summary(rows), "fallback": fallback}


@app.get("/suggest")
def suggest_page():
    ctx = {
        "options": entity_options(),
        "engines": ENGINES,
        "season_choices": SEASON_CHOICES,
        "form": {"entity_id": request.args.get("entity_id", "FI-D0001"),
                 "engine": request.args.get("engine", "baseline"),
                 "season": request.args.get("season", "2026")},
        "res": None,
        "error": None,
    }
    if request.args.get("entity_id"):
        try:
            ctx["res"] = _suggest_ctx(request.args)
        except BadRequest as e:
            ctx["error"] = str(e)
        except KeyError:
            ctx["error"] = "We don't know that business. Pick one from the list."
    return render_template("suggest.html", **ctx)


@app.get("/suggest/result")
def suggest_result():
    try:
        res = _suggest_ctx(request.args)
        html = render_template("_suggest_result.html", res=res, error=None)
    except BadRequest as e:
        res, html = None, render_template("_suggest_result.html", res=None, error=str(e))
    except KeyError:
        res, html = None, render_template("_suggest_result.html", res=None,
                                          error="We don't know that business. Pick one from the list.")
    resp = app.make_response(html)
    if res:
        resp.headers["HX-Push-Url"] = url_for("suggest_page") + "?" + urlencode(
            {"entity_id": res["entity_id"], "engine": res["engine"], "season": res["season"]})
    return resp


from app.demo import bp as demo_bp  # noqa: E402
from app.owner import bp as owner_bp  # noqa: E402
from app.webhooks import bp as webhooks_bp  # noqa: E402

app.register_blueprint(owner_bp)
app.register_blueprint(demo_bp)
app.register_blueprint(webhooks_bp)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
