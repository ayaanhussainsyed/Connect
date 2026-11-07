"""Optional LLM help (APP_DESIGN §4.3, L2/L3): polish one reason sentence, parse free-text replies.

Facts never come from an LLM:
- ``polish_reason`` may reword only the reason sentence; ``slot_check`` must confirm every date,
  time and number in the output is also in the original (and that none was dropped), or the
  original is kept.
- ``parse_reply`` asks Claude to reason first and then output JSON; the result is validated with
  ``cs.hours`` and anything doubtful returns ``None`` (then a person, or the card page, decides).

Everything works with the LLM switched off (``LLM_ENABLED=0``) or unreachable: a small regex
parser handles digits, yes/no words, "kiinni / stängt / stengt / closed", holiday names,
explicit dates and ``HH(:MM)?–HH(:MM)?`` ranges, so the demo never depends on the network.

Privacy (PLAN Part F): no names, phone numbers or e-mail addresses go into a prompt
(``scrub``); the owner's reason for an emergency closure is never asked for or kept.
Each call times out after ~20 s and falls back.

CLI::

    python -m cs.llm parse "kiinni tapaninpäivänä, muuten ok" --dates 2026-12-24,2026-12-25,2026-12-26
    python -m cs.llm parse "1" --dates 2026-12-24 --no-llm
    python -m cs.llm polish "Merkitsitte viime vuonna samalle päivälle samat ajat: 7.30–13." --lang fi
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from cs.compose import (_RANGE_RE, _canon_interval, extract_slots, fmt_hours, norm_lang)
from cs.config import settings
from cs.hours import is_valid, normalise

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
MIN_CONFIDENCE = 0.6
LANG_NAMES = {"fi": "Finnish", "sv": "Swedish", "nb": "Norwegian Bokmål", "en": "English"}
ACTIONS = ("APPROVE", "APPROVE_EVERY_YEAR", "EDIT", "DECLINE", "STOP", "EMERGENCY", "ANSWER", "UNCLEAR")


# ---------------------------------------------------------------------------
# Switches and clients
# ---------------------------------------------------------------------------
def llm_enabled() -> bool:
    """``LLM_ENABLED`` env var, default on; 0 / false / no / off switch every LLM call off."""
    return os.environ.get("LLM_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def claude_available() -> bool:
    return (llm_enabled() and settings.has("anthropic_foundry_api_key")
            and settings.has("anthropic_foundry_base_url")
            and bool(settings.claude_model or settings.claude_fast_model))


def astra_available() -> bool:
    return (llm_enabled() and settings.has("openai_api_key") and settings.has("openai_base_url")
            and settings.has("openai_model"))


def claude_text(prompt: str, *, system: str | None = None, fast: bool = False, max_tokens: int = 1024,
                timeout: float = TIMEOUT_S) -> str | None:
    """One Claude call (Microsoft Foundry). Returns the text, or None if off / failed / timed out."""
    if not claude_available():
        return None
    try:
        import anthropic
        client = anthropic.AnthropicFoundry(api_key=settings.anthropic_foundry_api_key,
                                            base_url=settings.anthropic_foundry_base_url,
                                            timeout=timeout, max_retries=0)
        model = ((settings.claude_fast_model if fast else settings.claude_model)
                 or settings.claude_model or settings.claude_fast_model)
        kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": prompt}]}
        if system:
            kw["system"] = system
        resp = client.messages.create(**kw)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        if not text.strip() and not fast and settings.claude_fast_model and model != settings.claude_fast_model:
            # An empty answer (e.g. stop_reason "refusal"): one retry on the fast model.
            log.warning("Claude returned no text (stop_reason=%s); retrying on the fast model",
                        getattr(resp, "stop_reason", "?"))
            kw["model"] = settings.claude_fast_model
            resp = client.messages.create(**kw)
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return text.strip() or None
    except Exception as e:  # noqa: BLE001 - never let the LLM break the flow; never log secrets
        log.warning("Claude call failed (%s); using the fallback", type(e).__name__)
        return None


def astra_text(prompt: str, *, timeout: float = TIMEOUT_S) -> str | None:
    """One GPT-6 Astra call (Azure OpenAI v1 Responses API). Text, or None."""
    if not astra_available():
        return None
    try:
        import openai
        client = openai.OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key,
                               timeout=timeout, max_retries=0)
        resp = client.responses.create(model=settings.openai_model, input=prompt)
        return (resp.output_text or "").strip() or None
    except Exception as e:  # noqa: BLE001
        log.warning("GPT-6 Astra call failed (%s); using the fallback", type(e).__name__)
        return None


# ---------------------------------------------------------------------------
# Privacy: nothing personal goes into a prompt
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w:.])(?:\+|00)\d[\d\s-]{6,16}\d|(?<![\w:.])0\d{1,3}[\s-]?\d{5,8}(?!\d)")
_URL_RE = re.compile(r"https?://\S+")


def scrub(text: str, names: Iterable[str] = ()) -> str:
    """Remove e-mails, phone numbers, links and the given names (owner, business) from ``text``."""
    out = _EMAIL_RE.sub("[email]", text or "")
    out = _URL_RE.sub("[link]", out)
    out = _PHONE_RE.sub("[phone]", out)
    for n in sorted({n for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        out = re.sub(re.escape(n.strip()), "[name]", out, flags=re.IGNORECASE)
    return out


# ---------------------------------------------------------------------------
# L3: polish the reason sentence, with a slot check
# ---------------------------------------------------------------------------
_CLOSED_WORD_RE = re.compile(r"\b(kiinni|suljettu|suljet\w*|stängt|stängd|stengt|closed?)\b", re.IGNORECASE)


def slot_check(original: str, polished: str, *, require_all: bool = True) -> bool:
    """True if every date, time range, time and number in ``polished`` also appears in
    ``original`` (format-independent: '7.30–13' == '07:30–13:00'). With ``require_all``
    (default) no date or time range of the original may be missing either, and a
    closed/open statement may not appear or disappear."""
    if not polished or not polished.strip():
        return False
    o, p = extract_slots(original), extract_slots(polished)
    endpoints = {t for iv in o.intervals for t in iv.split("-")}
    if not (p.intervals <= o.intervals and p.dates <= o.dates
            and p.times <= (o.times | endpoints) and p.numbers <= o.numbers):
        return False
    if require_all and not (o.intervals <= p.intervals and o.dates <= p.dates):
        return False
    if bool(_CLOSED_WORD_RE.search(original)) != bool(_CLOSED_WORD_RE.search(polished)):
        return False
    return True


def polish_reason(sentence: str, lang: str, *, protect: Iterable[str] = (), use_llm: bool | None = None) -> str:
    """Let Claude reword one reason sentence; keep the original unless ``slot_check`` passes.

    ``protect``: proper names in the sentence (e.g. the shopping centre). They are replaced
    by placeholders before the call (no names in prompts) and put back afterwards."""
    use = llm_enabled() if use_llm is None else use_llm
    if not use or not sentence or not claude_available():
        return sentence
    lang = norm_lang(lang)
    masked = sentence
    tokens: dict[str, str] = {}
    for i, name in enumerate(n for n in protect if n):
        tok = f"[N{i + 1}]"
        if name in masked:
            masked = masked.replace(name, tok)
            tokens[tok] = name
    masked = scrub(masked)
    prompt = (f"Rewrite this {LANG_NAMES[lang]} sentence so it sounds natural, warm and brief to a "
              f"small-business owner. Keep every date, time and number exactly, keep tokens like "
              f"[N1] unchanged, do not add or remove any fact. Reply with the rewritten sentence only, "
              f"in {LANG_NAMES[lang]}.\n\nSentence: {masked}")
    out = claude_text(prompt, max_tokens=300)
    if not out:
        return sentence
    out = out.strip().strip('"“”«»').strip()
    if "\n" in out or "http" in out or len(out) > 2 * len(masked) + 40 or any(t not in out for t in tokens):
        return sentence
    for tok, name in tokens.items():
        out = out.replace(tok, name)
    return out if slot_check(sentence, out) else sentence


# ---------------------------------------------------------------------------
# L2: parse a free-text reply
# ---------------------------------------------------------------------------
# Fixed-date holidays by name -> (month, day); longest names first so "lillejulafton" wins over "julafton".
HOLIDAY_NAMES: list[tuple[str, tuple[int, int]]] = [
    (r"lille\s*julafte?n|lillejulafton|aatonaat\w*|dec(?:ember)?\s*23\b", (12, 23)),
    (r"jouluaat\w*|julafton\w*|julaften\w*|christmas\s+eve", (12, 24)),
    (r"joulupäiv\w*|juldag\w*|(?:første|1\.)\s*juledag\w*|christmas\s+day|christmas\b", (12, 25)),
    (r"tapanin\w*|annandag\w*(?:\s+jul)?|(?:andre|2\.)\s*juledag\w*|boxing\s+day", (12, 26)),
    (r"uudenvuoden\s*aat\w*|uudenvuodenaat\w*|nyårsafton\w*|nyttårsafte\w*|new\s+year'?s\s+eve", (12, 31)),
    (r"uudenvuoden\s*päiv\w*|uudenvuodenpäiv\w*|nyårsdag\w*|nyttårsdag\w*|(?:første\s+)?nyttårsdag|new\s+year'?s(?:\s+day)?", (1, 1)),
    (r"loppiaisaat\w*|trettondagsafton\w*|epiphany\s+eve", (1, 5)),
    (r"loppiai\w*|trettondag\w*|epiphany", (1, 6)),
    (r"itsenäisyyspäiv\w*|independence\s+day", (12, 6)),
    (r"lucia\w*", (12, 13)),
]
_HOLIDAY_RES = [(re.compile(p, re.IGNORECASE), md) for p, md in HOLIDAY_NAMES]
_CLOSED_RE = re.compile(r"\b(kiinni|suljettu|stängt|stängd|stengt|closed)\b", re.IGNORECASE)
_REGULAR_RE = re.compile(r"\b(tavalli\w*|normaali\w*|kuten\s+yleensä|ordinarie|som\s+vanligt|vanlig\w*|"
                         r"som\s+normalt|regular|as\s+usual|normal\w*)\b", re.IGNORECASE)
_EVERY_YEAR_RE = re.compile(r"\b(joka\s+vuosi|jokainen\s+vuosi|vuosittain|varje\s+år|hvert\s+år|every\s+year|"
                            r"each\s+year)\b", re.IGNORECASE)
_ONLY_THIS_YEAR_RE = re.compile(r"\b(vain\s+tänä\s+vuonna|bara\s+i\s+år|bare\s+i\s+år|only\s+this\s+year)\b",
                                re.IGNORECASE)
_OTHERWISE_OK_RE = re.compile(r"\b(muuten|muut|annars|resten|övrigt|ellers|otherwise|rest|others?)\b\s*"
                              r"(?:is\s+|are\s+|on\s+)?(ok|okei|hyvä|hyviä|käy|sopii|kunnossa|bra|greit|fine|good)\b",
                              re.IGNORECASE)
_TODAY_RE = re.compile(r"\b(tänään|idag|i\s+dag|today)\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\b(huomenna|i\s*morgon|i\s+morgen|tomorrow)\b", re.IGNORECASE)
_FURTHER_RE = re.compile(r"\b(toistaiseksi|tills\s+vidare|inntil\s+videre|until\s+further\s+notice)\b",
                         re.IGNORECASE)
_APPROVE_WORDS = {"ok", "okei", "okay", "oki", "kyllä", "joo", "juu", "käy", "sopii", "hyväksy", "hyväksyn",
                  "hyvä", "ja", "japp", "jo", "godkänn", "godkänner", "godkjenn", "godkjenner", "bra", "greit",
                  "yes", "yep", "approve", "approved", "fine", "sounds good", "👍", "ok kiitos", "ok tack",
                  "ok takk", "ok thanks"}
_DECLINE_WORDS = {"ei", "ei kiitos", "nej", "nej tack", "nei", "nei takk", "no", "no thanks", "decline", "älä"}
_STOP_WORDS = {"stop", "stopp", "lopeta", "sluta", "slutt", "unsubscribe", "älä lähetä"}
_CARD_DIGITS = {"1": "APPROVE", "2": "APPROVE_EVERY_YEAR", "3": "EDIT", "4": "DECLINE"}
_QUESTION_DIGITS = {"1": "closed", "2": "regular", "3": "other"}
_CLAUSE_SPLIT = re.compile(r"[,;\n]+|\s+(?:ja|sekä|mutta|och|men|og|and|but)\s+", re.IGNORECASE)
_EXPLICIT_DATE_RE = re.compile(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})(?:\.(?!\d))?(?![\d/]|:\d)")


def _to_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, dict):
        return _to_date(d.get("date"))
    return date.fromisoformat(str(d)[:10])


def _norm_value(v: Any) -> str | None:
    """'closed' / 'regular' / canonical hours, or None if it isn't valid."""
    if v is None:
        return None
    s = str(v).strip()
    low = s.lower()
    if low in ("closed", "kiinni", "stängt", "stengt", "suljettu"):
        return "closed"
    if low in ("regular", "regular hours", "normal"):
        return "regular"
    try:
        out = normalise(s)
    except (ValueError, TypeError):
        return None
    return out if is_valid(out) else None


def _simple(text: str) -> str:
    return re.sub(r"[\s.!?]+$", "", re.sub(r"\s+", " ", (text or "").strip().lower())).strip(" ,")


def _clause_dates(clause: str, by_md: dict[tuple[int, int], date]) -> tuple[list[date], bool]:
    """Card dates named in a clause (explicit or by holiday name), and whether an unknown date was named."""
    found: list[date] = []
    unknown = False
    work = clause
    for rx, (month, day) in _HOLIDAY_RES:
        if rx.search(work):
            work = rx.sub(" ", work)
            if (day, month) in by_md:
                found.append(by_md[(day, month)])
            else:
                unknown = True
    for m in _EXPLICIT_DATE_RE.finditer(work):
        md = (int(m.group(1)), int(m.group(2)))
        if not (1 <= md[0] <= 31 and 1 <= md[1] <= 12):
            continue
        if md in by_md:
            found.append(by_md[md])
        else:
            unknown = True
    return found, unknown


def _clause_ranges(clause: str) -> list[str]:
    # Blank out dates first so '24.12. 10-14' does not read '12' as part of a time.
    def blank(m: re.Match) -> str:
        valid = 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12
        nxt = clause[m.end():m.end() + 2].lstrip()
        is_range_start = nxt[:1] in ("-", "–", "—")  # '9.30–13' is a time range, not 9 Sept 30
        return " " * len(m.group(0)) if valid and not is_range_start else m.group(0)
    work = _EXPLICIT_DATE_RE.sub(blank, clause)
    out = []
    for m in _RANGE_RE.finditer(work):
        iv = _canon_interval(*m.groups())
        if iv:
            out.append(iv)
    return out


def _result(action: str, *, dates: dict | None = None, every_year=None, emergency=None,
            approve_others=None, confidence: float = 0.9, source: str = "regex", answer=None) -> dict:
    return {"action": action, "dates": dates or {}, "every_year": every_year, "emergency": emergency,
            "approve_others": approve_others, "confidence": confidence, "source": source, "answer": answer}


def parse_reply_regex(text: str, lang: str, card_dates: Iterable, *, today: date | None = None,
                      expect: str = "card") -> dict | None:
    """The offline parser. Returns the same shape as ``parse_reply`` (``source='regex'``) or None."""
    today = today or date.today()
    dates = sorted({_to_date(d) for d in card_dates})
    by_md = {(d.day, d.month): d for d in dates}
    raw = (text or "").strip()
    if not raw:
        return None
    simple = _simple(raw)
    every_year = True if _EVERY_YEAR_RE.search(raw) else (False if _ONLY_THIS_YEAR_RE.search(raw) else None)

    # 1. Digits (buttons by SMS) and one-word answers.
    if re.fullmatch(r"[1-4]", simple):
        if expect == "question":
            if simple not in _QUESTION_DIGITS or len(dates) != 1:
                return None
            ans = _QUESTION_DIGITS[simple]
            vals = {dates[0].isoformat(): ans} if ans != "other" else {}
            return _result("ANSWER", dates=vals, answer=ans, confidence=1.0)
        if expect == "readback":
            return _result("APPROVE" if simple == "1" else "DECLINE", confidence=1.0) if simple in "12" else None
        act = _CARD_DIGITS[simple]
        return _result(act, every_year=True if act == "APPROVE_EVERY_YEAR" else every_year, confidence=1.0)
    if simple in _STOP_WORDS:
        return _result("STOP", confidence=0.95)
    if simple in _DECLINE_WORDS:
        return _result("DECLINE", confidence=0.95)
    stripped = _EVERY_YEAR_RE.sub("", simple).strip(" ,+&")
    if simple in _APPROVE_WORDS or (every_year and (stripped in _APPROVE_WORDS or stripped == "")):
        return _result("APPROVE_EVERY_YEAR" if every_year else "APPROVE", every_year=every_year, confidence=0.95)

    # 2. Emergency: "kiinni tänään", "stängt i morgon", "stengt inntil videre", "open 12-16 today".
    has_card_date = bool(_clause_dates(raw, by_md)[0])
    if not has_card_date and (_TODAY_RE.search(raw) or _TOMORROW_RE.search(raw) or _FURTHER_RE.search(raw)):
        ranges = _clause_ranges(raw)
        if _CLOSED_RE.search(raw) or _FURTHER_RE.search(raw):
            hours = "closed"
        elif len(ranges) >= 1:
            hours = ",".join(ranges)
            if _norm_value(hours) is None:
                return None
            hours = _norm_value(hours)
        else:
            return None
        start = today + timedelta(days=1) if (_TOMORROW_RE.search(raw) and not _TODAY_RE.search(raw)) else today
        further = bool(_FURTHER_RE.search(raw))
        em = {"from": start.isoformat(), "to": None if further else start.isoformat(), "hours": hours,
              "until_further_notice": further}
        return _result("EMERGENCY", emergency=em, confidence=0.85)

    # 3. Per-date edits, clause by clause.
    out: dict[str, str] = {}
    approve_others = True if _OTHERWISE_OK_RE.search(raw) else None
    pending: list[date] = []
    for clause in (c.strip() for c in _CLAUSE_SPLIT.split(raw)):
        if not clause:
            continue
        if _OTHERWISE_OK_RE.search(clause) and not _CLOSED_RE.search(clause) and not _clause_ranges(clause):
            continue
        cds, unknown = _clause_dates(clause, by_md)
        if unknown:
            return None  # a date that isn't on the card: let a person / the card page handle it
        ranges = _clause_ranges(clause)
        state = None
        if _CLOSED_RE.search(clause) and not ranges:
            state = "closed"
        elif ranges and not _CLOSED_RE.search(clause):
            state = _norm_value(",".join(ranges))
            if state is None:
                return None
        elif _REGULAR_RE.search(clause) and not ranges:
            state = "regular"
        elif ranges and _CLOSED_RE.search(clause):
            return None  # "closed" and hours in one clause: ambiguous
        if state is None:
            leftovers = extract_slots(_EVERY_YEAR_RE.sub("", clause))
            if cds and not (leftovers.numbers - {d.day for d in cds} - {d.month for d in cds}):
                pending.extend(cds)  # "26.12.:" — the state may follow in the next clause
                continue
            if cds or leftovers.numbers or leftovers.times:
                return None  # numbers we can't read ("we close at 2"): ask the LLM or a person
            continue
        targets = cds or pending
        if not targets:
            if len(dates) == 1:
                targets = dates
            else:
                return None  # "kiinni" without saying which day
        for d in targets:
            if d.isoformat() in out and out[d.isoformat()] != state:
                return None
            out[d.isoformat()] = state
        pending = []
    if out:
        return _result("EDIT", dates=out, every_year=every_year, approve_others=approve_others, confidence=0.8)
    if approve_others:
        return _result("APPROVE", every_year=every_year, confidence=0.8)
    return None


# --- the LLM path ------------------------------------------------------------
_FIXED_NAMES = {(12, 23): "Day before Christmas Eve", (12, 24): "Christmas Eve", (12, 25): "Christmas Day",
                (12, 26): "Boxing Day / St Stephen's Day", (12, 31): "New Year's Eve", (1, 1): "New Year's Day",
                (1, 5): "Epiphany Eve", (1, 6): "Epiphany", (12, 6): "Finnish Independence Day",
                (12, 13): "St Lucy's Day"}
# The model "reasons first" through a short visible <analysis> block (asking for hidden reasoning
# inside tags makes Opus 5 refuse), then the JSON.
_SYSTEM = ("You read short replies from small-business owners to a message that suggested opening hours "
           "for specific dates, and turn them into JSON. You never invent hours or dates.")


def _llm_prompt(text: str, lang: str, dates: list[date], today: date, context: dict | None) -> str:
    lines = []
    for d in dates:
        name = _FIXED_NAMES.get((d.month, d.day))
        ctx = (context or {}).get(d.isoformat()) or (context or {}).get(d) or {}
        extra = ""
        if ctx.get("suggested"):
            extra += f" Suggested: {ctx['suggested']}."
        if ctx.get("shown"):
            extra += f" Google shows now: {ctx['shown']}."
        lines.append(f"- {d.isoformat()} ({d.strftime('%A')}{', ' + name if name else ''}).{extra}")
    return f"""Language of the reply: {LANG_NAMES.get(lang, lang)} ({lang}).
Today: {today.isoformat()} ({today.strftime('%A')}).
The message suggested hours for these dates:
{chr(10).join(lines)}

Owner's reply (personal data removed):
\"\"\"{text}\"\"\"

Return a JSON object with exactly these keys:
- "action": one of APPROVE (accepts every suggestion as is), APPROVE_EVERY_YEAR (accepts and wants the same every year), EDIT (states hours for some of the dates above), DECLINE (does not want the changes), STOP (wants no more messages), EMERGENCY (an unplanned change today or in the next days, e.g. "closed today"), UNCLEAR.
- "dates": an object mapping ISO dates FROM THE LIST ABOVE to "closed", "regular" (their normal weekly hours) or hours "HH:MM-HH:MM" (24-hour clock; split days joined with ","). Only dates the reply talks about. If only a closing (or opening) time is given, combine it with the suggested hours for that date, else with what Google shows now.
- "approve_others": true if they accept the remaining suggestions ("otherwise ok"), false if they reject them, null if not said.
- "every_year": true if they want the same every year, false if only this year, null if not said.
- "emergency": null unless action is EMERGENCY; then {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD" or null (last day, inclusive; null if until further notice), "hours": "closed" or "HH:MM-HH:MM", "until_further_notice": true or false}}. Never include the reason for the closure.
- "confidence": a number from 0 to 1: how sure you are that this JSON says exactly what the owner meant.
If anything is ambiguous (which date, which hours), use action UNCLEAR with a low confidence.

First write a short analysis inside <analysis>...</analysis>: which dates the reply mentions and what it says about each. Then output only the JSON inside <json>...</json>."""


def _extract_json(out: str) -> dict | None:
    m = re.search(r"<json>\s*(\{.*\})\s*</json>", out, re.DOTALL)
    blob = m.group(1) if m else None
    if blob is None:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", out, re.DOTALL) or re.search(r"(\{.*\})", out, re.DOTALL)
        blob = m.group(1) if m else None
    if blob is None:
        return None
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _tri(v) -> tuple[bool, bool | None]:
    """(ok, value) for a true/false/null field."""
    if v is None or isinstance(v, bool):
        return True, v
    return False, None


def validate_llm_result(obj: dict, card_dates: Iterable, today: date) -> dict | None:
    """Check the model's JSON against the card and the hours validator; None on any doubt."""
    allowed = {d.isoformat() for d in (_to_date(x) for x in card_dates)}
    action = str(obj.get("action") or "").upper()
    if action not in ACTIONS or action == "UNCLEAR":
        return None
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= conf <= 1.0) or conf < MIN_CONFIDENCE:
        return None
    raw_dates = obj.get("dates") or {}
    if not isinstance(raw_dates, dict):
        return None
    out: dict[str, str] = {}
    for k, v in raw_dates.items():
        try:
            iso = date.fromisoformat(str(k)[:10]).isoformat()
        except ValueError:
            return None
        if iso not in allowed:
            return None
        nv = _norm_value(v)
        if nv is None:
            return None
        out[iso] = nv
    ok1, every_year = _tri(obj.get("every_year"))
    ok2, approve_others = _tri(obj.get("approve_others"))
    if not (ok1 and ok2):
        return None
    emergency = None
    if action == "EMERGENCY":
        em = obj.get("emergency")
        if not isinstance(em, dict):
            return None
        try:
            start = date.fromisoformat(str(em.get("from"))[:10])
            end = date.fromisoformat(str(em["to"])[:10]) if em.get("to") else None
        except (ValueError, TypeError):
            return None
        hours = _norm_value(em.get("hours") or "closed")
        further = bool(em.get("until_further_notice"))
        if hours is None or hours == "regular" or not (today - timedelta(days=1) <= start <= today + timedelta(days=60)):
            return None
        if end is not None and not (start <= end <= start + timedelta(days=60)):
            return None
        if end is None and not further:
            end = start
        emergency = {"from": start.isoformat(), "to": end.isoformat() if end else None, "hours": hours,
                     "until_further_notice": further}  # only these keys: never a reason
    if action == "EDIT" and not out:
        return None
    if action == "APPROVE_EVERY_YEAR":
        every_year = True
    return _result(action, dates=out, every_year=every_year, emergency=emergency,
                   approve_others=approve_others, confidence=round(conf, 3), source="llm")


def _parse_reply_llm(text: str, lang: str, dates: list[date], today: date, context: dict | None,
                     names: Iterable[str]) -> tuple[str, dict | None]:
    """('ok', result) | ('doubt', None) | ('error', None)."""
    prompt = _llm_prompt(scrub(text, names), lang, dates, today, context)
    out = claude_text(prompt, system=_SYSTEM, max_tokens=1200)
    if out is None:
        return "error", None
    obj = _extract_json(out)
    if obj is None:
        return "error", None
    res = validate_llm_result(obj, dates, today)
    return ("ok", res) if res is not None else ("doubt", None)


def _conflicts(a: dict, b: dict) -> bool:
    for k, v in a.get("dates", {}).items():
        if b.get("dates", {}).get(k) != v:
            return True
    if a.get("emergency") and b.get("action") != "EMERGENCY":
        return True
    return False


def parse_reply(text: str, lang: str, card_dates: Iterable, *, today: date | None = None,
                context: dict | None = None, names: Iterable[str] = (), expect: str = "card",
                use_llm: bool | None = None) -> dict | None:
    """Read an owner's free-text reply.

    Returns ``{"dates": {iso: hours|'closed'|'regular'}, "every_year": bool|None,
    "emergency": {from, to, hours, until_further_notice}|None, "confidence": float,
    "action": APPROVE|APPROVE_EVERY_YEAR|EDIT|DECLINE|STOP|EMERGENCY|ANSWER,
    "approve_others": bool|None, "source": 'regex'|'llm', "answer": …}`` or **None on any doubt**.

    ``card_dates``: the dates on the card (ISO strings, dates, or dicts with "date").
    ``context``: optional {iso: {"suggested", "shown"}} so "we close at 2" can be completed.
    ``names``: owner / business names to strip before the prompt. ``expect``: "card" (digits
    1–4 = the card buttons), "question" (1 closed · 2 regular · 3 other) or "readback" (1 yes · 2 no).
    Digits and one-word answers never go to the LLM. If the LLM is off or fails, the regex
    parser answers; if the LLM and the regex parser disagree, the answer is None.
    """
    lang = norm_lang(lang)
    today = today or date.today()
    dates = sorted({_to_date(d) for d in card_dates})
    rx = parse_reply_regex(text, lang, dates, today=today, expect=expect)
    if rx is not None and (rx["confidence"] >= 0.95 or expect != "card"):
        return rx  # digits, yes/no/stop: deterministic
    use = llm_enabled() if use_llm is None else use_llm
    if use and claude_available():
        status, res = _parse_reply_llm(text, lang, dates, today, context, names)
        if status == "ok":
            if rx is not None and (_conflicts(rx, res) or _conflicts(res, rx)):
                return None
            return res
        if status == "doubt":
            return None
    return rx


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.llm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse an owner's reply")
    p.add_argument("text")
    p.add_argument("--dates", required=True, help="comma-separated ISO dates on the card")
    p.add_argument("--lang", default="fi", choices=("fi", "sv", "nb", "en"))
    p.add_argument("--today", default=None)
    p.add_argument("--expect", default="card", choices=("card", "question", "readback"))
    p.add_argument("--no-llm", action="store_true", help="regex parser only")
    q = sub.add_parser("polish", help="polish one reason sentence (slot-checked)")
    q.add_argument("sentence")
    q.add_argument("--lang", default="fi", choices=("fi", "sv", "nb", "en"))
    a = ap.parse_args(argv)
    if a.cmd == "parse":
        dates = [d.strip() for d in a.dates.split(",") if d.strip()]
        today = date.fromisoformat(a.today) if a.today else None
        res = parse_reply(a.text, a.lang, dates, today=today, expect=a.expect, use_llm=not a.no_llm)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if res and res.get("dates"):
            for k, v in res["dates"].items():
                print(f"  {k}: {fmt_hours(v, a.lang) if v not in ('regular',) else v}", file=sys.stderr)
        return 0 if res is not None else 1
    out = polish_reason(a.sentence, a.lang)
    print(out)
    print(f"(slot check: {'kept original' if out == a.sentence else 'passed'})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
