"""Connection setup and its retry loops (`app/store.py`).

Both helpers run once, from the lifespan, and both retry because on a cold
`kubectl apply` the api can start before Postgres and Redis accept connections.
What's tested is the loop's contract: it retries a bounded number of times and
then raises, rather than handing back something unusable.

`asyncio.sleep` is patched out throughout — the real loop waits 5s between
attempts, which would make this module take 40 seconds.
"""

import pytest

from app import store
from tests.conftest import FakeConnection, FakePool, FakeRedis


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(store.asyncio, "sleep", instant)


# --------------------------------------------------------------------------
# make_pg_pool
# --------------------------------------------------------------------------


async def test_pg_pool_is_returned_on_the_first_success(monkeypatch):
    pool = FakePool(FakeConnection())
    calls = []

    async def create_pool(**kwargs):
        calls.append(kwargs)
        return pool

    monkeypatch.setattr(store.asyncpg, "create_pool", create_pool)

    assert await store.make_pg_pool() is pool
    assert len(calls) == 1


async def test_pg_pool_is_built_from_the_settings_dsn(monkeypatch):
    """`dsn=`, not `connect=`. The latter is not a create_pool parameter and
    fails at runtime, which no type checker catches."""
    captured = {}

    async def create_pool(**kwargs):
        captured.update(kwargs)
        return FakePool(FakeConnection())

    monkeypatch.setattr(store.asyncpg, "create_pool", create_pool)

    await store.make_pg_pool()

    assert captured["dsn"] == store.settings.postgres_url
    assert captured["min_size"] == 1
    assert captured["max_size"] == 5


async def test_pg_pool_retries_then_succeeds(monkeypatch):
    """asyncpg raises OSError, which does not inherit from the builtin
    ConnectionError — catching that specifically would let this escape."""
    pool = FakePool(FakeConnection())
    attempts = []

    async def create_pool(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("connection refused")
        return pool

    monkeypatch.setattr(store.asyncpg, "create_pool", create_pool)

    assert await store.make_pg_pool() is pool
    assert len(attempts) == 3


async def test_pg_pool_raises_after_five_failed_attempts(monkeypatch):
    attempts = []

    async def create_pool(**kwargs):
        attempts.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(store.asyncpg, "create_pool", create_pool)

    with pytest.raises(ConnectionError, match="after multiple attempts"):
        await store.make_pg_pool()

    assert len(attempts) == 5


# --------------------------------------------------------------------------
# make_redis
# --------------------------------------------------------------------------


async def test_redis_client_is_returned_once_it_answers_a_ping(monkeypatch):
    client = FakeRedis()

    monkeypatch.setattr(store.redis, "from_url", lambda **kwargs: client)

    assert await store.make_redis() is client


async def test_redis_client_decodes_responses(monkeypatch):
    """Without this the endpoint gets bytes back and every cached value would
    need decoding it doesn't do."""
    captured = {}

    def from_url(**kwargs):
        captured.update(kwargs)
        return FakeRedis()

    monkeypatch.setattr(store.redis, "from_url", from_url)

    await store.make_redis()

    assert captured["decode_responses"] is True
    assert captured["url"] == store.settings.redis_url


async def test_redis_retries_when_the_ping_fails(monkeypatch):
    """`from_url` opens no socket, so construction always succeeds — the ping is
    the only thing that proves Redis is actually reachable."""
    attempts = []

    class FlakyRedis(FakeRedis):
        async def ping(self):
            attempts.append(1)
            if len(attempts) < 2:
                raise OSError("connection refused")
            return True

    client = FlakyRedis()
    monkeypatch.setattr(store.redis, "from_url", lambda **kwargs: client)

    assert await store.make_redis() is client
    assert len(attempts) == 2


async def test_redis_raises_after_five_failed_pings(monkeypatch):
    """It must raise rather than return a dead client. A caller that only checks
    for None would hand the endpoint something that fails on every request."""
    class DeadRedis(FakeRedis):
        async def ping(self):
            raise OSError("connection refused")

    monkeypatch.setattr(store.redis, "from_url", lambda **kwargs: DeadRedis())

    with pytest.raises(ConnectionError, match="after multiple attempts"):
        await store.make_redis()
