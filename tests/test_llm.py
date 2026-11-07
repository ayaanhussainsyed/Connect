"""cs.llm: slot_check, the offline regex parser, validation of the model's JSON, privacy scrub,
and one live network test of parse_reply."""
from __future__ import annotations

from datetime import date

import pytest

from cs import llm
from cs.llm import parse_reply, parse_reply_regex, slot_check, validate_llm_result

XMAS = ["2026-12-24", "2026-12-25", "2026-12-26"]
TODAY = date(2026, 11, 7)


# ---------------------------------------------------------------------------
# slot_check
# ---------------------------------------------------------------------------
def test_slot_check_same_facts_pass():
    o = "Merkitsitte viime vuonna samalle päivälle samat ajat: 7.30–13."
    assert slot_check(o, o)
    assert slot_check(o, "Viime vuonna merkitsitte juuri nämä ajat samalle päivälle: 7.30–13.")
    assert slot_check(o, "Viime vuonna teillä oli auki 07:30–13:00 samana päivänä.")  # format only


def test_slot_check_rejects_changed_or_added_facts():
    o = "Last year you entered the same hours for Wed 24 Dec: 07:30–13:00."
    assert not slot_check(o, "Last year you entered 07:30–14:00 for Wed 24 Dec.")      # time changed
    assert not slot_check(o, "Last year you entered 07:30–13:00 for Wed 25 Dec.")      # date changed
    assert not slot_check(o, "For 3 years you entered 07:30–13:00 on Wed 24 Dec.")     # number added
    assert not slot_check(o, "Last year you entered the same hours: 07:30–13:00.")     # date dropped
    assert not slot_check(o, "Last year you were closed on Wed 24 Dec: 07:30–13:00.")  # closed added
    assert not slot_check(o, "")


def test_slot_check_keeps_closed_statement():
    o = "Useimmat vastaavat yritykset ovat silloin kiinni. Pitääkö tämä paikkansa?"
    assert slot_check(o, "Useimmat vastaavat ovat silloin kiinni – onko teillä samoin?")
    assert not slot_check(o, "Useimmat vastaavat ovat silloin auki – onko teillä samoin?")


def test_polish_reason_without_llm_keeps_the_original(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "0")
    s = "Merkitsitte viime vuonna samalle päivälle samat ajat: 7.30–13."
    assert not llm.llm_enabled()
    assert llm.polish_reason(s, "fi") == s


def test_polish_reason_rejects_a_bad_rewrite(monkeypatch):
    monkeypatch.setattr(llm, "claude_available", lambda: True)
    monkeypatch.setattr(llm, "claude_text", lambda *a, **k: "Viime vuonna olitte auki 8–13.")
    s = "Merkitsitte viime vuonna samalle päivälle samat ajat: 7.30–13."
    assert llm.polish_reason(s, "fi", use_llm=True) == s
    monkeypatch.setattr(llm, "claude_text", lambda *a, **k: "Viime vuonna [N1] ilmoitti ajat 10–15.")
    m = "Kauppakeskus Sello on ilmoittanut tälle päivälle ajat: 10–15."
    assert llm.polish_reason(m, "fi", protect=["Kauppakeskus Sello"], use_llm=True) == \
        "Viime vuonna Kauppakeskus Sello ilmoitti ajat 10–15."


# ---------------------------------------------------------------------------
# The regex fallback parser
# ---------------------------------------------------------------------------
def rx(text, dates=XMAS, **kw):
    return parse_reply_regex(text, kw.pop("lang", "fi"), dates, today=TODAY, **kw)


def test_regex_digits_and_words():
    assert rx("1")["action"] == "APPROVE"
    r2 = rx("2")
    assert r2["action"] == "APPROVE_EVERY_YEAR" and r2["every_year"] is True
    assert rx("3")["action"] == "EDIT"
    assert rx("4")["action"] == "DECLINE"
    assert rx("OK!")["action"] == "APPROVE"
    assert rx("Godkänn", lang="sv")["action"] == "APPROVE"
    assert rx("ok, joka vuosi")["action"] == "APPROVE_EVERY_YEAR"
    assert rx("ei kiitos")["action"] == "DECLINE"
    assert rx("Nei takk", lang="nb")["action"] == "DECLINE"
    assert rx("lopeta")["action"] == "STOP"


def test_regex_closed_words_and_holiday_names():
    r = rx("kiinni tapaninpäivänä, muuten ok")
    assert r["action"] == "EDIT" and r["dates"] == {"2026-12-26": "closed"} and r["approve_others"] is True
    assert rx("julafton stängt, annars ok", lang="sv")["dates"] == {"2026-12-24": "closed"}
    assert rx("stengt første juledag", lang="nb")["dates"] == {"2026-12-25": "closed"}
    assert rx("closed on Boxing Day", lang="en")["dates"] == {"2026-12-26": "closed"}


def test_regex_explicit_dates_and_ranges():
    assert rx("24.12: 9.30–13")["dates"] == {"2026-12-24": "09:30-13:00"}
    assert rx("24.12. 10-14, 25.12. kiinni")["dates"] == {"2026-12-24": "10:00-14:00", "2026-12-25": "closed"}
    assert rx("26/12 10:00-16:00", lang="sv")["dates"] == {"2026-12-26": "10:00-16:00"}
    assert rx("joulupäivänä kiinni ja tapaninpäivänä 12-16")["dates"] == {
        "2026-12-25": "closed", "2026-12-26": "12:00-16:00"}
    assert rx("26.12. tavalliset ajat")["dates"] == {"2026-12-26": "regular"}
    # one-date card: the state alone is enough
    assert rx("kiinni", dates=["2026-12-26"])["dates"] == {"2026-12-26": "closed"}


def test_regex_emergency():
    r = rx("kiinni tänään")
    assert r["action"] == "EMERGENCY"
    assert r["emergency"] == {"from": "2026-11-07", "to": "2026-11-07", "hours": "closed",
                              "until_further_notice": False}
    assert rx("stängt i morgon", lang="sv")["emergency"]["from"] == "2026-11-08"
    assert rx("closed until further notice", lang="en")["emergency"]["until_further_notice"] is True
    assert rx("auki tänään 12-16")["emergency"]["hours"] == "12:00-16:00"
    assert "reason" not in rx("olen kipeä, kiinni tänään")["emergency"]


def test_regex_returns_none_when_in_doubt():
    assert rx("kiinni") is None                         # which day?
    assert rx("kiinni 27.12.") is None                  # not on the card
    assert rx("suljemme klo 14 jouluaattona") is None   # needs the opening time
    assert rx("jotain ihan muuta") is None
    assert rx("") is None


def test_regex_question_and_readback_modes():
    q = rx("1", dates=["2026-12-26"], expect="question")
    assert q["action"] == "ANSWER" and q["dates"] == {"2026-12-26": "closed"}
    assert rx("2", dates=["2026-12-26"], expect="question")["dates"] == {"2026-12-26": "regular"}
    assert rx("3", dates=["2026-12-26"], expect="question")["answer"] == "other"
    assert rx("1", expect="readback")["action"] == "APPROVE"
    assert rx("2", expect="readback")["action"] == "DECLINE"


def test_parse_reply_offline_equals_regex(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "0")
    r = parse_reply("kiinni tapaninpäivänä, muuten ok", "fi", XMAS, today=TODAY)
    assert r is not None and r["source"] == "regex"
    assert r["dates"] == {"2026-12-26": "closed"}
    assert parse_reply("1", "fi", XMAS, today=TODAY)["action"] == "APPROVE"


def test_parse_reply_none_when_llm_and_regex_disagree(monkeypatch):
    monkeypatch.setattr(llm, "claude_available", lambda: True)
    monkeypatch.setattr(llm, "claude_text", lambda *a, **k: (
        '<analysis>x</analysis><json>{"action": "EDIT", "dates": {"2026-12-26": "10:00-14:00"}, '
        '"approve_others": true, "every_year": null, "emergency": null, "confidence": 0.9}</json>'))
    assert parse_reply("kiinni tapaninpäivänä, muuten ok", "fi", XMAS, today=TODAY, use_llm=True) is None


def test_parse_reply_uses_regex_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm, "claude_available", lambda: True)
    monkeypatch.setattr(llm, "claude_text", lambda *a, **k: None)  # timeout / error
    r = parse_reply("kiinni tapaninpäivänä, muuten ok", "fi", XMAS, today=TODAY, use_llm=True)
    assert r["source"] == "regex" and r["dates"] == {"2026-12-26": "closed"}


# ---------------------------------------------------------------------------
# Validating the model's JSON
# ---------------------------------------------------------------------------
def test_validate_llm_result():
    ok = {"action": "EDIT", "dates": {"2026-12-24": "9.30-13"}, "approve_others": True, "every_year": None,
          "emergency": None, "confidence": 0.9}
    v = validate_llm_result(ok, XMAS, TODAY)
    assert v["dates"] == {"2026-12-24": "09:30-13:00"} and v["source"] == "llm"
    assert validate_llm_result({**ok, "dates": {"2026-12-27": "closed"}}, XMAS, TODAY) is None  # not on card
    assert validate_llm_result({**ok, "dates": {"2026-12-24": "25:00-13:00"}}, XMAS, TODAY) is None  # invalid hours
    assert validate_llm_result({**ok, "confidence": 0.3}, XMAS, TODAY) is None
    assert validate_llm_result({**ok, "action": "UNCLEAR"}, XMAS, TODAY) is None
    assert validate_llm_result({**ok, "every_year": "maybe"}, XMAS, TODAY) is None
    em = {"action": "EMERGENCY", "dates": {}, "approve_others": None, "every_year": None, "confidence": 0.9,
          "emergency": {"from": "2026-11-07", "to": None, "hours": "closed", "until_further_notice": False,
                        "reason": "sick"}}
    v = validate_llm_result(em, XMAS, TODAY)
    assert v["emergency"] == {"from": "2026-11-07", "to": "2026-11-07", "hours": "closed",
                              "until_further_notice": False}
    assert validate_llm_result({**em, "emergency": {**em["emergency"], "from": "2027-06-01"}}, XMAS, TODAY) is None


def test_extract_json():
    assert llm._extract_json('<analysis>a</analysis>\n<json>{"a": 1}</json>') == {"a": 1}
    assert llm._extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert llm._extract_json("no json") is None


def test_scrub_removes_personal_data_but_keeps_hours():
    t = "Terveisin Aleksi, aleksi@example.com, +358 40 123 4567. Kiinni 26.12., muuten 10-14 17-22"
    s = llm.scrub(t, names=["Aleksi"])
    assert "Aleksi" not in s and "aleksi@" not in s and "123 4567" not in s
    assert "26.12." in s and "10-14 17-22" in s


# ---------------------------------------------------------------------------
# Live (one call)
# ---------------------------------------------------------------------------
@pytest.mark.network
def test_parse_reply_live_boxing_day_closed():
    if not llm.claude_available():
        pytest.skip("Claude is not configured (or LLM_ENABLED=0)")
    r = parse_reply("kiinni tapaninpäivänä, muuten ok", "fi", XMAS, today=TODAY, use_llm=True)
    assert r is not None
    assert r["source"] == "llm"
    assert r["dates"].get("2026-12-26") == "closed"
    assert "2026-12-24" not in r["dates"] and "2026-12-25" not in r["dates"]
    assert r["approve_others"] is True
