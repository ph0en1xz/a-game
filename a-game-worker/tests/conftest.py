"""Shared fixtures and fakes for the worker's unit tests.

Nothing here touches Postgres, RabbitMQ or football-data.org. The worker's I/O
is all injected — `pool`, `client` and the module-level `settings` — so the whole
service is testable with in-process doubles.

**The env block below must run before anything imports `app.*`.** `app.config`
builds `settings = Settings()` at module scope and `app.api_client` reads
`settings.sports_api_key` into `HEADERS` at import time, so a missing variable is
a collection error rather than a test failure. pytest imports conftest first,
which is what makes this placement work.
"""

import os

os.environ.setdefault("RABBITMQ_HOST", "test-rabbit")
os.environ.setdefault("RABBITMQ_PORT", "5672")
os.environ.setdefault("RABBITMQ_DEFAULT_USER", "test-user")
os.environ.setdefault("RABBITMQ_DEFAULT_PASS", "test-pass")
os.environ.setdefault("RABBITMQ_QUEUE", "test-queue")
os.environ.setdefault("POSTGRES_HOST", "test-postgres")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "test-pg-user")
os.environ.setdefault("POSTGRES_PASSWORD", "test-pg-pass")
os.environ.setdefault("POSTGRES_DB", "test-db")
os.environ.setdefault("SPORTS_API_KEY", "test-token")
os.environ.setdefault("SPORTS_API_URL", "https://api.football-data.org/v4")
os.environ.setdefault("SPORTS_COMPETITIONS_ENDPOINT", "competitions")
os.environ.setdefault(
    "SPORTS_COMPETITIONS_MATCHES_ENDPOINT", "competitions/{code}/matches"
)
os.environ.setdefault(
    "SPORTS_HISTORIC_MATCHES_ENDPOINT",
    "competitions/{league_name}/matches?season={season}&status=FINISHED",
)

import pytest

from app.model import Match

# --------------------------------------------------------------------------
# asyncpg doubles
# --------------------------------------------------------------------------


class _NullTransaction:
    """`conn.transaction()` — an async context manager that does nothing.

    Commit/rollback semantics are Postgres's job and can't be unit tested here.
    What these tests do check is what runs *inside* the block, and in what order.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Acquire:
    """`pool.acquire()` — async CM yielding the one fake connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    """Records every statement instead of running it.

    `fetchrow_results` is consumed in order, one per `fetchrow` call, and stands
    in for the change gate: a dict means the upsert's WHERE matched and RETURNING
    produced a row; None means it didn't. That distinction is the only thing the
    change-detection logic branches on (ADR 0007 §3).
    """

    def __init__(self, fetchrow_results=None, fetch_results=None):
        self.executed: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = fetch_results if fetch_results is not None else []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self._fetch_results

    def transaction(self):
        return _NullTransaction()

    # Convenience for assertions -------------------------------------------

    def statements(self) -> list[str]:
        """Every statement run, in order, execute and fetchrow interleaved."""
        return [q for q, _ in self.executed] + [q for q, _ in self.fetchrow_calls]

    def executed_matching(self, needle: str) -> list[tuple[str, tuple]]:
        return [(q, a) for q, a in self.executed if needle in q]


class FakePool:
    """`asyncpg.Pool` stand-in handing out the same connection every time."""

    def __init__(self, conn: FakeConnection):
        self.conn = conn
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return _Acquire(self.conn)


@pytest.fixture
def conn():
    return FakeConnection()


@pytest.fixture
def pool(conn):
    return FakePool(conn)


# --------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------


def match_payload(
    match_id: int = 538107,
    *,
    status: str = "FINISHED",
    home_goals: int | None = 2,
    away_goals: int | None = 1,
    referees: bool = True,
) -> dict:
    """A football-data match object, shaped like the real API response.

    Deliberately includes keys the model doesn't declare (`lastUpdated`, `odds`)
    — `_Payload` sets extra="allow" so the blob column stays a copy of the
    response rather than a lossy projection, and a test that only ever fed it
    known keys would never notice that breaking.
    """
    return {
        "id": match_id,
        "utcDate": "2026-08-22T14:00:00Z",
        "status": status,
        "matchday": 2,
        "stage": "REGULAR_SEASON",
        "group": None,
        "lastUpdated": "2026-08-22T16:05:11Z",
        "odds": {"msg": "Activate Odds-Package in User-Panel"},
        "area": {"id": 2072, "name": "England", "code": "ENG", "flag": None},
        "competition": {
            "id": 2021,
            "name": "Premier League",
            "code": "PL",
            "type": "LEAGUE",
            "emblem": "https://crests.football-data.org/PL.png",
        },
        "season": {
            "id": 2502,
            "startDate": "2026-08-21",
            "endDate": "2027-05-30",
            "currentMatchday": 2,
        },
        "homeTeam": {
            "id": 65,
            "name": "Manchester City FC",
            "shortName": "Man City",
            "tla": "MCI",
            "crest": "https://crests.football-data.org/65.png",
        },
        "awayTeam": {
            "id": 57,
            "name": "Arsenal FC",
            "shortName": "Arsenal",
            "tla": "ARS",
            "crest": "https://crests.football-data.org/57.png",
        },
        "score": {
            "winner": "HOME_TEAM" if status == "FINISHED" else None,
            "duration": "REGULAR",
            "fullTime": {"home": home_goals, "away": away_goals},
            "halfTime": {"home": 1, "away": 0} if status == "FINISHED" else {"home": None, "away": None},
        },
        "referees": (
            [{"id": 11580, "name": "Michael Oliver", "type": "REFEREE", "nationality": "England"}]
            if referees
            else []
        ),
    }


@pytest.fixture
def match() -> Match:
    return Match.model_validate(match_payload())


@pytest.fixture
def scheduled_match() -> Match:
    return Match.model_validate(
        match_payload(
            538108, status="SCHEDULED", home_goals=None, away_goals=None, referees=False
        )
    )
