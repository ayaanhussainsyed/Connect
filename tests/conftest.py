"""Shared pytest setup.

- Puts the repo root on ``sys.path`` so ``import cs`` / ``import app`` work under ``.venv/bin/pytest``
  (this also covers ``tests/engine/``).
- Registers markers: ``mongo`` (uses a throwaway ``<MONGODB_DB>_test_<pid>_<rand4>`` database of its own,
  dropped after the session, so parallel pytest runs by several agents don't clash), ``sandbox``
  (writes to the Yext sandbox), ``network``. Skip them with ``-m "not mongo"``.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "mongo: needs MongoDB; uses a <MONGODB_DB>_test_<pid>_<rand4> database only")
    config.addinivalue_line("markers", "sandbox: writes to the Yext sandbox and restores it")
    config.addinivalue_line("markers", "network: needs network access")


@pytest.fixture(autouse=True)
def no_real_messages(request, monkeypatch):
    """Tests never message real people: with real keys in .env the WhatsApp and e-mail channels
    would send to WHATSAPP_TEST_TO / SMTP_TEST_TO. They count as not configured unless the test is
    marked ``network``. Testing switches from .env (WHATSAPP_ASSUME_SESSION) don't leak into tests either."""
    monkeypatch.delenv("WHATSAPP_ASSUME_SESSION", raising=False)
    if request.node.get_closest_marker("network"):
        return
    from cs.channels import email, whatsapp
    monkeypatch.setattr(whatsapp, "is_configured", lambda: False)
    monkeypatch.setattr(email, "is_configured", lambda: False)


@pytest.fixture(scope="session")
def mongo_test_db():
    """This session's own test database (indexes created); dropped at the end of the session."""
    from cs import db

    if not db.settings.has("mongodb_uri"):
        pytest.skip("MONGODB_URI is not set")
    if not db.ping():
        pytest.skip("MongoDB is not reachable")
    name = db.test_db_name(f"{os.getpid()}_{secrets.token_hex(2)}")   # connect_suggests_test_<pid>_<rand4>
    database = db.get_db(name)
    assert db.is_test_db_name(database.name) and database.name != db.app_db_name()
    db.ensure_indexes(database)
    yield database
    db.drop_test_db(name)


@pytest.fixture
def audit_db(mongo_test_db):
    """The test database with an empty audit_events collection."""
    mongo_test_db.audit_events.delete_many({})
    yield mongo_test_db
    mongo_test_db.audit_events.delete_many({})


# ---------------------------------------------------------------------------
# A private Yext mock (the dataset's mock_api via scripts/run_mock.py) and the decision-flow setup
# ---------------------------------------------------------------------------
def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def yext_mock_url():
    """Start the mock on a free port with a throw-away state file; stop it at the end."""
    import signal
    import subprocess
    import time

    import httpx

    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "run_mock.py"), "--port", str(port)],
                            env={**os.environ, "MOCK_QUIET": "1"}, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"mock exited early: {proc.stdout.read()}")
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("mock did not start within 60 s")
    yield url
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def flow(mongo_test_db, yext_mock_url, monkeypatch):
    """Everything the card / decision flow needs, isolated: this session's test database (also for
    code that calls get_db()), the private mock (reset), no LLM, and fast question_value."""
    import httpx

    from cs import season
    from cs.yext import YextClient

    monkeypatch.setenv("CS_MONGODB_DB", mongo_test_db.name)
    monkeypatch.setenv("LLM_ENABLED", "0")
    httpx.post(f"{yext_mock_url}/mock/reset", timeout=10).raise_for_status()
    for c in ("cards", "decisions", "learned_facts", "permissions", "outbox", "messages", "writes", "link_nonces",
              "suggestions", "emergencies", "schedules", "reply_context", "onboarding", "journeys", "profile_offers",
              "demo_overrides"):
        mongo_test_db[c].delete_many({})

    def client(target="mock", db=None):
        return YextClient("mock", base_url=yext_mock_url, api_key="mock-test-key", db=db)

    monkeypatch.setattr(season, "yext_client", client)
    yield {"db": mongo_test_db, "mock_url": yext_mock_url, "client": client}
