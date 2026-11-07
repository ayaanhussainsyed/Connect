"""A13 – Weekly hours that drifted: "Your weekly hours look like they've changed".

Data: ``artifacts/predictions/drift_2026.parquet`` from the ML agent (one row per business:
``entity_id, p_outdated, proposed_week, evidence, action, basis, published_week, split``). Only
``action == "SUGGEST"`` rows become suggestions (46; SE-D0016 Restaurang Fyren is the demo).

    s = drift_suggestion("SE-D0016")                       # Suggestion(kind="REGULAR_HOURS") or None
    m = compose_drift_message(business, s, "sv", "WHATSAPP")  # {"text", "buttons", "parts"}
    r = apply_drift("SE-D0016", s, target="mock")           # via YextClient.set_regular_hours

``Suggestion.value`` / ``.shown`` hold the proposed / published week as JSON
(``{"monday": "11:00-22:00", …}``); use ``week_of(s.value)``. Facts in the message come only
from the parquet (both weeks) and the quoted review text.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cs import compose as C
from cs.domain import Evidence, Suggestion
from cs.hours import DAYS, is_valid, normalise

DRIFT_FILE = "drift_2026.parquet"
QUOTE_MAX = 140

T = {
    "fi": {"intro": "Viikoittaiset aukioloaikanne näyttävät muuttuneen:",
           "quote": "Asiakas kirjoitti: \"{q}\"", "quote_n": "{n} asiakasta kirjoitti: \"{q}\"",
           "edits": "Myös Google-käyttäjät ovat ehdottaneet muutoksia aikoihinne.",
           "edits_only": "Google-käyttäjät ovat ehdottaneet muutoksia aikoihinne.",
           "nothing": "Jos et tee mitään, Google näyttää edelleen vanhat ajat.",
           "ask": "Päivitetäänkö uudet ajat Googleen ja muihin palveluihin?"},
    "sv": {"intro": "Era ordinarie öppettider verkar ha ändrats:",
           "quote": "En kund skrev: \"{q}\"", "quote_n": "{n} kunder skrev: \"{q}\"",
           "edits": "Google-användare har också föreslagit ändrade tider.",
           "edits_only": "Google-användare har föreslagit ändrade tider.",
           "nothing": "Om du inte gör något visar Google fortfarande de gamla tiderna.",
           "ask": "Ska vi lägga in de nya tiderna på Google och i andra tjänster?"},
    "nb": {"intro": "De faste åpningstidene deres ser ut til å ha endret seg:",
           "quote": "En kunde skrev: \"{q}\"", "quote_n": "{n} kunder skrev: \"{q}\"",
           "edits": "Google-brukere har også foreslått endrede tider.",
           "edits_only": "Google-brukere har foreslått endrede tider.",
           "nothing": "Hvis du ikke gjør noe, viser Google fortsatt de gamle tidene.",
           "ask": "Skal vi legge inn de nye tidene på Google og i andre tjenester?"},
    "en": {"intro": "Your weekly hours look like they've changed:",
           "quote": "A customer wrote: \"{q}\"", "quote_n": "{n} customers wrote: \"{q}\"",
           "edits": "Google users have also suggested different hours.",
           "edits_only": "Google users have suggested different hours.",
           "nothing": "If you do nothing, Google keeps showing the old hours.",
           "ask": "Shall we update Google and the other apps with the new hours?"},
}
BUTTON_IDS = ("APPROVE", "EDIT", "DECLINE")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _table():
    import pandas as pd

    from cs.config import settings
    path = settings.root / "artifacts" / "predictions" / DRIFT_FILE
    if not path.exists():
        return None
    return pd.read_parquet(path)


def drift_row(entity_id: str) -> dict | None:
    """The parquet row for one business as a plain dict (None if missing)."""
    df = _table()
    if df is None:
        return None
    rows = df[df["entity_id"] == entity_id]
    if rows.empty:
        return None
    r = rows.iloc[0].to_dict()
    r["evidence"] = [dict(e) for e in (r.get("evidence") if r.get("evidence") is not None else [])]
    r["proposed_week"] = _canon_week(r.get("proposed_week"))
    r["published_week"] = _canon_week(r.get("published_week"))
    return r


def suggested_entities() -> list[str]:
    """Every business with a drift SUGGEST row."""
    df = _table()
    return [] if df is None else df.loc[df["action"] == "SUGGEST", "entity_id"].tolist()


def _canon_week(week: Any) -> dict[str, str] | None:
    if week is None:
        return None
    if isinstance(week, str):
        week = json.loads(week)
    out = {}
    for d in DAYS:
        v = week.get(d)
        if v is None:
            return None
        v = "closed" if str(v).lower() == "closed" else normalise(str(v))
        if not is_valid(v):
            return None
        out[d] = v
    return out


def week_of(value: str | dict | None) -> dict[str, str] | None:
    """``Suggestion.value`` / ``.shown`` (JSON) -> {weekday: hours}."""
    return _canon_week(value)


def _evidence(row: dict) -> list[Evidence]:
    out = []
    for e in row.get("evidence") or []:
        src = "USER_EDIT" if str(e.get("source", "")).upper().startswith("USER_EDIT") else str(e.get("source", "")).upper()
        out.append(Evidence(source=src, detail=str(e.get("detail") or ""), ref=e.get("ref"),
                            weight=float(e.get("weight") or 0.0)))
    return out


def drift_suggestion(entity_id: str) -> Suggestion | None:
    """A ``REGULAR_HOURS`` Suggestion for a business whose weekly hours drifted, or None.

    value = proposed week (JSON), shown = published week (JSON), evidence = the quoted review
    texts and Google user edits, probability = p_outdated."""
    row = drift_row(entity_id)
    if not row or row.get("action") != "SUGGEST":
        return None
    proposed, published = row["proposed_week"], row["published_week"]
    if proposed is None or published is None or proposed == published:
        return None
    basis = str(row.get("basis") or "").upper()
    basis = "USER_EDIT" if basis.startswith("USER_EDIT") else (basis or "REVIEW")
    return Suggestion(entity_id=entity_id, kind="REGULAR_HOURS", date=None, day_key=None,
                      shown=json.dumps(published), action="SUGGEST", value=json.dumps(proposed),
                      probability=float(row.get("p_outdated") or 0.0), basis=basis,
                      evidence=_evidence(row), model_version="drift_2026")


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------
def changed_days(published: dict[str, str], proposed: dict[str, str]) -> list[tuple[list[str], str, str]]:
    """Runs of consecutive weekdays with the same before -> after: [(days, before, after)]."""
    runs: list[tuple[list[str], str, str]] = []
    prev_changed = False
    for d in DAYS:
        a, b = published[d], proposed[d]
        if a == b:
            prev_changed = False
            continue
        if prev_changed and runs[-1][1] == a and runs[-1][2] == b:
            runs[-1][0].append(d)
        else:
            runs.append(([d], a, b))
        prev_changed = True
    return runs


def _day_span(days: list[str], lang: str) -> str:
    wd = C.WD[C.norm_lang(lang)]
    dot = "." if C.norm_lang(lang) == "nb" else ""
    first, last = wd[DAYS.index(days[0])] + dot, wd[DAYS.index(days[-1])] + dot
    return first if len(days) == 1 else f"{first}–{last}"


def _quote(text: str) -> str:
    t = " ".join((text or "").split())
    if len(t) <= QUOTE_MAX:
        return t
    cut = t[:QUOTE_MAX].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + " …"


def compose_drift_message(business, suggestion: Suggestion, lang: str, channel: str = "WHATSAPP", *,
                          first_contact: bool = False) -> dict:
    """"Your weekly hours look like they've changed:" + before → after per changed day, the
    reason (a short quote of the review, or the Google user edits), and Approve / Edit / No thanks.
    Returns ``{"text", "buttons", "parts"}`` like ``cs.compose`` (SMS gets digits)."""
    lang = C.norm_lang(lang)
    biz = C._business(business)
    published, proposed = week_of(suggestion.shown), week_of(suggestion.value)
    if not published or not proposed:
        raise ValueError("the suggestion has no valid weeks")
    runs = changed_days(published, proposed)
    lines = [f"• {_day_span(days, lang)} {C.fmt_hours(a, lang)} → {C.fmt_hours(b, lang)}" for days, a, b in runs]
    reviews = [e for e in suggestion.evidence if e.source == "REVIEW" and e.detail]
    edits = [e for e in suggestion.evidence if e.source == "USER_EDIT"]
    reason = None
    if reviews:
        best = max(reviews, key=lambda e: e.weight)
        same = sum(1 for e in reviews if e.detail.strip() == best.detail.strip())
        q = _quote(best.detail)
        reason = T[lang]["quote_n"].format(n=same, q=q) if same > 1 else T[lang]["quote"].format(q=q)
        if edits:
            reason += " " + T[lang]["edits"]
    elif edits:
        reason = T[lang]["edits_only"]
    header = C.T[lang]["disclosure"].format(brand=C._brand(biz)) if first_contact else None
    greeting = C.T[lang]["hi"].format(name=biz.get("name") or "").replace(" !", "!")
    out = [header, f"{greeting} {T[lang]['intro']}", *lines, reason, T[lang]["nothing"], T[lang]["ask"]]
    parts = {"kind": "DRIFT", "changes": [{"days": d, "before": a, "after": b} for d, a, b in runs],
             "reason": reason, "lines": lines, "suggestion_id": suggestion.id,
             "subject": T[lang]["intro"].rstrip(":")}
    return C._finish(out, C.make_buttons(BUTTON_IDS, lang), channel, lang, parts)


# ---------------------------------------------------------------------------
# Apply (owner approved)
# ---------------------------------------------------------------------------
def apply_drift(entity_id: str, suggestion: Suggestion | None = None, *, target: str = "mock",
                decision_id: str | None = None, final_week: dict[str, str] | None = None,
                client=None, audit: bool = True) -> dict:
    """Write the approved week through ``cs.yext.YextClient.set_regular_hours``.

    Owner wins: the live weekly hours are re-read first; if they no longer equal the week we
    showed the owner ("before"), nothing is written. If ``set_regular_hours`` doesn't exist
    yet, nothing is written and the result says so (with the body that would be sent).
    Returns ``{"status": applied|cancelled|unavailable|failed, "detail", "week", "write"?}``."""
    from cs.hours import week_from_yext
    suggestion = suggestion or drift_suggestion(entity_id)
    if suggestion is None and final_week is None:
        return {"status": "failed", "detail": "no drift suggestion for this business", "week": None}
    week = _canon_week(final_week) if final_week else week_of(suggestion.value)
    if week is None:
        return {"status": "failed", "detail": "invalid week", "week": None}
    before = week_of(suggestion.shown) if suggestion else None
    result: dict = {"week": week, "target": target}
    own_client = client is None
    try:
        if client is None:
            from cs.yext import YextClient
            client = YextClient(target=target)
        try:
            live = dict(zip(DAYS, week_from_yext((client.get_entity(entity_id).get("hours") or {}))))
        except Exception as ex:  # noqa: BLE001
            result.update(status="failed", detail=f"could not read the listing: {type(ex).__name__}")
            return _log(result, entity_id, decision_id, audit)
        if before is not None and live != before and live != week:
            result.update(status="cancelled", detail="the weekly hours changed since we asked (owner wins)",
                          live=live)
            return _log(result, entity_id, decision_id, audit)
        setter = getattr(client, "set_regular_hours", None)
        if setter is None:
            from cs.hours import week_strs_to_yext
            result.update(status="unavailable",
                          detail="YextClient.set_regular_hours is not available yet; nothing written",
                          would_send={"hours": week_strs_to_yext([week[d] for d in DAYS])})
            return _log(result, entity_id, decision_id, audit)
        import inspect
        params = inspect.signature(setter).parameters
        kw = {}
        if "decision_id" in params:
            kw["decision_id"] = decision_id
        if "expected_current_week" in params and before is not None:
            kw["expected_current_week"] = before  # the client's own owner-wins check
        rec = setter(entity_id, week, **kw)
        ok = getattr(rec, "readback_ok", None)
        result.update(status="applied" if ok is not False else "failed",
                      detail=getattr(rec, "note", "") or "written", write=rec)
        return result  # the Yext client writes its own WriteRecord + audit event
    finally:
        if own_client and client is not None and hasattr(client, "close"):
            client.close()


def _log(result: dict, entity_id: str, decision_id: str | None, audit: bool) -> dict:
    if audit:
        try:
            from cs import audit as audit_mod
            audit_mod.log("DRIFT_APPLY_" + result["status"].upper(),
                          {"decision_id": decision_id, "detail": result.get("detail"), "week": result.get("week")},
                          entity_id=entity_id)
        except Exception:  # noqa: BLE001 - the result is still returned
            pass
    return result

