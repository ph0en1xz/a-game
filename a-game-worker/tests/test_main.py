"""The daily run's orchestration (`app/main.py`).

This is the file that actually broke in the cluster: the deployed image predated
phase 2, so every run logged "Synced 13 competitions" and exited 0 having fetched
no matches at all. A green exit code proved nothing. These tests assert the shape
of a run — that phase 2 happens, that it uses the enabled leagues, and that the
outbox is drained only after a successful publish.

Every collaborator is monkeypatched at the `app.main` reference, not at its home
module, because `main` imports them as `sports_client` / `postgres` / `rabbitmq`
aliases and patching the source module would leave the alias pointing at the real
function.
"""

import asyncpg
import pytest

from app import main as worker_main
from tests.conftest import FakeConnection, FakePool, match_payload


class Recorder:
    """Collects the call order across all the patched collaborators."""

    def __init__(self):
        self.calls: list[str] = []
        self.published: list[list[int]] = []
        self.deleted: list[list[int]] = []
        self.synced_matches: list[list] = []


class FakeHTTPClient:
    def __init__(self, recorder):
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._recorder.calls.append("http_client_closed")
        return False


@pytest.fixture
def run(monkeypatch):
    """Wire `main` to in-process doubles and return the recorder.

    Returns a callable so each test can vary what the fakes hand back.
    """

    def _configure(
        *,
        competitions=None,
        league_codes=("PL",),
        matches=None,
        events=(),
        changed=1,
    ):
        rec = Recorder()
        pool = FakePool(FakeConnection())

        async def fake_create_pool(**kwargs):
            rec.calls.append("create_pool")

            class _Pool(FakePool):
                async def close(self):
                    rec.calls.append("pool_closed")

            p = _Pool(FakeConnection())
            return p

        def fake_make_client():
            rec.calls.append("make_client")
            return FakeHTTPClient(rec)

        async def fake_get_all_competitions(client):
            rec.calls.append("get_all_competitions")
            return list(competitions or [{"id": 2021, "code": "PL"}])

        async def fake_sync_competitions(_pool, comps):
            rec.calls.append("sync_competitions")
            return len(comps)

        async def fake_get_league_codes(_pool):
            rec.calls.append("get_league_codes")
            return list(league_codes)

        async def fake_get_matches(codes, client):
            rec.calls.append(f"get_matches:{','.join(codes)}")
            return list(matches or [])

        async def fake_sync_matches(_pool, ms):
            rec.calls.append("sync_matches")
            rec.synced_matches.append(list(ms))
            return changed

        async def fake_fetch_events(_pool):
            rec.calls.append("fetch_rabbit_events")
            return list(events)

        async def fake_run_producer(ids):
            rec.calls.append("run_producer")
            rec.published.append(list(ids))

        async def fake_delete_events(_pool, ids):
            rec.calls.append("delete_rabbit_event")
            rec.deleted.append(list(ids))

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        monkeypatch.setattr(worker_main.sports_client, "make_client", fake_make_client)
        monkeypatch.setattr(
            worker_main.sports_client, "get_all_competitions", fake_get_all_competitions
        )
        monkeypatch.setattr(
            worker_main.sports_client,
            "get_matches_per_competition",
            fake_get_matches,
        )
        monkeypatch.setattr(
            worker_main.postgres, "sync_competitions", fake_sync_competitions
        )
        monkeypatch.setattr(
            worker_main.postgres, "get_league_codes", fake_get_league_codes
        )
        monkeypatch.setattr(
            worker_main.postgres, "sync_matches_per_competition", fake_sync_matches
        )
        monkeypatch.setattr(
            worker_main.postgres, "fetch_rabbit_events", fake_fetch_events
        )
        monkeypatch.setattr(
            worker_main.postgres, "delete_rabbit_event", fake_delete_events
        )
        monkeypatch.setattr(worker_main.rabbitmq, "run_producer", fake_run_producer)

        _ = pool  # the real pool comes from fake_create_pool
        return rec

    return _configure


async def test_a_run_reaches_phase_two(run):
    """The regression that cost a day: the deployed image stopped after the
    competition sync and never fetched a single match."""
    rec = run()

    await worker_main.main()

    assert "get_league_codes" in rec.calls
    assert any(c.startswith("get_matches:") for c in rec.calls)
    assert "sync_matches" in rec.calls


async def test_phases_run_in_order(run):
    """Competitions before leagues before matches — season rows are FK targets
    and `enabled` is read from the table phase 1 just wrote."""
    rec = run()

    await worker_main.main()

    order = [c for c in rec.calls if c in {
        "get_all_competitions", "sync_competitions", "get_league_codes", "sync_matches"
    }]
    assert order == [
        "get_all_competitions",
        "sync_competitions",
        "get_league_codes",
        "sync_matches",
    ]


async def test_only_enabled_leagues_are_fetched(run):
    """`enabled` is how you widen ingestion without a deploy. If main ignored it
    and fetched all 13, the free tier's 10 req/min would start 429ing."""
    rec = run(league_codes=("PL", "PD"))

    await worker_main.main()

    assert "get_matches:PL,PD" in rec.calls


async def test_no_enabled_leagues_still_completes(run):
    """An empty selection is a valid state, not a crash."""
    rec = run(league_codes=())

    await worker_main.main()

    assert "get_matches:" in rec.calls
    assert "pool_closed" in rec.calls


async def test_pending_events_are_published_then_deleted(run):
    """Outbox drain, in that order. Deleting first would lose the job on a broker
    failure and the fixture would never get a prediction."""
    rec = run(events=[{"id": 1, "match_id": 538107}, {"id": 2, "match_id": 538108}])

    await worker_main.main()

    assert rec.published == [[538107, 538108]]
    assert rec.deleted == [[1, 2]]
    assert rec.calls.index("run_producer") < rec.calls.index("delete_rabbit_event")


async def test_no_events_publishes_nothing(run):
    """A no-change run: the gate found nothing, so the outbox is empty and the
    broker is never touched."""
    rec = run(events=[], changed=0)

    await worker_main.main()

    assert rec.published == []
    assert rec.deleted == []
    assert "run_producer" not in rec.calls


async def test_publish_failure_leaves_the_outbox_intact(run, monkeypatch):
    """If publishing raises, the rows must survive for the next run to retry.

    This is the whole reason the outbox exists — an event deleted without being
    delivered is a fixture silently dropped.
    """
    rec = run(events=[{"id": 1, "match_id": 538107}])

    async def boom(_ids):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(worker_main.rabbitmq, "run_producer", boom)

    with pytest.raises(ConnectionError):
        await worker_main.main()

    assert rec.deleted == []


async def test_pool_is_closed_even_when_a_phase_raises(run, monkeypatch):
    """The `finally` matters: a CronJob pod that leaks connections poisons the
    next run against Postgres's connection limit."""
    rec = run()

    async def boom(_pool, _comps):
        raise RuntimeError("postgres gone")

    monkeypatch.setattr(worker_main.postgres, "sync_competitions", boom)

    with pytest.raises(RuntimeError):
        await worker_main.main()

    assert "pool_closed" in rec.calls


async def test_http_client_is_closed_before_the_match_sync(run):
    """`async with make_client()` closes at the end of phase 2's fetch, so the
    database write happens with no socket held open."""
    from app.model import Match

    rec = run(matches=[Match.model_validate(match_payload())])

    await worker_main.main()

    assert rec.calls.index("http_client_closed") < rec.calls.index("sync_matches")


async def test_fetched_matches_are_handed_to_the_sync(run):
    from app.model import Match

    fetched = [Match.model_validate(match_payload())]
    rec = run(matches=fetched)

    await worker_main.main()

    assert rec.synced_matches == [fetched]
