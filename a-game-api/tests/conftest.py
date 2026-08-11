"""Shared fixtures and fakes for the API's unit tests.

Nothing here touches Postgres or Redis. Both are injected onto `app.state` by the
lifespan, so replacing them with in-process doubles makes the whole read path
testable without a cluster.

**The env block must run before anything imports `app.*`.** `app.config` builds
`settings = Settings()` at module scope, so a missing variable is a collection
error rather than a test failure. pytest imports conftest first, which is what
makes this placement work.

Requests go through `httpx.ASGITransport`, not `TestClient`, deliberately: it
drives the app without running the lifespan, so the fakes below stay on
`app.state` instead of being overwritten by real connection attempts.
"""

import os

os.environ.setdefault("REDIS_HOST", "test-redis")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("POSTGRES_HOST", "test-postgres")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "test-pg-user")
os.environ.setdefault("POSTGRES_PASSWORD", "test-pg-pass")
os.environ.setdefault("POSTGRES_DB", "test-db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

CACHED_TEXT = "Cached preview prose for this fixture."
STORED_TEXT = "Preview prose read from Postgres."


# --------------------------------------------------------------------------
# asyncpg doubles
# --------------------------------------------------------------------------


class _Acquire:
    """`pool.acquire()` — async CM yielding the one fake connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    """Records every query instead of running it.

    `fetchrow_results` is consumed in order, one per call. None means no
    commentary row exists for that match — the case the read path has to
    survive.
    """

    def __init__(self, fetchrow_results=None):
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self._fetchrow_results = list(fetchrow_results or [])

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None


class FakePool:
    """`asyncpg.Pool` stand-in handing out the same connection every time.

    `acquired` is the assertion that matters for cache behaviour: a Redis hit
    must never reach Postgres, and that shows up here as a count of zero.
    """

    def __init__(self, conn: FakeConnection):
        self.conn = conn
        self.acquired = 0
        self.closed = False

    def acquire(self):
        self.acquired += 1
        return _Acquire(self.conn)

    async def close(self):
        self.closed = True


# --------------------------------------------------------------------------
# Redis double
# --------------------------------------------------------------------------


class FakeRedis:
    """In-memory stand-in built with `decode_responses=True` semantics.

    Values go in and come out as `str`, matching the real client's
    configuration in `store.make_redis`. A double that returned bytes would
    hide the fact that the endpoint never decodes anything.
    """

    def __init__(self, values=None, get_error: Exception | None = None):
        self.values: dict[str, str] = dict(values or {})
        self.gets: list[str] = []
        self.sets: list[tuple] = []
        self.closed = False
        self._get_error = get_error

    async def get(self, key: str):
        self.gets.append(key)
        if self._get_error is not None:
            raise self._get_error
        return self.values.get(key)

    async def set(self, key: str, value, ex=None):
        self.sets.append((key, value, ex))
        self.values[key] = value

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def conn():
    return FakeConnection()


@pytest.fixture
def pool(conn):
    return FakePool(conn)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
async def api(pool, redis):
    """An httpx client bound to the app, with both stores faked."""
    app.state.pg_pool = pool
    app.state.redis_client = redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
