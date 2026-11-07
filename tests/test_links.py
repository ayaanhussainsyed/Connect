"""cs.links: signed, expiring, single-use tokens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cs import links

SECRET = "test-secret-not-the-real-one"


def test_sign_and_verify_round_trip():
    t = links.sign("card-1", "owner:FI-D0001", secret=SECRET)
    p = links.verify(t, secret=SECRET)
    assert p.card_id == "card-1" and p.owner_id == "owner:FI-D0001" and len(p.nonce) >= 12
    assert p.expires > datetime.now(timezone.utc) + timedelta(days=29)
    assert links.sign("card-1", "o", secret=SECRET) != links.sign("card-1", "o", secret=SECRET)  # fresh nonce


def test_expired():
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    t = links.sign("card-1", "o", ttl=timedelta(hours=1), now=now, secret=SECRET)
    assert links.verify(t, now=now + timedelta(minutes=59), secret=SECRET).card_id == "card-1"
    with pytest.raises(links.TokenError) as e:
        links.verify(t, now=now + timedelta(hours=1), secret=SECRET)
    assert e.value.kind == "expired"


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),          # signature changed
    lambda t: ("e" if t[0] != "e" else "f") + t[1:],                       # payload changed
    lambda t: links.sign("card-OTHER", "o", secret=SECRET).split(".")[0] + "." + t.split(".")[1],  # swapped body
])
def test_tampering_is_rejected(mutate):
    t = links.sign("card-1", "o", secret=SECRET)
    with pytest.raises(links.TokenError) as e:
        links.verify(mutate(t), secret=SECRET)
    assert e.value.kind in ("bad_signature", "malformed")


@pytest.mark.parametrize("bad", ["", "abc", "a.b.c", ".", "x.", None, 123])
def test_malformed(bad):
    with pytest.raises(links.TokenError):
        links.verify(bad, secret=SECRET)


def test_other_secret_is_rejected():
    t = links.sign("card-1", "o", secret=SECRET)
    with pytest.raises(links.TokenError) as e:
        links.verify(t, secret="another-secret")
    assert e.value.kind == "bad_signature"


def test_link_for_uses_base_url():
    assert links.link_for("TOKEN", "http://localhost:5050/") == "http://localhost:5050/c/TOKEN"


@pytest.mark.mongo
def test_consume_is_single_use_per_action(mongo_test_db):
    t = links.sign("card-1", "o", secret=SECRET)
    assert links.consume(t, "decide", mongo_test_db, secret=SECRET).card_id == "card-1"
    with pytest.raises(links.TokenError) as e:
        links.consume(t, "decide", mongo_test_db, secret=SECRET)
    assert e.value.kind == "used"
    links.consume(t, "undo", mongo_test_db, secret=SECRET)       # a different action is its own use
    with pytest.raises(ValueError):
        links.consume(t, "approve-everything", mongo_test_db, secret=SECRET)


@pytest.mark.mongo
def test_release_allows_a_retry(mongo_test_db):
    t = links.sign("card-2", "o")   # the real LINK_SECRET (never printed)
    links.consume(t, "decide", mongo_test_db)
    links.release(t, "decide", mongo_test_db)
    links.consume(t, "decide", mongo_test_db)
    assert links.is_used(t, "decide", mongo_test_db)


@pytest.mark.mongo
def test_expired_token_cannot_be_consumed(mongo_test_db):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    t = links.sign("card-3", "o", now=old, secret=SECRET)
    with pytest.raises(links.TokenError) as e:
        links.consume(t, "decide", mongo_test_db, secret=SECRET)
    assert e.value.kind == "expired"
    assert mongo_test_db.link_nonces.count_documents({"card_id": "card-3"}) == 0
