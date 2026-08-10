"""The one-off history bootstrap (`app/backfill.py`).

Run by hand, not by the CronJob, and it writes the three seasons both engine
models train on — so a silent failure here shows up much later as a bad Brier
score rather than as an error.

Unlike `main`, this one swallows exceptions and logs them. That's a deliberate
difference for a manual script, and the tests pin it down so nobody "fixes" it
into a re-raise without meaning to.
"""

import asyncpg
import pytest

from app import backfill
from app.model import Match
from tests.conftest import FakeConnection, FakePool, match_payload


@pytest.fixture
def run(monkeypatch):
    def _configure(*, league_codes=("PL",), matches=None, synced=380):
        state = {"calls": [], "synced_with": None, "closed": []}

        async def fake_create_pool(**kwargs):
            class _Pool(FakePool):
                async def close(self):
                    state["closed"].append("pool")

            return _Pool(FakeConnection())

        class FakeHTTPClient:
            async def aclose(self):
                state["closed"].append("client")

        def fake_make_client():
            return FakeHTTPClient()

        async def fake_get_league_codes(_pool):
            state["calls"].append("get_league_codes")
            return list(league_codes)

        async def fake_get_historic(_client, codes):
            state["calls"].append("get_historic_matches")
            return list(matches) if matches is not None else None

        async def fake_sync(_pool, ms):
            state["calls"].append("sync_historic_matches")
            state["synced_with"] = list(ms)
            return synced

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        monkeypatch.setattr(backfill, "make_client", fake_make_client)
        monkeypatch.setattr(backfill, "get_league_codes", fake_get_league_codes)
        monkeypatch.setattr(backfill, "get_historic_matches", fake_get_historic)
        monkeypatch.setattr(backfill, "sync_historic_matches", fake_sync)

        return state

    return _configure


async def test_happy_path_syncs_what_it_fetched(run):
    fetched = [Match.model_validate(match_payload())]
    state = run(matches=fetched)

    await backfill.main()

    assert state["calls"] == [
        "get_league_codes",
        "get_historic_matches",
        "sync_historic_matches",
    ]
    assert state["synced_with"] == fetched


async def test_stops_when_no_league_is_enabled(run):
    """Nothing enabled means nothing to backfill — return before spending any of
    the free tier's request budget."""
    state = run(league_codes=())

    await backfill.main()

    assert state["calls"] == ["get_league_codes"]


async def test_stops_when_the_api_returns_nothing(run):
    """`get_historic_matches` returns None, not an empty list, when it found
    nothing — including after it broke on a 403 for a season outside the plan."""
    state = run(matches=None)

    await backfill.main()

    assert "sync_historic_matches" not in state["calls"]


async def test_stops_on_an_empty_match_list(run):
    state = run(matches=[])

    await backfill.main()

    assert "sync_historic_matches" not in state["calls"]


async def test_both_resources_are_closed_on_the_happy_path(run):
    state = run(matches=[Match.model_validate(match_payload())])

    await backfill.main()

    assert set(state["closed"]) == {"client", "pool"}


async def test_both_resources_are_closed_on_an_early_return(run):
    """The early returns sit inside the try, so `finally` still runs. Worth
    asserting — a `return` before the try would leak both."""
    state = run(league_codes=())

    await backfill.main()

    assert set(state["closed"]) == {"client", "pool"}


async def test_errors_are_logged_and_swallowed(run, monkeypatch, caplog):
    """Deliberately different from `main`, which lets exceptions escape.

    This is a manual script; failing loudly in the log and exiting 0 is fine.
    What is not fine is leaking the pool, so that is asserted too.
    """
    state = run(matches=[Match.model_validate(match_payload())])

    async def boom(_pool, _matches):
        raise RuntimeError("postgres gone")

    monkeypatch.setattr(backfill, "sync_historic_matches", boom)

    await backfill.main()  # must not raise

    assert set(state["closed"]) == {"client", "pool"}
    assert "Error occurred while fetching historic matches" in caplog.text
