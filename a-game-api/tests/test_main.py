"""The HTTP surface: probes and the read path.

Redis first, Postgres second, `404` if neither has it — the contract in
`docs/api-spec.md` §4 and §7.
"""

from app.__version__ import __version__
from app.main import CACHE_TTL_SECONDS, app
from tests.conftest import CACHED_TEXT, STORED_TEXT, FakeConnection, FakePool, FakeRedis

MATCH_ID = 538107
KEY = f"match_id:{MATCH_ID}"


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------


async def test_health_reports_ok_and_the_running_version(api):
    response = await api.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readyz_reports_ready(api):
    """It answers from memory and never touches a store, so it says READY even
    with both stores down. Worth knowing before trusting it as a gate."""
    response = await api.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_root_returns_the_placeholder_greeting(api):
    response = await api.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


# --------------------------------------------------------------------------
# The read path
# --------------------------------------------------------------------------


async def test_cache_hit_is_served_from_redis(api, redis, pool):
    """The point of the cache: a hit must not reach Postgres at all."""
    redis.values[KEY] = CACHED_TEXT

    response = await api.get(f"/{MATCH_ID}")

    assert response.status_code == 200
    assert response.json() == {"description": CACHED_TEXT, "match_id": MATCH_ID}
    assert redis.gets == [KEY]
    assert pool.acquired == 0


async def test_cache_miss_falls_back_to_postgres(api, redis, pool, conn):
    conn._fetchrow_results = [{"prediction": STORED_TEXT}]

    response = await api.get(f"/{MATCH_ID}")

    assert response.status_code == 200
    assert response.json()["description"] == STORED_TEXT
    assert redis.gets == [KEY]
    assert pool.acquired == 1


async def test_postgres_hit_is_written_back_to_redis(api, redis, conn):
    """The backfill in api-spec §4. Without it a cold cache hits Postgres on
    every request for that fixture, forever."""
    conn._fetchrow_results = [{"prediction": STORED_TEXT}]

    await api.get(f"/{MATCH_ID}")

    assert redis.sets == [(KEY, STORED_TEXT, CACHE_TTL_SECONDS)]


async def test_a_cache_hit_is_not_written_back(api, redis, conn):
    """Re-writing on every hit would refresh the TTL indefinitely and keep a
    fixture cached long after the pipeline stopped touching it."""
    redis.values[KEY] = CACHED_TEXT

    await api.get(f"/{MATCH_ID}")

    assert redis.sets == []


async def test_missing_suggestion_is_a_404(api):
    """api-spec §7: neither store has it, so the fixture is unknown or the
    pipeline hasn't reached it. Returning 200 with placeholder prose would make
    "no data" indistinguishable from a real preview by status code."""
    response = await api.get(f"/{MATCH_ID}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "suggestion_not_ready"


async def test_a_404_does_not_backfill_the_cache(api, redis, conn):
    """Nothing to cache, and caching a miss would serve the 404 for a day after
    the pipeline filled the row in."""
    await api.get(f"/{MATCH_ID}")

    assert redis.sets == []


async def test_non_integer_match_id_is_rejected_before_the_handler(api, redis):
    """FastAPI's path validation, so neither store is touched (api-spec §3)."""
    response = await api.get("/not-a-number")

    assert response.status_code == 422
    assert redis.gets == []


async def test_reads_postgres_directly_when_no_redis_client_is_set(api, pool, conn):
    """The `redis is None` branch. `make_redis` raises rather than returning
    None, so nothing reaches this in production — it is dead code, and this test
    exists to cover it rather than to endorse it."""
    conn._fetchrow_results = [{"prediction": STORED_TEXT}]
    app.state.redis_client = None

    response = await api.get(f"/{MATCH_ID}")

    assert response.status_code == 200
    assert response.json()["description"] == STORED_TEXT
    assert pool.acquired == 1


async def test_a_store_failure_propagates_rather_than_being_swallowed(api):
    """The handler logs and re-raises. A broken store must surface as a 500, not
    as a 200 carrying the placeholder — that would look like a missing
    prediction and hide the outage."""
    app.state.redis_client = FakeRedis(get_error=RuntimeError("redis is down"))

    try:
        response = await api.get(f"/{MATCH_ID}")
    except RuntimeError:
        return

    assert response.status_code == 500


# --------------------------------------------------------------------------
# Lifespan
# --------------------------------------------------------------------------


async def test_lifespan_opens_both_stores_once_and_closes_them(monkeypatch):
    """One pool and one client per process, not per request. The close half
    matters too: without it a rolling restart leaks connections until Postgres
    refuses new ones."""
    pool = FakePool(FakeConnection())
    redis = FakeRedis()

    async def fake_pg():
        return pool

    async def fake_redis():
        return redis

    monkeypatch.setattr("app.main.make_pg_pool", fake_pg)
    monkeypatch.setattr("app.main.make_redis", fake_redis)

    async with app.router.lifespan_context(app):
        assert app.state.pg_pool is pool
        assert app.state.redis_client is redis
        assert not pool.closed

    assert pool.closed
    assert redis.closed
