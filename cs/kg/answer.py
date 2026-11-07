"""B10 grounded chat: answer an owner's question from the business memory graph only.

    ask("FI-D0001", "Mitä tein viime pääsiäisenä?", "fi")
    -> {"answer", "citations": [ref], "used_nodes": [node id], "grounded": bool, "mode": "llm"|"template"|"fallback"}

1. ``retrieve.resolve_question`` finds the days and topics; ``retrieve.subgraph`` follows the graph's links.
2. Each retrieved node becomes one or more fact lines, each tagged with its ``ref``.
3. Claude (``cs.llm.claude_text``) answers in the owner's language from those facts **only**, citing a ref after every
   fact. Names, e-mails and phone numbers are removed before the call (``cs.llm.scrub``); the business and shopping
   centre are referred to as "the business" / "the shopping centre".
4. The answer is accepted only if every citation is a retrieved ref and every date, time and number in it appears in
   the facts (``check_answer``). Otherwise: a safe "I don't know that yet" in the owner's language.
5. With ``LLM_ENABLED=0`` (or no LLM reachable) the answer is a template filled straight from the facts.
"""
from __future__ import annotations

import re

from cs.engine import data as D

from .retrieve import resolve_question, subgraph

LANGS = ("fi", "sv", "nb", "en")
DONT_KNOW = {
    "fi": "En tiedä sitä vielä – voin kysyä asiaa yritykseltä.",
    "sv": "Det vet jag inte än – jag kan fråga företaget.",
    "nb": "Det vet jeg ikke ennå – jeg kan spørre bedriften.",
    "en": "I don't know that yet – I can ask the business.",
}
CLOSED = {"fi": "kiinni", "sv": "stängt", "nb": "stengt", "en": "closed"}
REGULAR = {"fi": "tavalliset aukioloajat", "sv": "ordinarie öppettider", "nb": "vanlige åpningstider", "en": "regular hours"}
T = {
    "entry": {"fi": "{date} ({day}) olitte merkinneet: {hours}.", "sv": "Den {date} ({day}) hade ni angett: {hours}.",
              "nb": "Den {date} ({day}) hadde dere registrert: {hours}.", "en": "On {date} ({day}) you had entered: {hours}."},
    "fact": {"fi": "Vahvistitte päivälle {date} ({day}): {hours}.", "sv": "Ni bekräftade för {date} ({day}): {hours}.",
             "nb": "Dere bekreftet for {date} ({day}): {hours}.", "en": "You confirmed for {date} ({day}): {hours}."},
    "mall": {"fi": "Kauppakeskus on merkinnyt päivälle {date} ({day}): {hours}.",
             "sv": "Köpcentret har angett för {date} ({day}): {hours}.",
             "nb": "Kjøpesenteret har registrert for {date} ({day}): {hours}.",
             "en": "Your shopping centre has entered for {date} ({day}): {hours}."},
    "peers": {"fi": "{group}: kaudella {season} päivän {day} merkinneistä {n} yrityksestä {pc} % oli kiinni; yleisin "
                    "poikkeava aukioloaika oli {top}.",
              "sv": "{group}: säsongen {season} hade {pc} % av de {n} företag som angav {day} stängt; vanligaste "
                    "avvikande tider var {top}.",
              "nb": "{group}: i sesongen {season} holdt {pc} % av de {n} bedriftene som registrerte {day} stengt; "
                    "vanligste avvikende tider var {top}.",
              "en": "{group}: in the {season} season, {pc}% of the {n} businesses that entered {day} were closed; the "
                    "most common special hours were {top}."},
    "shown": {"fi": "Google näyttää nyt päivälle {date} ({day}): {shown}.", "sv": "Google visar nu för {date} ({day}): {shown}.",
              "nb": "Google viser nå for {date} ({day}): {shown}.", "en": "Google now shows for {date} ({day}): {shown}."},
    "suggest": {"fi": "Connect ehdottaa: {hours} (varmuus {p} %).", "sv": "Connect föreslår: {hours} (säkerhet {p} %).",
                "nb": "Connect foreslår: {hours} (sikkerhet {p} %).", "en": "Connect suggests: {hours} ({p}% confidence)."},
}


def _fmt_date(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{int(dd)}.{int(m)}.{y}"


def _fmt_hours(h: str | None, lang: str) -> str:
    if h is None:
        return "–"
    if h == "closed":
        return CLOSED[lang]
    if h == "REGULAR":
        return REGULAR[lang]
    return h.replace("-", "–")


def _day_name(day: dict | None, lang: str) -> str:
    if not day:
        return ""
    p = day["props"]
    return p.get("name_en") if lang == "en" else p.get("name_local") or p.get("name_en")


# ----------------------------------------------------------------------------- facts for the LLM (English)
def fact_lines(sub: dict) -> list[tuple[str, str]]:
    """(ref, text) for every retrieved fact. No business, mall or person names."""
    nodes = {n["id"]: n for n in sub["nodes"]}
    day_of = {}
    for e in sub["edges"]:
        if e["type"] == "FOR_DAY" and e["dst"] in nodes:
            day_of[e["src"]] = nodes[e["dst"]]
    out = []
    for n in sub["nodes"]:
        p, t, ref = n["props"], n["type"], n["ref"]
        if t == "BUSINESS":
            wk = ", ".join(f"{d[:3]} {h}" for d, h in (p.get("regular_week") or {}).items())
            out.append((ref, f"The business is a {p['segment'].replace('_', ' ')} in {p['city']} ({p['country']}); "
                             f"its regular weekly hours are: {wk}."))
        elif t == "SPECIAL_DAY":
            out.append((ref, f"{_fmt_date(p['date'])} is {p['name_en']} ({p['day_key']}, {p['day_type'].lower()})."))
            if p.get("shown_now"):
                out.append((p["shown_ref"], f"For {_fmt_date(p['date'])} Google and other apps currently show: "
                                            f"{p['shown_now']}."))
            if p.get("suggestion_action"):
                if p["suggestion_action"] == "SUGGEST":
                    out.append((p["suggestion_ref"], f"For {_fmt_date(p['date'])} Connect suggests: {p['suggestion_hours']} "
                                                     f"(confidence {round(100 * p['suggestion_probability'])}%, basis "
                                                     f"{p['suggestion_basis']})."))
                elif p["suggestion_action"] == "NO_ACTION":
                    out.append((p["suggestion_ref"], f"For {_fmt_date(p['date'])} Connect expects the shown hours to be "
                                                     f"right and suggests no change."))
        elif t == "ENTRY":
            who = "The shopping centre" if p.get("entered_by") == "SHOPPING_CENTRE" else "The business"
            out.append((ref, f"{who} entered {p['hours']} for {_fmt_date(p['date'])} ({p['day_key']}), on "
                             f"{_fmt_date(p['entered_at'])}."))
        elif t == "LEARNED_FACT":
            state = "current" if p.get("current") else "replaced by a newer answer"
            out.append((ref, f"The owner told Connect {p['value']} for {_fmt_date(p['date'])} ({p['day_key']}); this fact "
                             f"is {state}."))
        elif t == "DECISION":
            v = f" with {p['final_value']}" if p.get("final_value") else ""
            out.append((ref, f"The owner's decision for {_fmt_date(p['date'])}: {p['decision']}{v} (status {p['status']}, "
                             f"recorded {_fmt_date(p['at'])})."))
        elif t == "MALL":
            out.append((ref, "The business is inside a shopping centre."))
        elif t == "PEER_GROUP":
            top = (f"; the most common special hours were {p['top_special_hours']} "
                   f"({round(100 * p['top_special_share_of_entered'])}% of them)") if p.get("top_special_hours") else ""
            out.append((ref, f"Similar businesses ({p['group']}, {p['n_businesses']} businesses): in the {p['season']} "
                             f"season, {p['n_entered']} of them entered {p['day_key']}; "
                             f"{round(100 * p['share_closed_of_entered'])}% of those were closed{top}."))
        elif t == "REVIEW":
            out.append((ref, f"A {int(p['rating'])}-star review on {_fmt_date(p['date'])} said: \"{p['quote']}\""))
        elif t == "EVENT":
            out.append((ref, f"Nearby event ({p['km']} km): {p['name']}, {p['start'].replace('T', ' ')} to "
                             f"{p['end'][11:]}, about {p['expected_attendance']} visitors."))
    for e in sub["edges"]:
        if e["type"] == "LINKED_HOLIDAY":
            a, b = nodes[e["src"]]["props"], nodes[e["dst"]]["props"]
            out.append((f"model:linked:{a['day_key']}~{b['day_key']}",
                        f"In Connect's data, businesses closed on {a['name_en']} tend to be closed on {b['name_en']} "
                        f"too (correlation {e['weight']:.2f})."))
    return out


# ----------------------------------------------------------------------------- the check
_REF_RE = re.compile(r"\[([^\[\]]+)\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?\]])\s+(?=[A-ZÅÄÖØÆ0-9])|\n+")


def keep_cited_sentences(answer: str) -> str:
    """Drop every sentence that carries no citation (a fact must always be cited); very short connectives stay."""
    parts = [x.strip() for x in _SENT_SPLIT.split(answer or "") if x and x.strip()]
    kept = [x for x in parts if _REF_RE.search(x) or (len(x.split()) <= 3 and not re.search(r"\d", x))]
    while kept and not _REF_RE.search(kept[-1]):
        kept.pop()
    return " ".join(kept)


def check_answer(answer: str, facts: list[tuple[str, str]]) -> tuple[bool, list[str], str]:
    """(ok, citations, reason). Every citation must be a retrieved ref; every date, time range, time and number in
    the answer (outside the citations) must appear in the facts."""
    from cs.compose import extract_slots
    if not answer or answer.strip().upper().startswith("NO_ANSWER"):
        return False, [], "no answer"
    refs = _REF_RE.findall(answer)
    allowed = {r for r, _ in facts}
    if not refs:
        return False, [], "no citation"
    bad = [r for r in refs if r not in allowed]
    if bad:
        return False, refs, f"unknown citation {bad[0]}"
    text = _REF_RE.sub(" ", answer)
    src = " ".join(t for _, t in facts)
    o, p = extract_slots(src), extract_slots(text)
    endpoints = {t for iv in o.intervals for t in iv.split("-")}
    if not p.intervals <= o.intervals:
        return False, refs, f"time range not in facts: {sorted(p.intervals - o.intervals)}"
    if not p.dates <= o.dates:
        return False, refs, f"date not in facts: {sorted(p.dates - o.dates)}"
    if not p.times <= (o.times | endpoints):
        return False, refs, f"time not in facts: {sorted(p.times - (o.times | endpoints))}"
    if not p.numbers <= (o.numbers | {n for d in o.dates for n in d}):
        return False, refs, f"number not in facts: {sorted(p.numbers - o.numbers)}"
    return True, list(dict.fromkeys(refs)), "ok"


# ----------------------------------------------------------------------------- template answer (no LLM)
def template_answer(sub: dict, res: dict, lang: str) -> tuple[str, list[str]]:
    nodes = {n["id"]: n for n in sub["nodes"]}
    day_of = {}
    for e in sub["edges"]:
        if e["type"] == "FOR_DAY" and e["dst"] in nodes and nodes[e["dst"]]["type"] == "SPECIAL_DAY":
            day_of.setdefault(e["src"], nodes[e["dst"]])
    seeds = [nodes[s] for s in sub.get("seeds", []) if s in nodes]
    seed_ids = {s["id"] for s in seeds}
    sents, cites = [], []

    def add(text, ref):
        sents.append(f"{text} [{ref}]")
        cites.append(ref)

    by_type = lambda t: [n for n in sub["nodes"] if n["type"] == t and day_of.get(n["id"], {}).get("id") in seed_ids]  # noqa: E731
    if "MALL" in res["topics"]:
        for n in by_type("ENTRY"):
            if n["props"].get("entered_by") == "SHOPPING_CENTRE":
                d = day_of[n["id"]]
                add(T["mall"][lang].format(date=_fmt_date(d["props"]["date"]), day=_day_name(d, lang),
                                           hours=_fmt_hours(n["props"]["hours"], lang)), n["ref"])
    if "PEERS" in res["topics"]:
        seen = set()
        for n in sub["nodes"]:
            if n["type"] == "PEER_GROUP" and n["props"]["day_key"] in (res["day_keys"] or [s["props"]["day_key"] for s in seeds]) \
                    and n["props"]["day_key"] not in seen:
                p = n["props"]
                seen.add(p["day_key"])
                d = next((s for s in seeds if s["props"]["day_key"] == p["day_key"]), None)
                add(T["peers"][lang].format(group=p["group"], season=p["season"], n=p["n_entered"],
                                            pc=round(100 * p["share_closed_of_entered"]), day=_day_name(d, lang) or p["day_key"],
                                            top=_fmt_hours(p.get("top_special_hours"), lang)), n["ref"])
    if not sents:
        for n in by_type("LEARNED_FACT"):
            if n["props"].get("current"):
                d = day_of[n["id"]]
                add(T["fact"][lang].format(date=_fmt_date(d["props"]["date"]), day=_day_name(d, lang),
                                           hours=_fmt_hours(n["props"]["value"], lang)), n["ref"])
        for n in by_type("ENTRY"):
            if n["props"].get("entered_by") != "SHOPPING_CENTRE":
                d = day_of[n["id"]]
                add(T["entry"][lang].format(date=_fmt_date(d["props"]["date"]), day=_day_name(d, lang),
                                            hours=_fmt_hours(n["props"]["hours"], lang)), n["ref"])
    if not sents:
        for d in seeds:
            p = d["props"]
            if p.get("shown_now"):
                add(T["shown"][lang].format(date=_fmt_date(p["date"]), day=_day_name(d, lang),
                                            shown=_fmt_hours(p["shown_now"], lang)), p["shown_ref"])
                if p.get("suggestion_action") == "SUGGEST":
                    add(T["suggest"][lang].format(hours=_fmt_hours(p["suggestion_hours"], lang),
                                                  p=round(100 * p["suggestion_probability"])), p["suggestion_ref"])
    if not sents:
        return DONT_KNOW[lang], []
    return " ".join(sents[:6]), list(dict.fromkeys(cites[:6]))


# ----------------------------------------------------------------------------- the entry point
SYSTEM = ("You answer a business owner's question about their own listing for Fonecta Connect. Use ONLY the numbered "
          "facts given. After every sentence that states a fact, add the fact's reference in square brackets exactly as "
          "given, e.g. [holiday_hours_history:FI-00001:2025-04-18]; every sentence must contain at least one reference. "
          "Don't state that something is missing unless a fact says so. Never add a date, time or number that is not in the "
          "facts, and don't do arithmetic. If the facts don't answer the question, reply exactly NO_ANSWER. Answer in "
          "{lang}, in 1-3 short sentences, plain text, addressing the owner as 'you'.")


def ask(entity_id: str, question: str, lang: str = "fi", *, use_llm: bool | None = None, db=None, as_of: str | None = None
        ) -> dict:
    from cs import llm
    lang = lang if lang in LANGS else "en"
    res = resolve_question(entity_id, question, **({"as_of": as_of} if as_of else {}))
    sub = subgraph(entity_id, res["dates"], res["day_keys"], hops=2, db=db, topics=tuple(res["topics"]))
    facts = fact_lines(sub)
    by_ref = {}
    for n in sub["nodes"]:
        for r in (n["ref"], n["props"].get("shown_ref"), n["props"].get("suggestion_ref")):
            if r:
                by_ref[r] = n["id"]
    base = {"question_resolution": res, "retrieved": len(sub["nodes"])}
    if not res["dates"] and not res["topics"]:
        return {"answer": DONT_KNOW[lang], "citations": [], "used_nodes": [], "grounded": True, "mode": "fallback", **base}
    use = llm.claude_available() if use_llm is None else (use_llm and llm.claude_available())
    if use:
        ents = D.entities().set_index("entity_id")
        names = [ents.loc[entity_id, "name"]]
        mall = ents.loc[entity_id, "mall_id"]
        if isinstance(mall, str):
            names.append(ents.loc[mall, "name"])
        try:
            first = D.contacts().set_index("entity_id").loc[entity_id, "contact_first_name"]
            names.append(first)
        except KeyError:
            pass
        fact_text = "\n".join(f"- [{r}] {t}" for r, t in facts)
        prompt = llm.scrub(f"Facts:\n{fact_text}\n\nQuestion from the owner: {question}", names=names)
        raw = llm.claude_text(prompt, system=SYSTEM.format(lang=llm.LANG_NAMES.get(lang, "English")), max_tokens=700)
        out = keep_cited_sentences(raw or "")
        ok, cites, why = check_answer(out, facts)
        if ok:
            if out != (raw or "").strip():
                base["dropped_uncited"] = True
            return {"answer": out.strip(), "citations": cites, "used_nodes": [by_ref.get(c, c) for c in cites],
                    "grounded": True, "mode": "llm", **base}
        base["rejected_llm_answer"] = {"answer": raw, "reason": why}
        return {"answer": DONT_KNOW[lang], "citations": [], "used_nodes": [], "grounded": True, "mode": "fallback", **base}
    ans, cites = template_answer(sub, res, lang)
    mode = "template" if cites else "fallback"
    return {"answer": ans, "citations": cites, "used_nodes": [by_ref.get(c, c) for c in cites], "grounded": True,
            "mode": mode, **base}
