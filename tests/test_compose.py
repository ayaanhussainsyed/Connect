"""cs.compose: 4 languages × bases × single/multi-date, channel limits, button lengths,
the AI disclosure, and the slot check on our own templates (every date and time in a message
comes from the input Suggestions)."""
from __future__ import annotations

import inspect
from datetime import date

import pytest

from cs import compose as C
from cs.compose import extract_slots, fmt_date, fmt_hours, value_intervals
from cs.domain import Suggestion

LANGS = C.LANGS
BASES = {"own": "OWN_LAST_YEAR", "mall": "MALL", "peers": "PEERS"}
BIZ = {"name": "Kahvila Testi", "mall_name": "Kauppakeskus Sello"}
TODAY = date(2026, 11, 7)
DEADLINE = date(2026, 12, 16)
VIEWS = 140

SINGLE = [("2026-12-24", "07:30-13:00", "07:30-18:00")]
MULTI = [
    ("2026-12-23", "10:00-20:00", "10:00-18:00"),
    ("2026-12-24", "07:30-13:00", "07:30-18:00"),
    ("2026-12-25", "closed", "10:00-16:00"),
    ("2026-12-31", "10:00-14:00,17:00-22:00", "10:00-18:00"),
    ("2027-01-01", "closed", "10:00-16:00"),
    ("2027-01-05", "18:00-02:00", "18:00-23:00"),
    ("2027-01-06", "00:00-23:59", "09:00-17:00"),
]


def sug(d: str, value: str, shown: str, basis: str, action: str = "SUGGEST") -> Suggestion:
    return Suggestion(entity_id="FI-T0001", kind="HOLIDAY_HOURS", date=d, day_key=None, shown=shown,
                      action=action, value=value, basis=basis)


def many(n: int, basis: str = "OWN_LAST_YEAR") -> list[Suggestion]:
    base = date(2026, 11, 20)
    out = []
    for i in range(n):
        d = date.fromordinal(base.toordinal() + 4 * i)
        out.append(sug(d.isoformat(), "10:00-14:00,17:00-23:30" if i % 2 else "closed", "09:00-21:00", basis))
    return out


def allowed_numbers(views=VIEWS) -> set[int]:
    # value line, "2 years ago"/"14 days", SMS digit legend 1-4
    return {views, 2, 14, 1, 3, 4} if views is not None else {2, 14, 1, 3, 4}


def check_slots(msg: dict, sugs: list[Suggestion], deadline: date | None, views: int | None,
                question: dict | None = None) -> None:
    """Every date / time in the text equals an input value; every visible input is in the text."""
    text = msg["text"]
    slots = extract_slots(text)
    ok_iv = set()
    ok_dates = set()
    for s in sugs:
        ok_iv |= value_intervals(s.value) | value_intervals(s.shown)
        d = date.fromisoformat(s.date)
        ok_dates.add((d.day, d.month))
    if question:
        qd = date.fromisoformat(question["date"])
        ok_dates.add((qd.day, qd.month))
        ok_iv |= value_intervals(question.get("candidate"))
    if deadline:
        ok_dates.add((deadline.day, deadline.month))
    endpoints = {t for iv in ok_iv for t in iv.split("-")}
    assert slots.intervals <= ok_iv, (slots.intervals - ok_iv, text)
    assert slots.dates <= ok_dates, (slots.dates - ok_dates, text)
    assert slots.times <= endpoints, (slots.times, text)
    allowed = allowed_numbers(views) | {msg["parts"].get("elided") or 0}  # "+ N more dates"
    assert slots.numbers <= allowed, (slots.numbers, text)
    # completeness: every date line that is shown carries the right value
    visible = set(msg["parts"].get("dates_shown") or [])
    for s in sugs:
        if s.date not in visible:
            continue
        d = date.fromisoformat(s.date)
        assert (d.day, d.month) in slots.dates
        assert value_intervals(s.value) <= slots.intervals
        line = next(ln for ln in text.splitlines() if ln.startswith(f"• {fmt_date(d, msg['parts']['lang'])} "))
        assert fmt_hours(s.value, msg["parts"]["lang"]) in line, line


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------
def test_local_date_formats():
    d = date(2025, 12, 24)  # a Wednesday
    assert fmt_date(d, "fi") == "ke 24.12."
    assert fmt_date(d, "sv") == "ons 24/12"
    assert fmt_date(d, "nb") == "ons. 24.12."
    assert fmt_date(d, "en") == "Wed 24 Dec"


def test_local_hour_formats():
    assert fmt_hours("07:30-13:00", "fi") == "7.30–13"
    for lang in ("en", "sv", "nb"):
        assert fmt_hours("07:30-13:00", lang) == "07:30–13:00"
    assert fmt_hours("10:00-14:00,17:00-22:00", "fi") == "10–14, 17–22"
    assert fmt_hours("closed", "fi") == "kiinni"
    assert fmt_hours("closed", "sv") == "stängt"
    assert fmt_hours("closed", "nb") == "stengt"
    assert fmt_hours("00:00-23:59", "fi") == "0–24"


def test_slot_extractor_reads_every_format():
    for lang in LANGS:
        for v in ("07:30-13:00", "10:00-14:00,17:00-22:00", "18:00-02:00", "00:00-23:59"):
            assert extract_slots(fmt_hours(v, lang)).intervals == value_intervals(v), (lang, v)
        d = date(2027, 1, 6)
        assert extract_slots(fmt_date(d, lang)).dates == {(6, 1)}


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------
def test_every_button_fits_whatsapp():
    for lang, buttons in C.BUTTONS.items():
        for bid, title in buttons.items():
            assert len(title) <= C.BUTTON_TITLE_LIMIT, (lang, bid, title)
    assert len("Hyväksy + joka vuosi") == 20
    assert C.BUTTONS["fi"]["APPROVE_EVERY_YEAR"] == "Hyväksy + joka vuosi"
    assert C.BUTTONS["sv"]["APPROVE_EVERY_YEAR"] == "Godkänn + varje år"
    assert C.BUTTONS["nb"]["APPROVE_EVERY_YEAR"] == "Godkjenn + hvert år"
    assert C.BUTTONS["en"]["APPROVE_EVERY_YEAR"] == "Approve + every year"


# ---------------------------------------------------------------------------
# The card: 4 languages × bases × single/multi × channel
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["WHATSAPP", "SMS", "EMAIL"])
@pytest.mark.parametrize("multi", [False, True], ids=["single", "multi"])
@pytest.mark.parametrize("basis", list(BASES.values()), ids=list(BASES))
@pytest.mark.parametrize("lang", LANGS)
def test_card(lang, basis, multi, channel):
    sugs = [sug(d, v, s, basis) for d, v, s in (MULTI if multi else SINGLE)]
    msg = C.compose_card_message(BIZ, sugs, lang, channel, True, None, DEADLINE, VIEWS,
                                 value_line_day="XMAS_EVE", today=TODAY)
    text, parts = msg["text"], msg["parts"]
    assert text and parts["lang"] == lang
    for b in msg["buttons"]:
        assert len(b["title"]) <= C.BUTTON_TITLE_LIMIT
    if channel == "WHATSAPP":
        assert len(text) <= C.WHATSAPP_BODY_LIMIT
        assert 1 <= len(msg["buttons"]) <= C.WHATSAPP_MAX_BUTTONS
        assert [b["id"] for b in msg["buttons"]] == ["APPROVE", "APPROVE_EVERY_YEAR", "EDIT"]
    elif channel == "SMS":
        assert len(text) <= C.SMS_LIMIT
        assert msg["buttons"] == []
        assert "1 = " in text and "4 = " in text
        assert parts["reply_digits"]["2"] == "APPROVE_EVERY_YEAR"
    else:
        assert [b["id"] for b in msg["buttons"]] == list(C.CARD_BUTTON_IDS)
    assert C.T[lang]["hi"].format(name=BIZ["name"]) in text
    check_slots(msg, sugs, DEADLINE, VIEWS)
    reasons_shown = "no_reasons" not in parts["compaction"]
    if basis == "MALL" and reasons_shown:
        assert BIZ["mall_name"] in text
    if basis == "PEERS":
        first = sugs[0]
        line = next(ln for ln in text.splitlines()
                    if ln.startswith(f"• {fmt_date(date.fromisoformat(first.date), lang)} "))
        assert " ?" in line  # peer claims are phrased as a question
    if basis == "OWN_LAST_YEAR" and reasons_shown and not multi:
        assert C.REASONS[lang]["OWN_LAST_YEAR"] in text


@pytest.mark.parametrize("lang", LANGS)
def test_disclosure_only_on_first_contact(lang):
    sugs = [sug(d, v, s, "OWN_LAST_YEAR") for d, v, s in SINGLE]
    disclosure = C.T[lang]["disclosure"].format(brand=C.DEFAULT_BRAND)
    first = C.compose_card_message(BIZ, sugs, lang, "WHATSAPP", True, None, DEADLINE, None, today=TODAY)
    later = C.compose_card_message(BIZ, sugs, lang, "WHATSAPP", False, None, DEADLINE, None, today=TODAY)
    assert first["text"].startswith(disclosure)
    assert disclosure not in later["text"]
    assert "Connect" in disclosure


@pytest.mark.parametrize("channel,limit", [("WHATSAPP", C.WHATSAPP_BODY_LIMIT), ("SMS", C.SMS_LIMIT)])
@pytest.mark.parametrize("lang", LANGS)
def test_worst_case_fits_the_channel(lang, channel, limit):
    sugs = many(16, "PEERS")
    q = {"date": "2026-12-26", "candidate": "10:00-16:00"}
    msg = C.compose_card_message({"name": "Ravintola Erittäin Pitkä Nimi Oy", "mall_name": "Kauppakeskus Iso Omena"},
                                 sugs, lang, channel, True, q, DEADLINE, 1234, value_line_day="XMAS_EVE",
                                 link="https://connect.example/c/eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop", today=TODAY)
    assert len(msg["text"]) <= limit
    assert msg["parts"]["elided"] >= 0
    check_slots(msg, sugs, DEADLINE, 1234, question=q)


def test_value_line_left_out_when_none():
    sugs = [sug(d, v, s, "OWN_LAST_YEAR") for d, v, s in SINGLE]
    with_v = C.compose_card_message(BIZ, sugs, "fi", "WHATSAPP", True, None, DEADLINE, 140,
                                    value_line_day="XMAS_EVE", today=TODAY)
    without = C.compose_card_message(BIZ, sugs, "fi", "WHATSAPP", True, None, DEADLINE, None, today=TODAY)
    assert "Viime jouluaattona ~140 ihmistä haki teitä." in with_v["text"]
    assert "ihmistä haki" not in without["text"] and without["parts"]["value"] is None


def test_deadline_line_and_past_deadline():
    sugs = [sug(d, v, s, "OWN_LAST_YEAR") for d, v, s in SINGLE]
    m = C.compose_card_message(BIZ, sugs, "en", "WHATSAPP", True, None, date(2026, 12, 17), None, today=TODAY)
    assert "Please answer by Thu 17 Dec so AI assistants show it in time." in m["text"]
    past = C.compose_card_message(BIZ, sugs, "en", "WHATSAPP", True, None, date(2026, 11, 1), None, today=TODAY)
    assert "answer soon" in past["text"] and "1 Nov" not in past["text"]


def test_shown_line_says_what_google_shows():
    sugs = [sug("2026-12-24", "07:30-13:00", "07:30-18:00", "OWN_LAST_YEAR"),
            sug("2026-12-25", "closed", "10:00-16:00", "OWN_LAST_YEAR")]
    m = C.compose_card_message(BIZ, sugs, "fi", "WHATSAPP", True, None, DEADLINE, None, today=TODAY)
    assert "Jos et tee mitään, Google näyttää: to 24.12. 7.30–18 · pe 25.12. 10–16" in m["text"]


def test_flag_and_no_action_never_become_a_message():
    flagged = [sug("2026-12-24", None, "10:00-16:00", "REVIEW", action="FLAG"),
               sug("2026-12-25", None, "10:00-16:00", "MODEL", action="NO_ACTION")]
    with pytest.raises(ValueError):
        C.compose_card_message(BIZ, flagged, "fi", "WHATSAPP", True, None, DEADLINE, None, today=TODAY)
    mixed = flagged + [sug("2026-12-26", "closed", "10:00-16:00", "OWN_LAST_YEAR")]
    m = C.compose_card_message(BIZ, mixed, "fi", "WHATSAPP", True, None, DEADLINE, None, today=TODAY)
    assert m["parts"]["dates"] == ["2026-12-26"]


@pytest.mark.parametrize("lang", LANGS)
def test_question_in_card_and_alone(lang):
    sugs = [sug(d, v, s, "OWN_LAST_YEAR") for d, v, s in SINGLE]
    q = {"date": "2026-12-26", "candidate": "10:00-16:00", "regular": "09:00-17:00"}
    m = C.compose_card_message(BIZ, sugs, lang, "WHATSAPP", True, q, DEADLINE, None, today=TODAY)
    assert C.T[lang]["q_marker"] in m["text"]
    qm = C.compose_question(q, lang, "WHATSAPP")
    assert fmt_date(date(2026, 12, 26), lang) in qm["text"]
    assert [b["id"] for b in qm["buttons"]] == ["Q_CLOSED", "Q_REGULAR", "Q_OTHER"]
    assert all(len(b["title"]) <= 20 for b in qm["buttons"])
    assert "1 = " in qm["text"] and "3 = " in qm["text"]
    sms = C.compose_question(q, lang, "SMS")
    assert sms["buttons"] == [] and sms["parts"]["reply_digits"]["1"] == "Q_CLOSED"
    habit = C.compose_question({"kind": "HABIT"}, lang, "WHATSAPP")
    assert [b["id"] for b in habit["buttons"]] == ["H_YES", "H_NO", "H_VARIES"]


# ---------------------------------------------------------------------------
# Confirmation, follow-ups, emergency, autopilot
# ---------------------------------------------------------------------------
WHERE = [
    {"publisher": "Google (Search & Maps)", "publisher_id": "GOOGLEMYBUSINESS", "status": "LIVE", "typical_seconds": 120},
    {"publisher": "Finder.fi / Fonecta.fi", "publisher_id": "FONECTA_FINDER", "status": "LIVE", "typical_seconds": 600},
    {"publisherName": "Fonecta Caller", "publisherId": "FONECTA_CALLER", "status": "LIVE", "typicalUpdateSpeedSeconds": 3600},
    {"publisher": "Apple Maps", "publisher_id": "APPLE", "status": "LIVE", "typical_seconds": 172800},
    {"publisher": "AI assistants (via Bing/Apple data)", "publisher_id": "CHATGPT_SEARCH", "status": "LIVE", "typical_seconds": 604800},
    {"publisher": "Facebook", "publisher_id": "FACEBOOK", "status": "OPTED_OUT", "typical_seconds": 600},
]


@pytest.mark.parametrize("lang", LANGS)
def test_confirmation(lang):
    m = C.compose_confirmation(BIZ, {"2026-12-24": "07:30-13:00", "2026-12-25": "closed"}, WHERE, lang,
                               "WHATSAPP", every_year=True)
    assert "Google ~2 min" in m["text"]
    assert C.STATUS_TEXT[lang]["OPTED_OUT"] in m["text"]
    assert C.AI_ASSISTANTS[lang] in m["text"]
    assert [b["id"] for b in m["buttons"]] == ["UNDO"]
    assert C.T[lang]["scope_granted"] in m["text"]
    slots = extract_slots(m["text"])
    assert slots.intervals == {"07:30-13:00"} and slots.dates == {(24, 12), (25, 12)}


def test_confirmation_accepts_confirm_module_rows():
    confirm = pytest.importorskip("cs.confirm")
    rows = confirm.where_it_shows("FI-D0001")
    m = C.compose_confirmation(BIZ, {"2026-12-24": "07:30-13:00"}, rows, "fi")
    assert m["text"].startswith("✓ Tallennettu: to 24.12. 7.30–13.")
    assert "Google ~2 min" in m["text"] and "Facebook: ei yhdistetty" in m["text"]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("decision", C.FOLLOWUPS)
def test_followups(lang, decision):
    m = C.compose_followup(decision, lang, values={"2026-12-26": "closed"} if decision in ("EDIT", "REMINDER") else None,
                           deadline=DEADLINE, today=TODAY, dates=["2026-12-24", "2026-12-26"])
    assert m["text"]
    assert all(len(b["title"]) <= 20 for b in m["buttons"])
    if decision == "EDIT":
        assert [b["id"] for b in m["buttons"]] == ["YES", "NO"]  # read back before saving
        assert fmt_date(date(2026, 12, 26), lang) in m["text"]
    if decision == "REMINDER":
        assert fmt_date(DEADLINE, lang) in m["text"]
    if decision == "APPROVE_EVERY_YEAR":
        assert "14" in m["text"]


def test_emergency_has_no_reason():
    sig = inspect.signature(C.compose_emergency)
    assert not any(n in sig.parameters for n in ("reason", "why", "note", "comment"))
    with pytest.raises(TypeError):
        C.compose_emergency(BIZ, "2026-12-12", "fi", reason="olen kipeä")  # type: ignore[call-arg]
    m = C.compose_emergency(BIZ, date(2026, 12, 12), "fi", today=date(2026, 12, 12), where_it_shows=WHERE)
    assert m["text"].startswith("✓ Tallennettu: kiinni tänään la 12.12.")
    assert "Tavalliset ajat palaavat huomenna." in m["text"]
    assert "Google ~2 min" in m["text"] and "Apple Maps ~2 vrk" in m["text"]
    assert "reason" not in m["parts"]
    assert [b["id"] for b in m["buttons"]] == ["UNDO"]
    en = C.compose_emergency(BIZ, date(2026, 12, 12), "en", today=date(2026, 12, 12), where_it_shows=WHERE)
    assert "closed today, Sat 12 Dec" in en["text"] and "Normal hours return tomorrow." in en["text"]
    rng = C.compose_emergency(BIZ, date(2026, 12, 12), "en", until=date(2026, 12, 14), today=date(2026, 12, 12))
    assert "Normal hours return Tue 15 Dec." in rng["text"]


@pytest.mark.parametrize("lang", LANGS)
def test_autopilot_note(lang):
    m = C.compose_autopilot_note(BIZ, {"2026-12-24": "closed", "2026-12-25": "closed"}, lang,
                                 where_it_shows=WHERE, short_notice=True)
    assert [b["id"] for b in m["buttons"]] == ["UNDO", "STOP"]
    assert C.T[lang]["auto_short"] in m["text"]
    assert extract_slots(m["text"]).dates == {(24, 12), (25, 12)}


def test_reason_sentences_carry_the_value():
    s = sug("2026-12-24", "07:30-13:00", "07:30-18:00", "OWN_LAST_YEAR")
    for lang in LANGS:
        r = C.reason_sentence(s, lang)
        assert extract_slots(r).intervals == {"07:30-13:00"}
    mall = sug("2026-12-24", "10:00-15:00", "10:00-20:00", "MALL")
    assert "Kauppakeskus Sello" in C.reason_sentence(mall, "fi", "Kauppakeskus Sello")


# ---------------------------------------------------------------------------
# WhatsApp template parameter
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lang", LANGS)
def test_template_param_is_one_line_with_every_fact(lang):
    sugs = [sug(d, v, s, "OWN_LAST_YEAR") for d, v, s in MULTI]
    msg = C.compose_card_message(BIZ, sugs, lang, "WHATSAPP", True, None, DEADLINE, VIEWS, today=TODAY)
    params = msg["parts"]["template_params"]
    assert len(params) == 1
    p = params[0]
    assert "\n" not in p and "\t" not in p and "  " not in p and len(p) <= C.TEMPLATE_PARAM_LIMIT
    assert not p.endswith(".")  # the template adds the full stop after {{1}}
    slots = extract_slots(p)
    want_iv = set().union(*(value_intervals(s.value) for s in sugs))
    want_dates = {(date.fromisoformat(s.date).day, date.fromisoformat(s.date).month) for s in sugs}
    assert slots.intervals == want_iv
    assert slots.dates == want_dates | {(DEADLINE.day, DEADLINE.month)}
    assert fmt_date(DEADLINE, lang).rstrip(".") in p


def test_template_param_fits_with_many_dates():
    sugs = many(60, "PEERS")
    q = {"date": "2026-12-26", "candidate": "10:00-16:00"}
    for lang in LANGS:
        p = C.compose_template_params(BIZ, sugs, lang, DEADLINE, q, today=TODAY)[0]
        assert len(p) <= C.TEMPLATE_PARAM_LIMIT and "\n" not in p
        assert C.T[lang]["more"].split("{n}")[0].strip() in p  # "+ N more dates"
        assert fmt_date(DEADLINE, lang).rstrip(".") in p        # the deadline is never cut


def test_template_param_example_matches_spec():
    sugs = [sug("2026-12-24", "07:30-13:00", "07:30-18:00", "OWN_LAST_YEAR"),
            sug("2026-12-25", "closed", "07:30-18:00", "OWN_LAST_YEAR"),
            sug("2027-01-01", "closed", "07:30-18:00", "OWN_LAST_YEAR"),
            sug("2027-01-06", "10:00-16:00", "07:30-18:00", "OWN_2_SEASONS")]
    p = C.compose_template_params(BIZ, sugs, "fi", date(2026, 12, 17), today=TODAY)[0]
    assert p == "to 24.12. 7.30–13 · pe 25.12. kiinni · pe 1.1. kiinni · ke 6.1. 10–16 · vastaa viimeistään to 17.12"


def test_sanitize_template_param():
    s = C.sanitize_template_param("a\n\nb\t c      d\r\n")
    assert s == "a · b c d"
    assert C.sanitize_template_param("") == "-"
    assert C.sanitize_template_param("x" * 1000, 900).endswith("…")
    assert len(C.sanitize_template_param("x" * 1000, 900)) == 900
