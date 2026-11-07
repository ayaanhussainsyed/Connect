"""B10 business memory graph and grounded chat."""
from __future__ import annotations

import pandas as pd
import pytest

from cs.engine import data as D
from cs.kg.answer import DONT_KNOW, ask, check_answer
from cs.kg.build import MIN_PEERS, build, build_graph
from cs.kg.retrieve import resolve_question, subgraph


@pytest.fixture(scope="module")
def g4():
    return build_graph("FI-D0004")


def _types(nodes):
    return pd.Series([n["type"] for n in nodes]).value_counts().to_dict()


# ----------------------------------------------------------------------------- build
def test_graph_counts(g4):
    nodes, edges = g4
    t = _types(nodes)
    assert t["BUSINESS"] == 1 and t["MALL"] == 1
    assert t["SPECIAL_DAY"] >= 60 and t["ENTRY"] >= 10 and t.get("PEER_GROUP", 0) >= 5
    et = pd.Series([e["type"] for e in edges]).value_counts().to_dict()
    assert et["IN_MALL"] == 1 and et["ENTERED"] >= 10 and et["LINKED_HOLIDAY"] >= 5
    assert all(n.get("source") and n.get("ref") for n in nodes)          # every node can be cited
    ids = {n["id"] for n in nodes}
    assert all(e["src"] in ids and e["dst"] in ids for e in edges)


def test_no_author_names_stored():
    nodes, _ = build_graph("FI-D0017")
    reviews = [n for n in nodes if n["type"] == "REVIEW"]
    assert reviews, "FI-D0017 should have hours-related reviews"
    assert all(set(n["props"]) == {"date", "rating", "quote"} for n in reviews)
    R = D.reviews()
    authors = set(pd.json_normalize(pd.read_json(D.DS / "data" / "reviews.json").response["reviews"])
                  .query("locationId == 'FI-D0017'").authorName)
    text = " ".join(str(n["props"]) + n["label"] for n in nodes)
    assert authors and not any(a in text for a in authors)
    assert len(R)  # reviews loaded


def test_peer_groups_only_with_ten_or_more():
    for eid in ("FI-D0001", "FI-D0017", "SE-D0016", "FI-D0004"):
        nodes, _ = build_graph(eid)
        for n in nodes:
            if n["type"] == "PEER_GROUP":
                assert n["props"]["n_businesses"] >= MIN_PEERS and n["props"]["n_entered"] >= MIN_PEERS
    nodes, _ = build_graph("FI-D0005")       # a segment without 10 comparable entries: no peer node at all
    assert "PEER_GROUP" not in _types(nodes)


# ----------------------------------------------------------------------------- retrieval
def test_question_resolution():
    r = resolve_question("FI-D0001", "Mitä tein viime pääsiäisenä?")
    assert {"2026-04-03", "2026-04-05", "2026-04-06"} <= set(r["dates"]) and r["past"]
    r = resolve_question("FI-D0004", "Onko kauppakeskus auki 26.12.?")
    assert r["dates"] == ["2026-12-26"] and "MALL" in r["topics"]
    r = resolve_question("FI-D0017", "What do similar cafés do on Boxing Day?")
    assert r["day_keys"] == ["BOXING_DAY"] and r["dates"] == ["2026-12-26"] and "PEERS" in r["topics"]
    r = resolve_question("SE-D0008", "Hade vi öppet på julafton i fjol?")
    assert r["dates"] == ["2025-12-24"]


@pytest.mark.mongo
def test_subgraph_finds_the_asked_day(mongo_test_db):
    build("FI-D0004", db=mongo_test_db)
    sub = subgraph("FI-D0004", ["2026-12-26"], ["BOXING_DAY"], hops=2, db=mongo_test_db, topics=("MALL",))
    ids = {n["id"] for n in sub["nodes"]}
    assert sub["seeds"] == ["FI-D0004:SPECIAL_DAY:2026-12-26"]
    mall = D.entities().set_index("entity_id").loc["FI-D0004", "mall_id"]
    assert f"FI-D0004:ENTRY:{mall}:2026-12-26" in ids and f"FI-D0004:MALL:{mall}" in ids
    assert not any(n["type"] == "ENTRY" and n["props"]["date"] < "2026-01-01" for n in sub["nodes"])


# ----------------------------------------------------------------------------- the answer check
FACTS = [("holiday_hours_history:FI-M000:2026-12-26", "The shopping centre entered 11:00-17:00 for 26.12.2026 (BOXING_DAY)."),
         ("peers:x", "Similar businesses: 36 of 83 entered BOXING_DAY; 72% of those were closed.")]


def test_check_rejects_invented_time_and_bad_citations():
    ok, _, _ = check_answer("Yes, the centre is open 11:00–17:00 on 26.12.2026 [holiday_hours_history:FI-M000:2026-12-26].", FACTS)
    assert ok
    assert not check_answer("Yes, it is open 09:00–17:00 [holiday_hours_history:FI-M000:2026-12-26].", FACTS)[0]
    assert not check_answer("It opens at 10:30 [holiday_hours_history:FI-M000:2026-12-26].", FACTS)[0]
    assert not check_answer("80% of similar cafés were closed [peers:x].", FACTS)[0]
    assert not check_answer("Yes, it is open 11:00–17:00.", FACTS)[0]                           # no citation
    assert not check_answer("Open 11:00–17:00 [made-up:ref].", FACTS)[0]                          # unknown citation
    assert not check_answer("NO_ANSWER", FACTS)[0]


@pytest.mark.mongo
def test_offline_answer_is_templated_and_cited(mongo_test_db, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "0")
    build("FI-D0004", db=mongo_test_db)
    r = ask("FI-D0004", "Onko kauppakeskus auki 26.12.?", "fi", db=mongo_test_db)
    assert r["mode"] == "template" and r["grounded"]
    assert "11:00–17:00" in r["answer"] and r["citations"] and r["citations"][0].startswith("holiday_hours_history:")
    r = ask("FI-D0004", "Mikä on lempivärisi?", "fi", db=mongo_test_db)        # nothing to retrieve
    assert r["answer"] == DONT_KNOW["fi"] and r["mode"] == "fallback"


@pytest.mark.mongo
@pytest.mark.network
def test_live_grounded_answer(mongo_test_db):
    from cs import llm
    if not llm.claude_available():
        pytest.skip("Claude not configured")
    build("FI-D0004", db=mongo_test_db)
    r = ask("FI-D0004", "Is the shopping centre open on 26.12?", "en", db=mongo_test_db)
    assert r["grounded"]
    if r["mode"] == "llm":
        assert r["citations"] and any(c.startswith("holiday_hours_history:") for c in r["citations"])
    else:
        assert r["answer"] == DONT_KNOW["en"]


def test_uncited_sentences_are_dropped():
    from cs.kg.answer import keep_cited_sentences
    a = ("Yes. The centre is open 11:00–17:00 [holiday_hours_history:FI-M000:2026-12-26]. "
         "It also opens at 9 on the 27th.\n\nThe café next door")
    out = keep_cited_sentences(a)
    assert out.endswith("[holiday_hours_history:FI-M000:2026-12-26].") and "27th" not in out and "next door" not in out
