"""Shared fixtures and fakes for the brain's unit tests.

No Postgres, no RabbitMQ, no Redis, no LiteLLM. Everything the brain touches is
passed in — `pg`, `redis`, `client` — with the single exception of `settings`,
which is a module-level singleton.

**The env block must run before anything imports `app.*`.** `app.config` builds
`Settings()` at module scope, so a missing variable is a collection error rather
than a test failure. pytest imports conftest first, which is what makes this work.

`_env_file=None` is used in the Settings tests for the same reason as the worker's:
there is a real `.env` in this directory, and without it those assertions would be
testing that file instead of the code.
"""

import os

os.environ.setdefault("RABBITMQ_HOST", "test-rabbit")
os.environ.setdefault("RABBITMQ_PORT", "5672")
os.environ.setdefault("RABBITMQ_DEFAULT_USER", "test-user")
os.environ.setdefault("RABBITMQ_DEFAULT_PASS", "test-pass")
os.environ.setdefault("RABBITMQ_QUEUE", "test-queue")
os.environ.setdefault("REDIS_HOST", "test-redis")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("POSTGRES_HOST", "test-postgres")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "test-pg-user")
os.environ.setdefault("POSTGRES_PASSWORD", "test-pg-pass")
os.environ.setdefault("POSTGRES_DB", "test-db")
os.environ.setdefault("LITELLM_HOST", "test-litellm")
os.environ.setdefault("LITELLM_PORT", "4000")

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.db_model import Match
from app.engine.elo import MatchResult

# --------------------------------------------------------------------------
# asyncpg doubles
# --------------------------------------------------------------------------


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    """Records statements instead of running them.

    `execute` can be made to raise, which is how the transaction tests check that
    a failed second write is not silently swallowed.
    """

    def __init__(self, fetch_results=None, fetchrow_result=None, execute_error=None):
        self.executed: list[tuple[str, tuple]] = []
        self._fetch_results = fetch_results if fetch_results is not None else []
        self._fetchrow_result = fetchrow_result
        self._execute_error = execute_error

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if self._execute_error is not None and self._execute_error[0] in query:
            raise self._execute_error[1]
        return "INSERT 0 1"

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self._fetch_results

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return self._fetchrow_result

    def transaction(self):
        return _NullTransaction()

    def executed_matching(self, needle: str) -> list[tuple[str, tuple]]:
        return [(q, a) for q, a in self.executed if needle in q]


class FakePool:
    def __init__(self, conn: FakeConnection):
        self.conn = conn
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return _Acquire(self.conn)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, object] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture
def conn():
    return FakeConnection()


@pytest.fixture
def pool(conn):
    return FakePool(conn)


@pytest.fixture
def fake_redis():
    return FakeRedis()


# --------------------------------------------------------------------------
# LiteLLM / OpenAI doubles
# --------------------------------------------------------------------------


class FakeRawResponse:
    """Mimics `LegacyAPIResponse` — `.headers` plus a synchronous `.parse()`.

    `parse()` really is sync on the raw-response wrapper even under the async
    client. Getting that wrong is the kind of thing that only shows up at runtime,
    so the double matches the real shape rather than being convenient.
    """

    def __init__(self, parsed, headers=None):
        self._parsed = parsed
        self.headers = headers or {}

    def parse(self):
        return self._parsed


class FakeToolCall:
    def __init__(self, arguments: str, call_type: str = "function"):
        self.type = call_type
        self.function = type("Fn", (), {"name": "emit_preview", "arguments": arguments})()


class FakeMessage:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, tool_calls):
        self.message = FakeMessage(tool_calls)


class FakeCompletion:
    def __init__(self, tool_calls, model="claude-haiku", usage=True):
        self.choices = [FakeChoice(tool_calls)] if tool_calls is not None else []
        self.model = model
        self.usage = (
            type("Usage", (), {"prompt_tokens": 1200, "completion_tokens": 180})()
            if usage
            else None
        )


class FakeLLMClient:
    """`AsyncOpenAI` stand-in exposing only `chat.completions.with_raw_response`."""

    def __init__(self, raw=None, error=None):
        self.calls: list[dict] = []
        client = self

        class _WithRaw:
            async def create(self, **kwargs):
                client.calls.append(kwargs)
                if error is not None:
                    raise error
                return raw

        class _Completions:
            with_raw_response = _WithRaw()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def tool_arguments(**overrides) -> str:
    payload = {
        "text": (
            "Manchester City are slight favourites here, though the model makes it "
            "closer than the league baseline would suggest."
        ),
        "suggested_bet": "Over 2.5 goals",
        "suggested_bet_reason": "The model puts this at 58% against a 52% baseline.",
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def llm_client():
    return FakeLLMClient(
        raw=FakeRawResponse(
            FakeCompletion([FakeToolCall(tool_arguments())]),
            headers={"x-litellm-model-name": "anthropic/claude-haiku-4-5-20251001"},
        )
    )


# --------------------------------------------------------------------------
# Domain fixtures
# --------------------------------------------------------------------------

KICKOFF = datetime(2026, 8, 22, 14, 0, tzinfo=UTC)


def make_match(**overrides) -> Match:
    base = {
        "id": 538107,
        "season_id": 2502,
        "competition_id": 2021,
        "home_team_id": 65,
        "away_team_id": 57,
        "home_team": "Manchester City FC",
        "away_team": "Arsenal FC",
        "matchday": 2,
        "utc_date": KICKOFF,
        "status": "SCHEDULED",
        "home_goals": None,
        "away_goals": None,
        "fulltime_outcome": None,
    }
    base.update(overrides)
    return Match(**base)


@pytest.fixture
def match() -> Match:
    return make_match()


def result(
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    home_goals: int,
    away_goals: int,
    *,
    days_ago: int = 0,
    competition_id: int = 2021,
    as_of: datetime | None = None,
) -> MatchResult:
    """One finished match, `days_ago` before `as_of` (default: the fixed kickoff)."""
    anchor = as_of or KICKOFF
    return MatchResult(
        match_id=match_id,
        competition_id=competition_id,
        utc_date=anchor - timedelta(days=days_ago),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_goals=home_goals,
        away_goals=away_goals,
    )


@pytest.fixture
def history() -> list[MatchResult]:
    """A small round-robin: team 1 strong, team 3 weak, team 2 in between."""
    return [
        result(1, 1, 2, 3, 0, days_ago=90),
        result(2, 2, 3, 2, 1, days_ago=80),
        result(3, 3, 1, 0, 2, days_ago=70),
        result(4, 1, 3, 4, 0, days_ago=60),
        result(5, 2, 1, 1, 1, days_ago=50),
        result(6, 3, 2, 0, 1, days_ago=40),
    ]
