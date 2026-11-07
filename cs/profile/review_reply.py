"""A13 – Replies to "the door was closed" reviews.

1. **Find** a business's reviews (dataset ``reviews.json``, published before ``as_of``) that
   complain about wrong hours or a closed door and have **no owner reply** (``comments`` empty).
2. **Draft** a short reply from a template in the review's language: an apology, plus the
   confirmed hours for that special day from the business's newest approved answer (Mongo
   ``learned_facts``) or entered exception (``holiday_hours_history``). Only facts from data;
   no customer names. Opus may polish the wording (``polish=True``), kept only if
   ``cs.llm.slot_check`` passes.
3. **Offer** it to the owner: ``compose_review_reply_offer`` ("Reply to the 2 customers who found
   you closed on Good Friday?" + the draft) with Approve / Edit / No thanks.
4. **Approve**: ``approve_review_reply`` stores the reply in Mongo ``review_replies``, marked
   "would post via the Yext Reviews API (POST /reviews/{id}/comments)". It is **not posted**: the
   Yext sandbox has no reviews and the dataset's mock API has no comment endpoint.

    drafts = review_reply_drafts("SE-D0016")   # [{"suggestion", "review", "facts", "lang"}]
    sugs = review_reply_suggestions("SE-D0016") # [Suggestion(kind="REVIEW_REPLY")]
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from cs import compose as C
from cs.domain import Evidence, Suggestion, new_id, utcnow
from cs.profile.days import cap, detect_day_key, on_day

DEFAULT_AS_OF = "2026-11-06"
TZ = {"FI": "Europe/Helsinki", "SE": "Europe/Stockholm", "NO": "Europe/Oslo"}
QUOTE_MAX = 160
COLLECTION = "review_replies"
YEXT_COMMENT_PATH = "/v2/accounts/me/reviews/{review_id}/comments"
NOT_POSTED_NOTE = ("Not posted: the Yext sandbox has no reviews and the dataset mock API has no comment "
                   "endpoint. In production Connect posts it via the Yext Reviews API "
                   "(POST /reviews/{id}/comments).")

# A complaint about wrong hours / a closed door (fi, sv, nb, en) ...
COMPLAINT_RE = re.compile(
    r"kiinni|suljettu|suljetun|ovi oli|ovet olivat|pimeänä|(?:aukiolo\w*|ajat)\s+(?:olivat\s+|ovat\s+)?(?:väärin|väärät)|"
    r"väärät (?:aukiolo)?ajat|vanhat tiedot|eri ajat|turha reissu|"
    r"stängt|stängd|stängde|fel öppettider|gamla tider|onödig resa|"
    r"stengt|stengte|feil åpningstider|gamle tider|bortkastet|"
    r"\bclosed\b|locked|wrong hours|old hours|out of date|wasted trip", re.IGNORECASE)
# ... but not a thank-you for being open.
THANKS_RE = re.compile(
    r"kiitos(,)? että olitte auki|kiva että olitte auki|tack för att ni hade öppet|takk for at dere hadde åpent|"
    r"thanks? (you )?for being open", re.IGNORECASE)
CLOSED_DOOR_RE = re.compile(r"kiinni|suljet|pimeänä|stängt|stängd|stengt|\bclosed\b|locked", re.IGNORECASE)
MAX_RATING = 3.0

_LANG_HINTS = {
    "fi": r"\b(oli|että|mutta|vaikka|olivat|netissä|kiitos|tultiin|aukioloajat|harmi|harmillista|paikka|luki|"
          r"kiinni|auki|mukaan|ovi|ovet|todellisuudessa|suljettu)\b",
    "sv": r"\b(och|nätet|öppet|stängt|stängd|inte|trots|det var|stod|onödig|för|uppdatera)\b",
    "nb": r"\b(og|nett|åpent|stengt|ikke|selv om|det sto|bortkastet|oppdater|gjerne)\b",
    "en": r"\b(the|it|was|but|closed|online|open|even|though|said|please|wasted)\b",
}

T = {
    "fi": {"apology_day": "Kiitos palautteesta, ja pahoittelut: aukioloaikamme olivat verkossa väärin {on}.",
           "apology": "Kiitos palautteesta, ja pahoittelut: aukioloaikamme olivat verkossa vanhentuneet.",
           "past": "Viimeksi {on} ({date}) olimme {state}.", "future": "{On} ({date}) olemme {state}.",
           "closed": "kiinni", "open": "auki {h}", "regular": "auki tavallisin ajoin",
           "promise_day": "Päivitämme pyhien aukioloajat jatkossa ajoissa.",
           "promise": "Pidämme aukioloajat jatkossa ajan tasalla.", "bye": "Tervetuloa uudelleen!",
           "ask_closed_1": "Vastataanko asiakkaalle, joka löysi teidät kiinni{on}?",
           "ask_closed_n": "Vastataanko {n} asiakkaalle, jotka löysivät teidät kiinni{on}?",
           "ask_wrong_1": "Vastataanko asiakkaalle, jonka mukaan aukioloaikanne olivat väärin{on}?",
           "ask_wrong_n": "Vastataanko {n} asiakkaalle, joiden mukaan aukioloaikanne olivat väärin{on}?",
           "wrote": "Asiakas kirjoitti: \"{q}\"", "draft": "Vastausehdotus: \"{d}\"", "more": "+ {n} muuta"},
    "sv": {"apology_day": "Tack för din recension, och förlåt: våra öppettider på nätet var fel {on}.",
           "apology": "Tack för din recension, och förlåt: våra öppettider på nätet var inaktuella.",
           "past": "Senast {on} ({date}) hade vi {state}.", "future": "{On} ({date}) har vi {state}.",
           "closed": "stängt", "open": "öppet {h}", "regular": "öppet som vanligt",
           "promise_day": "Vi uppdaterar våra helgtider i god tid framöver.",
           "promise": "Vi håller våra öppettider uppdaterade framöver.", "bye": "Välkommen tillbaka!",
           "ask_closed_1": "Vill du svara kunden som kom till en stängd dörr{on}?",
           "ask_closed_n": "Vill du svara de {n} kunderna som kom till en stängd dörr{on}?",
           "ask_wrong_1": "Vill du svara kunden som såg fel öppettider{on}?",
           "ask_wrong_n": "Vill du svara de {n} kunderna som såg fel öppettider{on}?",
           "wrote": "Kunden skrev: \"{q}\"", "draft": "Förslag till svar: \"{d}\"", "more": "+ {n} till"},
    "nb": {"apology_day": "Takk for tilbakemeldingen, og beklager: åpningstidene våre på nett var feil {on}.",
           "apology": "Takk for tilbakemeldingen, og beklager: åpningstidene våre på nett var utdaterte.",
           "past": "Sist {on} ({date}) hadde vi {state}.", "future": "{On} ({date}) har vi {state}.",
           "closed": "stengt", "open": "åpent {h}", "regular": "åpent som vanlig",
           "promise_day": "Vi oppdaterer helligdagstidene våre i god tid fremover.",
           "promise": "Vi holder åpningstidene våre oppdatert fremover.", "bye": "Velkommen tilbake!",
           "ask_closed_1": "Vil du svare kunden som kom til en stengt dør{on}?",
           "ask_closed_n": "Vil du svare de {n} kundene som kom til en stengt dør{on}?",
           "ask_wrong_1": "Vil du svare kunden som så feil åpningstider{on}?",
           "ask_wrong_n": "Vil du svare de {n} kundene som så feil åpningstider{on}?",
           "wrote": "Kunden skrev: \"{q}\"", "draft": "Forslag til svar: \"{d}\"", "more": "+ {n} til"},
    "en": {"apology_day": "Thank you for your review, and sorry: our opening hours online were wrong {on}.",
           "apology": "Thank you for your review, and sorry: our opening hours online were out of date.",
           "past": "Last time {on} ({date}) we were {state}.", "future": "{On} ({date}) we are {state}.",
           "closed": "closed", "open": "open {h}", "regular": "open as usual",
           "promise_day": "We'll update our holiday hours in good time from now on.",
           "promise": "We'll keep our opening hours up to date from now on.", "bye": "Welcome back!",
           "ask_closed_1": "Reply to the customer who found you closed{on}?",
           "ask_closed_n": "Reply to the {n} customers who found you closed{on}?",
           "ask_wrong_1": "Reply to the customer who saw the wrong hours{on}?",
           "ask_wrong_n": "Reply to the {n} customers who saw the wrong hours{on}?",
           "wrote": "The customer wrote: \"{q}\"", "draft": "Suggested reply: \"{d}\"", "more": "+ {n} more"},
}
BUTTON_IDS = ("APPROVE", "EDIT", "DECLINE")


# ---------------------------------------------------------------------------
# 1. Find the complaints
# ---------------------------------------------------------------------------
def is_complaint(review: dict) -> bool:
    """A low-rated review about wrong hours / a closed door that isn't a thank-you."""
    text = review.get("content") or ""
    rating = review.get("rating")
    return (bool(COMPLAINT_RE.search(text)) and not THANKS_RE.search(text)
            and (rating is None or float(rating) <= MAX_RATING))


def has_owner_reply(review: dict) -> bool:
    return bool(review.get("comments"))


def detect_lang(text: str, default: str = "en") -> str:
    scores = {lang: len(re.findall(rx, text or "", re.IGNORECASE)) for lang, rx in _LANG_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default


def _local_date(ms: int, country: str) -> date:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo(TZ.get(country, "Europe/Helsinki"))).date()


def _special_day(country: str, day_key: str | None, review_day: date) -> tuple[str | None, date | None]:
    """(day_key, date) the review is about: the named day's latest date on/before the review
    (within 30 days), else the latest special day 0–3 days before the review."""
    from cs import data
    cal = data.calendar()
    cal = cal[cal["country"] == country]
    if day_key:
        rows = cal[(cal["day_key"] == day_key) & (cal["date"] <= review_day.isoformat())
                   & (cal["date"] >= (review_day - timedelta(days=30)).isoformat())]
        return day_key, (date.fromisoformat(rows["date"].max()) if not rows.empty else None)
    rows = cal[(cal["date"] <= review_day.isoformat()) & (cal["date"] >= (review_day - timedelta(days=3)).isoformat())]
    if rows.empty:
        return None, None
    last = rows.sort_values("date").iloc[-1]
    return last["day_key"], date.fromisoformat(last["date"])


def unanswered_complaints(entity_id: str, as_of: date | str = DEFAULT_AS_OF) -> list[dict]:
    """Complaints with no owner reply, oldest first::

        {review_id, publisher, review_date, rating, content, day_key, day_date, closed_door}

    Author names are never read into the result (GDPR minimisation)."""
    from cs import data
    country = entity_id[:2]
    out = []
    for r in data.reviews(as_of):
        if r["locationId"] != entity_id or has_owner_reply(r) or not is_complaint(r):
            continue
        rd = _local_date(r["publisherDate"], country)
        key, d = _special_day(country, detect_day_key(r["content"]), rd)
        out.append({"review_id": str(r["id"]), "publisher": r.get("publisherId"), "review_date": rd.isoformat(),
                    "rating": r.get("rating"), "content": r["content"], "day_key": key,
                    "day_date": d.isoformat() if d else None,
                    "closed_door": bool(CLOSED_DOOR_RE.search(r["content"]))})
    return sorted(out, key=lambda x: x["review_date"])


# ---------------------------------------------------------------------------
# 2. The confirmed hours and the draft
# ---------------------------------------------------------------------------
def confirmed_hours(entity_id: str, day_key: str | None, *, after_date: date | None = None,
                    after_ts: str | None = None, as_of: date | str = DEFAULT_AS_OF,
                    use_db: bool = True) -> dict | None:
    """The newest confirmed value for this special day: an owner's approved answer
    (``learned_facts``) or an exception entered in Yext (``holiday_hours_history``, before
    ``as_of``). Only values for a date after ``after_date`` or entered after ``after_ts`` count
    (what the business did *since* the complaint). ``{date, hours, source, ref}`` or None."""
    if not day_key or day_key == "EVENT":
        return None
    from cs import data
    cands: list[dict] = []
    if use_db:
        try:
            from cs.learn import active_facts
            for f in active_facts(entity_id):
                if f.day_key == day_key and f.value:
                    cands.append({"date": f.date, "hours": f.value, "source": "OWNER_ANSWER",
                                  "ref": f"learned_facts:{f.id}", "entered_at": f.created_at.isoformat()})
        except Exception:  # noqa: BLE001 - no database: the dataset alone still works
            pass
    cal = data.calendar()
    for d in cal[(cal["country"] == entity_id[:2]) & (cal["day_key"] == day_key)]["date"]:
        entries = data.holiday_entries(entity_id, d, as_of)
        if entries:
            e = entries[-1]
            cands.append({"date": d, "hours": e["hours"], "source": "HOLIDAY_ENTRY",
                          "ref": f"holiday_hours_history:{entity_id}:{d}", "entered_at": e["entered_at"]})
    ok = [c for c in cands
          if (after_date is not None and c["date"] > after_date.isoformat())
          or (after_ts is not None and c["entered_at"] > after_ts)]
    if not ok:
        return None
    best = max(ok, key=lambda c: (c["date"], c["entered_at"]))
    h = best["hours"]
    best["hours"] = "regular" if h == "REGULAR" else h
    return best


def _state(hours: str, lang: str) -> str:
    t = T[lang]
    if hours == "closed":
        return t["closed"]
    if hours == "regular":
        return t["regular"]
    return t["open"].format(h=C.fmt_hours(hours, lang))


def draft_reply(complaint: dict, lang: str, confirmed: dict | None, *, as_of: date | str = DEFAULT_AS_OF) -> str:
    """The reply text (template). Facts: the special day's name, and the confirmed date + hours."""
    lang = C.norm_lang(lang)
    t = T[lang]
    on = on_day(complaint.get("day_key"), lang)
    parts = [t["apology_day"].format(on=on) if on else t["apology"]]
    if on and confirmed:
        d = date.fromisoformat(confirmed["date"])
        tmpl = t["past"] if d < C._to_date(as_of) else t["future"]
        parts.append(tmpl.format(on=on, On=cap(on), date=C.fmt_date(d, lang, weekday=False),
                                 state=_state(confirmed["hours"], lang)))
    parts += [t["promise_day"] if on else t["promise"], t["bye"]]
    return " ".join(parts)


def _quote(text: str) -> str:
    t = " ".join((text or "").split())
    if len(t) <= QUOTE_MAX:
        return t
    return t[:QUOTE_MAX].rsplit(" ", 1)[0].rstrip(",.;:") + " …"


def review_reply_drafts(entity_id: str, lang: str | None = None, *, as_of: date | str = DEFAULT_AS_OF,
                        polish: bool = False, use_db: bool = True) -> list[dict]:
    """One draft per unanswered complaint::

        {"suggestion": Suggestion(kind="REVIEW_REPLY"), "review": {...}, "facts": {...}|None, "lang": "sv"}

    The reply is in the review's language (``lang`` is the fallback when it can't be told)."""
    out = []
    if lang is None:
        try:
            from cs import data
            lang = data.entity(entity_id)["meta"].get("language")
        except KeyError:
            lang = "en"
    for c in unanswered_complaints(entity_id, as_of):
        rlang = detect_lang(c["content"], default=C.norm_lang(lang))
        conf = confirmed_hours(entity_id, c["day_key"],
                               after_date=date.fromisoformat(c["day_date"]) if c["day_date"] else None,
                               after_ts=c["review_date"], as_of=as_of, use_db=use_db)
        text = draft_reply(c, rlang, conf, as_of=as_of)
        version = "review_reply_template"
        if polish:
            from cs.llm import polish_reason
            polished = polish_reason(text, rlang)
            if polished != text:
                text, version = polished, "review_reply_template+llm"
        ev = [Evidence(source="REVIEW", detail=_quote(c["content"]), ref=f"reviews:{c['review_id']}", weight=1.0)]
        if conf:
            src = "OWNER_ANSWER" if conf["source"] == "OWNER_ANSWER" else "OWN_LAST_YEAR"
            ev.append(Evidence(source=src, detail=f"{conf['date']}: {conf['hours']}", ref=conf["ref"], weight=1.0))
        sug = Suggestion(entity_id=entity_id, kind="REVIEW_REPLY", date=None, day_key=c["day_key"], shown=None,
                         action="SUGGEST", value=text, probability=None, basis="REVIEW", evidence=ev,
                         model_version=version)
        out.append({"suggestion": sug, "review": c, "facts": conf, "lang": rlang})
    return out


def review_reply_suggestions(entity_id: str, lang: str | None = None, **kw) -> list[Suggestion]:
    return [d["suggestion"] for d in review_reply_drafts(entity_id, lang, **kw)]


def review_id_of(suggestion: Suggestion) -> str | None:
    for e in suggestion.evidence:
        if e.source == "REVIEW" and (e.ref or "").startswith("reviews:"):
            return e.ref.split(":", 1)[1]
    return None


# ---------------------------------------------------------------------------
# 3. The offer to the owner
# ---------------------------------------------------------------------------
def compose_review_reply_offer(business, drafts: list, lang: str, channel: str = "WHATSAPP", *,
                               max_shown: int = 2) -> dict:
    """"Reply to the 2 customers who found you closed on Good Friday?" + each review quote and
    draft (up to ``max_shown``) + Approve / Edit / No thanks. ``drafts`` are the dicts from
    ``review_reply_drafts`` (or REVIEW_REPLY Suggestions)."""
    lang = C.norm_lang(lang)
    t = T[lang]
    items = [d if isinstance(d, dict) else {"suggestion": d, "review": None} for d in drafts]
    if not items:
        raise ValueError("no review replies to offer")
    sugs = [d["suggestion"] for d in items]
    keys = {s.day_key for s in sugs}
    on = on_day(next(iter(keys)), lang) if len(keys) == 1 else None
    on_txt = f" {on}" if on else ""
    closed = all((d.get("review") or {}).get("closed_door", True) for d in items)
    n = len(items)
    ask = t[("ask_closed_" if closed else "ask_wrong_") + ("1" if n == 1 else "n")].format(n=n, on=on_txt)
    biz = C._business(business)
    greeting = C.T[lang]["hi"].format(name=biz.get("name") or "").replace(" !", "!")

    def render(k: int) -> list[str]:
        lines = [f"{greeting} {ask}"]
        for d in items[:k]:
            rv = next((e.detail for e in d["suggestion"].evidence if e.source == "REVIEW"), "")
            lines += [t["wrote"].format(q=rv), t["draft"].format(d=d["suggestion"].value)]
        if k < n:
            lines.append(t["more"].format(n=n - k))
        return lines

    k = min(max_shown, n)
    lines = render(k)
    limit = C.CHANNEL_LIMITS.get((channel or "").upper())
    while limit and k > 1 and len("\n".join(lines)) > limit:
        k -= 1
        lines = render(k)
    parts = {"kind": "REVIEW_REPLY", "ask": ask, "suggestion_ids": [s.id for s in sugs], "shown": k,
             "review_ids": [review_id_of(s) for s in sugs]}
    return C._finish(lines, C.make_buttons(BUTTON_IDS, lang), channel, lang, parts)


# ---------------------------------------------------------------------------
# 4. Approve (stored, not posted)
# ---------------------------------------------------------------------------
def approve_review_reply(suggestion: Suggestion | dict, final_text: str | None = None, *, db=None,
                         via: str = "web", audit: bool = True) -> dict:
    """Store the approved reply in Mongo ``review_replies`` (status ``APPROVED_NOT_POSTED``) with the
    Yext call that would post it. ``final_text`` is the owner's edited text, if any."""
    sug = suggestion["suggestion"] if isinstance(suggestion, dict) else suggestion
    text = (final_text if final_text is not None else sug.value or "").strip()
    if not text or len(text) > 4000:
        raise ValueError("the reply must be 1–4000 characters")
    rid = review_id_of(sug)
    doc = {"id": new_id("rr"), "entity_id": sug.entity_id, "review_id": rid, "suggestion_id": sug.id,
           "day_key": sug.day_key, "text": text, "edited": final_text is not None and text != (sug.value or ""),
           "status": "APPROVED_NOT_POSTED",
           "would_post": {"api": "Yext Reviews API", "method": "POST",
                          "path": YEXT_COMMENT_PATH.format(review_id=rid), "body": {"content": text}},
           "note": NOT_POSTED_NOTE, "via": via, "approved_at": utcnow()}
    if db is None:
        from cs.db import get_db
        db = get_db()
    db[COLLECTION].insert_one(dict(doc))
    if audit:
        try:
            from cs import audit as audit_mod
            audit_mod.log("REVIEW_REPLY_APPROVED", {"review_reply_id": doc["id"], "review_id": rid,
                                                    "suggestion_id": sug.id, "edited": doc["edited"],
                                                    "status": doc["status"]}, entity_id=sug.entity_id, db=db)
        except Exception:  # noqa: BLE001
            pass
    return doc


def stored_replies(entity_id: str, db=None) -> list[dict]:
    if db is None:
        from cs.db import get_db
        db = get_db()
    return list(db[COLLECTION].find({"entity_id": entity_id}, {"_id": 0}).sort("approved_at", 1))


def entities_with_complaints(ids: Iterable[str], as_of: date | str = DEFAULT_AS_OF) -> dict[str, int]:
    """{entity_id: number of unanswered complaints} for the given businesses (demo helper)."""
    return {e: n for e in ids if (n := len(unanswered_complaints(e, as_of)))}

