"""Persistence layer (`app/db.py`), driven through fake asyncpg objects.

No Postgres here. What these tests pin down is the *logic wrapped around* the SQL
— the change-gate counting, the outbox write, the write ordering the foreign keys
demand, and the payload-to-column mapping. The SQL itself is verified by the
integration path (the real cluster) and by the DDL's own constraints.

The one thing worth stating plainly: `fetchrow` returning a row means the upsert's
WHERE clause matched and RETURNING produced an id, i.e. something actually
changed. Returning None means the row was already current. Every counter in this
module hangs off that distinction (ADR 0007 §3).
"""

import json

from app import db
from tests.conftest import FakeConnection, FakePool

# --------------------------------------------------------------------------
# sync_matches_per_competition — the daily path
# --------------------------------------------------------------------------


async def test_changed_match_is_counted_and_queues_an_event(match):
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    changed = await db.sync_matches_per_competition(pool, [match])

    assert changed == 1

    events = conn.executed_matching("rabbit_event")
    assert len(events) == 1
    assert events[0][1] == ("match.changed", match.id)


async def test_unchanged_match_counts_nothing_and_queues_nothing(match):
    """A no-op run must publish nothing — the whole point of the change gate.

    Without this, the 06:00 run fires a full Haiku rerun every day for fixtures
    whose data never moved.
    """
    conn = FakeConnection(fetchrow_results=[None])
    pool = FakePool(conn)

    changed = await db.sync_matches_per_competition(pool, [match])

    assert changed == 0
    assert conn.executed_matching("rabbit_event") == []


async def test_event_is_written_inside_the_match_transaction(match):
    """Outbox pattern: the event and the row it describes commit together.

    The fake transaction can't prove atomicity, but it can prove the event insert
    happens after the match upsert and within the same acquired connection —
    exactly one acquire for the pair.
    """
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match])

    assert pool.acquired == 1


async def test_season_and_teams_are_written_before_the_match(match):
    """FK order. match references season and both teams, so they must exist first."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match])

    kinds = [
        "season" if "a_game.season" in q else "team" if "a_game.team" in q else "other"
        for q, _ in conn.executed
    ]
    assert kinds[:3] == ["season", "team", "team"]
    assert len(conn.fetchrow_calls) == 1


async def test_team_crest_is_written_to_the_emblem_column(match):
    """The payload field is `crest`, the column is `emblem`. This mapping lives in
    db.py and nothing else enforces it."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match])

    team_args = [args for _, args in conn.executed_matching("a_game.team")]
    home = team_args[0]

    assert home[0] == match.homeTeam.id
    assert home[1] == "Manchester City FC"
    assert home[4] == "https://crests.football-data.org/65.png"


async def test_match_upsert_arguments_map_to_the_right_columns(match):
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match])

    _, args = conn.fetchrow_calls[0]

    assert args[0] == match.id
    assert args[1] == match.season.id
    assert args[2] == match.homeTeam.id
    assert args[3] == match.awayTeam.id
    assert args[4] == match.matchday
    assert args[6] == "FINISHED"
    assert args[8] == 2      # full-time home
    assert args[9] == 1      # full-time away
    assert args[10] == 1     # half-time home
    assert args[12] == "Michael Oliver"


async def test_blob_is_the_whole_payload_as_json(match):
    """The blob column is the insurance policy against the 10 req/min ceiling.

    It has to round-trip as JSON and keep the keys the model doesn't declare.
    """
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match])

    blob = json.loads(conn.fetchrow_calls[0][1][13])

    assert blob["id"] == match.id
    assert blob["lastUpdated"] == "2026-08-22T16:05:11Z"


async def test_scheduled_match_writes_nulls_and_no_referee(scheduled_match):
    conn = FakeConnection(fetchrow_results=[{"id": scheduled_match.id}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [scheduled_match])

    _, args = conn.fetchrow_calls[0]

    assert args[6] == "SCHEDULED"
    assert args[8] is None    # full-time home
    assert args[9] is None    # full-time away
    assert args[12] is None   # referee


async def test_counts_only_the_matches_that_changed(match, scheduled_match):
    """Two fetched, one changed."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}, None])
    pool = FakePool(conn)

    changed = await db.sync_matches_per_competition(pool, [match, scheduled_match])

    assert changed == 1
    assert len(conn.executed_matching("rabbit_event")) == 1


async def test_empty_match_list_touches_nothing():
    conn = FakeConnection()
    pool = FakePool(conn)

    assert await db.sync_matches_per_competition(pool, []) == 0
    assert conn.executed == []
    assert pool.acquired == 0


async def test_each_match_gets_its_own_transaction(match, scheduled_match):
    """One acquire per match — a bad fixture rolls back only itself."""
    conn = FakeConnection(fetchrow_results=[{"id": 1}, {"id": 2}])
    pool = FakePool(conn)

    await db.sync_matches_per_competition(pool, [match, scheduled_match])

    assert pool.acquired == 2


# --------------------------------------------------------------------------
# sync_historic_matches — the backfill path
# --------------------------------------------------------------------------


async def test_historic_backfill_counts_the_matches_it_changed(match):
    """Same change-gate contract as the daily path: a returned row means the
    upsert wrote something, and that is what `changed` counts."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    changed = await db.sync_historic_matches(pool, [match])

    assert changed == 1


async def test_historic_backfill_does_not_count_unchanged_rows(match):
    conn = FakeConnection(fetchrow_results=[None])
    pool = FakePool(conn)

    assert await db.sync_historic_matches(pool, [match]) == 0


async def test_historic_backfill_queues_no_events(match):
    """Backfill is bulk history — it must not fan out thousands of Haiku jobs."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_historic_matches(pool, [match])

    assert conn.executed_matching("rabbit_event") == []


async def test_historic_backfill_never_overwrites_a_season(match):
    """DO NOTHING, not DO UPDATE. sync_competitions owns season data; this only
    guarantees the FK target exists."""
    conn = FakeConnection(fetchrow_results=[{"id": match.id}])
    pool = FakePool(conn)

    await db.sync_historic_matches(pool, [match])

    season_sql = conn.executed_matching("a_game.season")[0][0]

    assert "DO NOTHING" in season_sql


async def test_historic_backfill_runs_in_one_transaction(match, scheduled_match):
    """Unlike the daily path, the whole batch shares a transaction."""
    conn = FakeConnection(fetchrow_results=[{"id": 1}, {"id": 2}])
    pool = FakePool(conn)

    await db.sync_historic_matches(pool, [match, scheduled_match])

    assert pool.acquired == 1


# --------------------------------------------------------------------------
# Outbox drain
# --------------------------------------------------------------------------


async def test_fetch_rabbit_events_returns_the_rows():
    rows = [{"id": 1, "match_id": 538107}, {"id": 2, "match_id": 538108}]
    conn = FakeConnection(fetch_results=rows)
    pool = FakePool(conn)

    assert await db.fetch_rabbit_events(pool) == rows


async def test_fetch_rabbit_events_is_ordered_oldest_first():
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_rabbit_events(pool)

    assert "ORDER BY id" in conn.executed[0][0]


async def test_fetch_rabbit_events_empty_is_not_an_error():
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    assert await db.fetch_rabbit_events(pool) == []


async def test_delete_rabbit_event_passes_the_id_array():
    conn = FakeConnection()
    pool = FakePool(conn)

    await db.delete_rabbit_event(pool, [1, 2, 3])

    query, args = conn.executed[0]
    assert "DELETE" in query
    assert args == ([1, 2, 3],)


# --------------------------------------------------------------------------
# Competition / season sync and league selection
# --------------------------------------------------------------------------


async def test_get_league_codes_returns_only_enabled():
    conn = FakeConnection(fetch_results=[{"code": "PL"}])
    pool = FakePool(conn)

    codes = await db.get_league_codes(pool)

    assert codes == ["PL"]
    assert "WHERE enabled" in conn.executed[0][0]


async def test_get_league_codes_empty_when_nothing_enabled():
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    assert await db.get_league_codes(pool) == []


async def test_sync_competitions_upserts_competition_and_season():
    conn = FakeConnection()
    pool = FakePool(conn)

    count = await db.sync_competitions(
        pool,
        [
            {
                "id": 2021,
                "name": "Premier League",
                "code": "PL",
                "type": "LEAGUE",
                "emblem": "https://crests.football-data.org/PL.png",
                "currentSeason": {
                    "id": 2502,
                    "startDate": "2026-08-21",
                    "endDate": "2027-05-30",
                },
            }
        ],
    )

    assert count == 1
    assert len(conn.executed_matching("a_game.competition")) == 1

    season_args = conn.executed_matching("a_game.season")[0][1]
    assert season_args[0] == 2502
    assert season_args[1] == 2021
    assert str(season_args[2]) == "2026-08-21"


async def test_sync_competitions_never_writes_the_enabled_column():
    """`enabled` is operator state. If the upsert touched it, every daily run
    would reset which leagues you'd chosen to ingest (schema.md §2)."""
    conn = FakeConnection()
    pool = FakePool(conn)

    await db.sync_competitions(
        pool, [{"id": 2021, "name": "PL", "code": "PL", "type": "LEAGUE"}]
    )

    for query, _ in conn.executed:
        assert "enabled" not in query


async def test_sync_competitions_skips_a_competition_with_no_current_season():
    """The World Cup and the Euros have a `currentSeason` that may be missing
    dates. Skipping the season write beats inserting a null-dated row."""
    conn = FakeConnection()
    pool = FakePool(conn)

    await db.sync_competitions(
        pool, [{"id": 2000, "name": "FIFA World Cup", "code": "WC", "type": "CUP"}]
    )

    assert len(conn.executed_matching("a_game.competition")) == 1
    assert conn.executed_matching("a_game.season") == []


async def test_sync_competitions_skips_a_season_missing_its_dates():
    conn = FakeConnection()
    pool = FakePool(conn)

    await db.sync_competitions(
        pool,
        [
            {
                "id": 2001,
                "name": "UEFA Champions League",
                "code": "CL",
                "type": "CUP",
                "currentSeason": {"id": 2454, "startDate": None, "endDate": None},
            }
        ],
    )

    assert conn.executed_matching("a_game.season") == []


async def test_sync_competitions_tolerates_a_missing_emblem():
    """`.get("emblem")` rather than `["emblem"]` — some competitions have none."""
    conn = FakeConnection()
    pool = FakePool(conn)

    await db.sync_competitions(
        pool, [{"id": 2152, "name": "Copa Libertadores", "code": "CLI", "type": "CUP"}]
    )

    assert conn.executed_matching("a_game.competition")[0][1][4] is None
