"""A13 – cs.profile: drift (weekly hours that changed) and replies to closed-door reviews."""
from __future__ import annotations

import json
from datetime import date

import pytest

from cs import compose as C
from cs import data
from cs.compose import extract_slots, value_intervals
from cs.profile import days, drift, review_reply as R

pytestmark = pytest.mark.skipif(drift._table() is None, reason="drift_2026.parquet not built yet")


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------
def test_drift_suggestion_for_se_d0016():
    s = drift.drift_suggestion("SE-D0016")
    assert s is not None and s.kind == "REGULAR_HOURS" and s.action == "SUGGEST"
    assert s.probability > 0.9 and s.basis == "REVIEW"
    proposed, published = drift.week_of(s.value), drift.week_of(s.shown)
    assert proposed == {**{d: "11:00-22:00" for d in ("monday", "tuesday", "wednesday", "thursday", "friday",
                                                      "saturday")}, "sunday": "closed"}
    assert published["monday"] == "11:00-23:00" and published["sunday"] == "12:00-21:00"
    reviews = [e for e in s.evidence if e.source == "REVIEW"]
    assert reviews and reviews[0].detail == "Numera öppet bara mån–lör 11–22."
    assert reviews[0].ref.startswith("reviews:")


def test_drift_only_for_suggest_rows():
    assert drift.drift_suggestion("FI-D0001") is None
    assert drift.drift_suggestion("NO-SUCH") is None
    ids = drift.suggested_entities()
    assert "SE-D0016" in ids and len(ids) == 46
    for eid in ids:
        s = drift.drift_suggestion(eid)
        assert s is not None and drift.week_of(s.value) != drift.week_of(s.shown)


def test_changed_days_groups_consecutive_runs():
    pub = {d: "11:00-23:00" for d in drift.DAYS}
    pub["sunday"] = "12:00-21:00"
    new = {d: "11:00-22:00" for d in drift.DAYS}
    new["wednesday"] = "11:00-23:00"
    new["sunday"] = "closed"
    runs = drift.changed_days(pub, new)
    assert runs == [(["monday", "tuesday"], "11:00-23:00", "11:00-22:00"),
                    (["thursday", "friday", "saturday"], "11:00-23:00", "11:00-22:00"),
                    (["sunday"], "12:00-21:00", "closed")]


@pytest.mark.parametrize("lang", C.LANGS)
@pytest.mark.parametrize("channel", ["WHATSAPP", "SMS", "EMAIL"])
def test_drift_message_slots_match_the_data(lang, channel):
    for eid in drift.suggested_entities():
        s = drift.drift_suggestion(eid)
        m = drift.compose_drift_message({"name": "Testi"}, s, lang, channel)
        pub, new = drift.week_of(s.shown), drift.week_of(s.value)
        reason = m["parts"]["reason"] or ""
        body = m["text"].replace(reason, "")  # the quote is the customer's words (checked below)
        slots = extract_slots(body)
        allowed = set().union(*(value_intervals(v) for v in list(pub.values()) + list(new.values())))
        assert slots.intervals <= allowed, (eid, slots.intervals - allowed)
        assert not slots.dates
        for days_, a, b in drift.changed_days(pub, new):
            line = next(ln for ln in m["text"].splitlines() if ln.startswith(f"• {drift._day_span(days_, lang)} "))
            assert C.fmt_hours(a, lang) in line and C.fmt_hours(b, lang) in line
        if reason:  # the quote is verbatim review text from the evidence (shortened at most)
            quoted = reason.split('"')[1] if '"' in reason else ""
            assert not quoted or any(e.detail.startswith(quoted.rstrip(" …")) for e in s.evidence)
        assert all(len(b["title"]) <= 20 for b in m["buttons"])
        if channel == "WHATSAPP":
            assert len(m["text"]) <= C.WHATSAPP_BODY_LIMIT
            assert [b["id"] for b in m["buttons"]] == ["APPROVE", "EDIT", "DECLINE"]
        if channel == "SMS":
            assert m["buttons"] == [] and "3 = " in m["text"]


def test_drift_message_example_sv():
    m = drift.compose_drift_message({"name": "Restaurang Fyren"}, drift.drift_suggestion("SE-D0016"), "sv")
    assert "• mån–lör 11:00–23:00 → 11:00–22:00" in m["text"]
    assert "• sön 12:00–21:00 → stängt" in m["text"]
    assert '"Numera öppet bara mån–lör 11–22."' in m["text"]


class FakeYext:
    def __init__(self, live_week: dict, with_setter: bool = True):
        from cs.hours import week_strs_to_yext
        self.entity = {"hours": week_strs_to_yext([live_week[d] for d in drift.DAYS])}
        self.calls = []
        if with_setter:
            self.set_regular_hours = self._set

    def get_entity(self, entity_id):
        return self.entity

    def _set(self, entity_id, week, expected_current_week=None, decision_id=None):
        self.calls.append((entity_id, week, decision_id))
        self.expected = expected_current_week

        class Rec:
            readback_ok = True
            note = "applied weekly hours"
        return Rec()


def test_apply_drift_paths():
    s = drift.drift_suggestion("SE-D0016")
    pub, new = drift.week_of(s.shown), drift.week_of(s.value)
    ok = FakeYext(pub)
    r = drift.apply_drift("SE-D0016", s, client=ok, decision_id="dec-1", audit=False)
    assert r["status"] == "applied" and ok.calls == [("SE-D0016", new, "dec-1")]
    assert ok.expected == pub  # the Yext client gets the week we showed, for its owner-wins check
    missing = drift.apply_drift("SE-D0016", s, client=FakeYext(pub, with_setter=False), audit=False)
    assert missing["status"] == "unavailable" and "would_send" in missing
    assert missing["would_send"]["hours"]["sunday"] == {"isClosed": True}
    changed = dict(pub, monday="09:00-17:00")
    owner = FakeYext(changed)
    r = drift.apply_drift("SE-D0016", s, client=owner, audit=False)
    assert r["status"] == "cancelled" and owner.calls == []  # the owner changed it: owner wins


# ---------------------------------------------------------------------------
# Review replies
# ---------------------------------------------------------------------------
def test_holiday_names_in_reviews():
    assert days.detect_day_key("Det stod öppet 11–23 på nätet men det var stängt på långfredagen.") == "GOOD_FRIDAY"
    assert days.detect_day_key("Tultiin pääsiäislauantaina paikan päälle") == "EASTER_SATURDAY"
    assert days.detect_day_key("stängt på annandag påsk") == "EASTER_MONDAY"
    assert days.detect_day_key("kiinni toisena pääsiäispäivänä") == "EASTER_MONDAY"
    assert days.detect_day_key("Harmi, välipäivinä ovet kiinni.") == "INTERDAY"
    assert days.detect_day_key("Vi kom hit på lillejulafton") == "DEC23"
    assert days.detect_day_key("stengt 17. mai") == "NATIONAL_DAY"
    assert days.detect_day_key("closed on New Year's Eve") == "NYE"
    assert days.detect_day_key("Great coffee") is None


def test_complaint_rules():
    yes = [{"content": "Netissä luki auki 10–18, mutta kiirastorstaina oli suljettu.", "rating": 1.0},
           {"content": "Fel öppettider på midsommardagen. Stod framför en stängd dörr.", "rating": 1.0},
           {"content": "Online it said open, but it was closed on Good Friday.", "rating": 2.0},
           {"content": "Aukioloajat ovat muuttuneet, netissä on vanhat tiedot.", "rating": 3.0}]
    no = [{"content": "Kiitos että olitte auki helluntaina, pelastitte päivän.", "rating": 5.0},
          {"content": "Tack för att ni hade öppet på midsommarafton.", "rating": 5.0},
          {"content": "Tilaus meni väärin kahdesti. Ruoka oli haaleaa.", "rating": 2.0},
          {"content": "Great coffee, closed kitchen though was fine", "rating": 5.0},
          {"content": "", "rating": 1.0}]
    assert all(R.is_complaint(r) for r in yes)
    assert not any(R.is_complaint(r) for r in no)


def test_only_unanswered_complaints():
    raw = {str(r["id"]): r for r in data.reviews(R.DEFAULT_AS_OF)}
    for eid in ("NO-D0009", "SE-D0016", "FI-D0007", "FI-D0001"):
        for c in R.unanswered_complaints(eid):
            r = raw[c["review_id"]]
            assert r["locationId"] == eid and not r["comments"] and R.is_complaint(r)
            assert "authorName" not in c
    # NO-D0009: "1. mai stengte …" has an owner reply, the Good Friday one doesn't
    texts = [c["content"] for c in R.unanswered_complaints("NO-D0009")]
    assert any("langfredag" in t for t in texts) and not any("1. mai" in t for t in texts)
    assert R.unanswered_complaints("FI-D0001") == []


def _demo_with_complaints():
    return R.entities_with_complaints(C.DEMO_IDS)


def test_reply_has_no_invented_facts():
    found = _demo_with_complaints()
    assert {"SE-D0016", "FI-D0007", "NO-D0009"} <= set(found)
    raw = {str(r["id"]): r for r in data.reviews(R.DEFAULT_AS_OF)}
    for eid in found:
        for d in R.review_reply_drafts(eid, use_db=False):
            text, facts, rv = d["suggestion"].value, d["facts"], d["review"]
            slots = extract_slots(text)
            allowed_iv = value_intervals(facts["hours"]) if facts and facts["hours"] != "regular" else set()
            allowed_dates = set()
            if facts:
                fd = date.fromisoformat(facts["date"])
                allowed_dates = {(fd.day, fd.month)}
                # the fact really is the business's newest entry for that date, and it came after the complaint
                entries = data.holiday_entries(eid, facts["date"], R.DEFAULT_AS_OF)
                assert entries and (entries[-1]["hours"] == facts["hours"]
                                    or (entries[-1]["hours"] == "REGULAR" and facts["hours"] == "regular"))
                assert facts["date"] > (rv["day_date"] or "") or facts["entered_at"] > rv["review_date"]
            assert slots.intervals <= allowed_iv, (eid, text)
            assert slots.dates <= allowed_dates, (eid, text)
            assert not slots.numbers and not slots.times, (eid, text)
            on = days.on_day(rv["day_key"], d["lang"])
            if on:
                assert on in text
            author = raw[rv["review_id"]].get("authorName") or "@@"
            assert author not in text and author.split(" ")[0] not in text
            s = d["suggestion"]
            assert s.kind == "REVIEW_REPLY" and R.review_id_of(s) == rv["review_id"]


def test_reply_examples():
    fi = R.review_reply_drafts("FI-D0007", use_db=False)
    asc = next(d for d in fi if d["review"]["day_key"] == "ASCENSION")
    assert asc["suggestion"].value == (
        "Kiitos palautteesta, ja pahoittelut: aukioloaikamme olivat verkossa väärin helatorstaina. "
        "Viimeksi helatorstaina (14.5.) olimme kiinni. Päivitämme pyhien aukioloajat jatkossa ajoissa. "
        "Tervetuloa uudelleen!")
    sv = R.review_reply_drafts("SE-D0016", use_db=False)
    assert all(d["lang"] == "sv" for d in sv)
    assert R.detect_lang("Online it said open, but it was closed on Good Friday.") == "en"
    assert R.detect_lang("Det sto åpent 7–18 på nett, men det var stengt langfredag.") == "nb"


@pytest.mark.parametrize("lang", C.LANGS)
def test_review_reply_offer(lang):
    drafts = R.review_reply_drafts("SE-D0016", use_db=False)
    m = R.compose_review_reply_offer({"name": "Restaurang Fyren"}, drafts, lang, "WHATSAPP")
    assert len(m["text"]) <= C.WHATSAPP_BODY_LIMIT
    assert [b["id"] for b in m["buttons"]] == ["APPROVE", "EDIT", "DECLINE"]
    assert all(len(b["title"]) <= 20 for b in m["buttons"])
    assert drafts[0]["suggestion"].value in m["text"]
    assert m["parts"]["review_ids"] == [d["review"]["review_id"] for d in drafts]
    one = R.compose_review_reply_offer({"name": "X"}, drafts[:1], lang, "SMS")
    assert one["buttons"] == [] and "1 = " in one["text"]


@pytest.mark.mongo
def test_approve_review_reply_is_stored_not_posted(mongo_test_db):
    mongo_test_db[R.COLLECTION].delete_many({})
    d = R.review_reply_drafts("NO-D0009", use_db=False)[0]
    doc = R.approve_review_reply(d, db=mongo_test_db, via="web", audit=False)
    assert doc["status"] == "APPROVED_NOT_POSTED" and not doc["edited"]
    assert doc["would_post"]["path"] == f"/v2/accounts/me/reviews/{d['review']['review_id']}/comments"
    assert "not posted" in doc["note"].lower()
    edited = R.approve_review_reply(d, "Takk, vi har rettet tidene.", db=mongo_test_db, audit=False)
    assert edited["edited"] and edited["text"] == "Takk, vi har rettet tidene."
    stored = R.stored_replies("NO-D0009", db=mongo_test_db)
    assert [x["id"] for x in stored] == [doc["id"], edited["id"]]
    with pytest.raises(ValueError):
        R.approve_review_reply(d, "   ", db=mongo_test_db, audit=False)
    mongo_test_db[R.COLLECTION].delete_many({})
    assert json.dumps(stored, default=str)  # serialisable for the API


def test_apply_drift_on_the_mock_signature():
    """The real YextClient.set_regular_hours signature is what apply_drift calls."""
    import inspect
    yext = pytest.importorskip("cs.yext")
    setter = getattr(yext.YextClient, "set_regular_hours", None)
    if setter is None:
        pytest.skip("set_regular_hours not added yet")
    params = inspect.signature(setter).parameters
    assert {"entity_id", "week"} <= set(params)
