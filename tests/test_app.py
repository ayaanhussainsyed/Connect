"""Flask app: A1 endpoints."""
from __future__ import annotations

import pytest

from app.app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def test_api_shown_demo_row(client):
    r = client.get("/api/shown?entity_id=FI-D0001&date=2026-12-24")
    assert r.status_code == 200
    j = r.get_json()
    assert j["shown"] == "07:30-18:00" and j["source"] == "REGULAR_HOURS"
    assert j["as_of"] == "2026-11-06" and j["label"] is None and j["label_match"] is None
    assert "summary" in j["explanation"]


def test_api_shown_dev_row_has_label(client):
    j = client.get("/api/shown?entity_id=FI-00330&date=2026-12-21").get_json()
    assert j["shown"] == "10:00-17:00"
    assert j["label"]["displayed_hours_at_snapshot"] == "10:00-17:00" and j["label_match"] is True
    assert j["label"]["truth_hours"] == "10:00-18:00"


def test_api_shown_back_test_snapshot(client):
    j = client.get("/api/shown?entity_id=FI-00848&date=2025-11-28&as_of=2025-11-06").get_json()
    assert j["shown"] == "11:00-13:00" and j["label_match"] is True


@pytest.mark.parametrize("qs, code", [
    ("entity_id=NOPE&date=2026-12-24", 404),
    ("entity_id=FI-D0001&date=2026-13-24", 400),
    ("date=2026-12-24", 400),
    ("entity_id=FI-D0001", 400),
])
def test_api_shown_errors(client, qs, code):
    r = client.get(f"/api/shown?{qs}")
    assert r.status_code == code and "error" in r.get_json()


def test_a1_page(client):
    r = client.get("/a1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'hx-get="/a1/result"' in html and "FI-D0001" in html and "Kahvila Aurora" in html
    assert html.count("<option value=") >= 3032


def test_a1_page_with_query_renders_result(client):
    html = client.get("/a1?entity_id=FI-00330&date=2026-12-21").get_data(as_text=True)
    assert "10:00-17:00" in html and "✓" in html and "Upcoming special days" in html


def test_a1_result_partial(client):
    r = client.get("/a1/result?entity_id=FI-D0001+Kahvila+Aurora&date=2026-12-24&as_of=2026-11-06")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "<html" not in html and "07:30-18:00" in html and "Upcoming special days" in html
    assert r.headers["HX-Push-Url"].startswith("/a1?entity_id=FI-D0001")


def test_a1_result_errors_are_friendly(client):
    html = client.get("/a1/result?entity_id=NOPE&date=2026-12-24").get_data(as_text=True)
    assert 'role="alert"' in html


# --- suggestions (chunk 2) ------------------------------------------------------
def test_api_suggestions_baseline(client):
    r = client.get("/api/suggestions?entity_id=FI-D0011")
    assert r.status_code == 200
    j = r.get_json()
    assert j["engine"] == "baseline" and j["season"] == 2026 and j["model_versions"] == ["baseline-v0"]
    assert j["suggestions"] and all(s["action"] == "FLAG" and s["message_ok"] is False for s in j["suggestions"])
    first = j["suggestions"][0]
    assert {"id", "date", "day_key", "shown", "action", "value", "probability", "basis", "evidence",
            "guardrail_notes", "message_ok", "auto_apply_ok", "label"} <= set(first)


def test_api_suggestions_dev_rows_have_label_points(client):
    j = client.get("/api/suggestions?entity_id=FI-00330&engine=baseline").get_json()
    labelled = [s for s in j["suggestions"] if s["label"]]
    assert labelled and all({"truth_hours", "points", "outcome", "ok"} <= set(s["label"]) for s in labelled)
    assert j["summary"]["dev_rows"] == len(labelled)


def test_api_suggestions_ml_engine(client):
    j = client.get("/api/suggestions?entity_id=FI-00002&engine=ml").get_json()
    assert j["engine"] == "ml" and j["suggestions"]


@pytest.mark.parametrize("qs, code", [("", 400), ("entity_id=NOPE", 404), ("entity_id=FI-D0001&engine=x", 400),
                                      ("entity_id=FI-D0001&season=2024", 400)])
def test_api_suggestions_errors(client, qs, code):
    assert client.get(f"/api/suggestions?{qs}").status_code == code


def test_suggest_page_and_partial(client):
    html = client.get("/suggest").get_data(as_text=True)
    assert 'hx-get="/suggest/result"' in html and "FI-D0001" in html
    html = client.get("/suggest?entity_id=FI-D0013").get_data(as_text=True)
    assert "Upcoming special days" in html and "Hammasklinikka Kallio" in html
    r = client.get("/suggest/result?entity_id=FI-00330&engine=baseline&season=2026")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "<html" not in body and "Dev points" in body
    assert r.headers["HX-Push-Url"].startswith("/suggest?entity_id=FI-00330")
    assert 'role="alert"' in client.get("/suggest/result?entity_id=NOPE").get_data(as_text=True)


def test_a1_links_to_suggestions(client):
    html = client.get("/a1?entity_id=FI-D0001&date=2026-12-24").get_data(as_text=True)
    assert "/suggest?entity_id=FI-D0001" in html
