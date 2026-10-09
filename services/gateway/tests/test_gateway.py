import asyncio
import os
import re
from uuid import uuid4

os.environ.setdefault("JWT_SECRET", "test-secret-please-change-0123456789")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402

from app import schema as schema_module  # noqa: E402
from app.main import schema  # noqa: E402
from app.projection import Node, to_camel, to_snake  # noqa: E402
from app.ratelimit import RateLimiter, Rule  # noqa: E402
from app.security import hash_password, needs_rehash, verify_password  # noqa: E402
from app.sessions import Session, SessionStore  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ passwords
def test_argon2id_hash_and_verify():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$") and "m=19456" in h and "t=3" in h
    assert verify_password(h, "correct-horse-battery")
    assert not verify_password(h, "wrong-password-here")
    assert not verify_password(None, "anything")  # unknown user -> False (constant-ish time)
    assert not needs_rehash(h)


# ------------------------------------------------------------------ sessions (Session Fixation)
def make_store():
    return SessionStore(fakeredis.aioredis.FakeRedis(decode_responses=True), os.environ["JWT_SECRET"], 60)


def test_session_id_is_regenerated_on_login_and_old_id_is_invalid():
    async def scenario():
        store = make_store()
        anon = await store.create()                       # pre-login session (attacker knows this id)
        cookie_before = store.encode(anon.id)
        user = uuid4()
        fresh = await store.regenerate(anon, user)        # what login does
        assert fresh.id != anon.id
        old = await store.load(cookie_before)             # attacker replays the fixed cookie
        assert old.user_id is None and old.id != anon.id  # not authenticated, and a NEW anon session is issued
        good = await store.load(store.encode(fresh.id))
        assert good.user_id == user
    run(scenario())


def test_forged_or_tampered_cookie_is_rejected():
    async def scenario():
        store = make_store()
        s = await store.create(uuid4())
        assert store.decode(store.encode(s.id)) == s.id
        assert store.decode(s.id + ".deadbeef" * 4) is None
        assert store.decode(store.encode(s.id)[:-2] + "zz") is None
        assert store.decode(None) is None and store.decode("nodot") is None
    run(scenario())


def test_logout_destroys_session():
    async def scenario():
        store = make_store()
        s = await store.create(uuid4())
        await store.destroy(s)
        assert (await store.load(store.encode(s.id))).user_id is None
    run(scenario())


# ------------------------------------------------------------------ rate limiting
def test_sliding_window_rate_limit_blocks_and_reports_retry_after():
    pytest.importorskip("lupa")

    async def scenario():
        limiter = RateLimiter(fakeredis.aioredis.FakeRedis(decode_responses=True))
        rule = Rule("t", limit=3, window_s=60)
        assert [(await limiter.hit(rule, "ip1"))[0] for _ in range(3)] == [True, True, True]
        allowed, retry = await limiter.hit(rule, "ip1")
        assert not allowed and 1 <= retry <= 60
        assert (await limiter.hit(rule, "ip2"))[0]        # other identity unaffected
    run(scenario())


# ------------------------------------------------------------------ projection / no over-fetching
def test_name_helpers():
    assert to_snake("pricePerNight") == "price_per_night" and to_camel("price_per_night") == "pricePerNight"
    assert Node({"page_info": 1}).page_info == 1 and Node({"hasNextPage": True}).has_next_page is True
    with pytest.raises(AttributeError):
        Node({}).__await__


class FakeCtx:
    session = Session(id="anon")
    client_ip = "127.0.0.1"


def execute(query, monkeypatch, canned):
    sent = {}

    async def fake_resolve(pool, gql, variables=None):
        sent["query"], sent["variables"] = gql, variables
        return canned

    monkeypatch.setattr(schema_module, "resolve", fake_resolve)
    monkeypatch.setattr(schema_module.state, "pool", None, raising=False)
    result = run(schema.execute(query, context_value=FakeCtx()))
    return result, sent


def test_only_requested_columns_reach_the_database(monkeypatch):
    canned = {"r": {"edges": [{"node": {"id": "1", "price": 99.5}}]}}
    result, sent = execute('{ flights(origin:"BOG", destination:"MDE"){ edges{ node{ id price } } } }',
                           monkeypatch, canned)
    assert result.errors is None and result.data["flights"]["edges"][0]["node"] == {"id": "1", "price": 99.5}
    cols = re.search(r"node \{ ([^}]*) \}", sent["query"]).group(1).split()
    assert sorted(cols) == ["id", "price"]                      # nothing else was requested from Postgres
    assert "airline" not in sent["query"] and "seats_available" not in sent["query"]


def test_camel_case_fields_map_to_snake_case_columns(monkeypatch):
    canned = {"r": {"edges": [{"node": {"departure_at": "2026-10-01T10:00:00+00:00", "seats_available": 4}}]}}
    result, sent = execute('{ flights(origin:"BOG", destination:"MDE"){ edges{ node{ departureAt seatsAvailable } } } }',
                           monkeypatch, canned)
    assert result.errors is None
    assert sorted(re.search(r"node \{ ([^}]*) \}", sent["query"]).group(1).split()) == ["departure_at", "seats_available"]


def test_search_packages_skips_unrequested_collections_and_uses_one_roundtrip(monkeypatch):
    canned = {"flights": {"edges": [{"node": {"id": "f"}}]}}
    result, sent = execute('{ searchPackages(origin:"BOG", destination:"MDE", departDate:"2026-10-01"){'
                           ' flights{ edges{ node{ id } } } } }', monkeypatch, canned)
    assert result.errors is None
    assert "ws_catalog_hotelsCollection" not in sent["query"] and "ws_catalog_carsCollection" not in sent["query"]

    canned = {k: {"edges": []} for k in ("flights", "hotels", "cars")}
    result, sent = execute('{ searchPackages(origin:"BOG", destination:"MDE", departDate:"2026-10-01"){'
                           ' flights{ edges{ node{ id } } } hotels{ edges{ node{ id } } } cars{ edges{ node{ id } } } } }',
                           monkeypatch, canned)
    assert result.errors is None and sent["query"].count("Collection(") == 3   # one query, three collections


def test_user_input_is_passed_as_variables_never_interpolated(monkeypatch):
    canned = {"r": {"edges": []}}
    _, sent = execute('{ hotels(city:"MDE", minStars: 4){ edges{ node{ id } } } }', monkeypatch, canned)
    assert sent["variables"]["hf"] == {"city": {"eq": "MDE"}, "stars": {"gte": 4}}
    result, _ = execute('{ hotels(city:"MDE\\") { evil"){ edges{ node{ id } } } }', monkeypatch, canned)
    assert result.errors  # rejected by IATA validation before reaching the database


def test_orders_require_authentication(monkeypatch):
    result, _ = execute('{ myOrders { id } }', monkeypatch, {})
    assert result.errors and "sesión" in result.errors[0].message


def test_search_packages_real_only_excludes_the_synthetic_source(monkeypatch):
    canned = {k: {"edges": []} for k in ("flights", "hotels", "cars")}
    result, sent = execute('{ searchPackages(origin:"BOG", destination:"MDE", departDate:"2026-10-01", realOnly: true){'
                           ' flights{ edges{ node{ id source } } } hotels{ edges{ node{ id } } } cars{ edges{ node{ id } } } } }',
                           monkeypatch, canned)
    assert result.errors is None
    for prefix in ("f", "h", "c"):
        assert sent["variables"][f"{prefix}f"]["source"] == {"neq": "mock"}


def test_departure_day_is_the_local_day_at_the_origin_airport(monkeypatch):
    canned = {k: {"edges": []} for k in ("flights", "hotels", "cars")}
    _, sent = execute('{ searchPackages(origin:"BOG", destination:"MAD", departDate:"2026-10-12"){'
                      ' flights{ edges{ node{ id } } } } }', monkeypatch, canned)
    # A 21:30 departure from Bogotá on Oct 12 is 02:30Z on Oct 13: the UTC day would miss it.
    assert sent["variables"]["ff"]["departure_at"] == {"gte": "2026-10-12T00:00:00-05:00",
                                                       "lt": "2026-10-13T00:00:00-05:00"}


def test_data_sources_reads_the_ingestion_summary_view(monkeypatch):
    canned = {"d": {"edges": [{"node": {"id": "hotels:booking.com", "kind": "hotels", "source": "booking.com",
                                        "items": 169}}]}}
    result, sent = execute("{ dataSources { kind source items } }", monkeypatch, canned)
    assert result.errors is None and result.data["dataSources"][0]["items"] == 169
    assert "ws_catalog_sourcesCollection" in sent["query"]
