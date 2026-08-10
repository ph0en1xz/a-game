"""Persistence layer (`app/db.py`), driven through fake asyncpg objects.

No Postgres. What's checked is the logic around the SQL: the training query's
filters, the argument-to-column mapping, the rounding that keeps the sum-to-1
CHECK satisfiable, and the fact that failures are re-raised rather than logged and
swallowed — the transaction in `process_job` only works if these propagate.
"""

import json
from datetime import UTC, datetime

import pytest

from app import db
from app.engine import poisson
from tests.conftest import FakeConnection, FakePool

PROBS = poisson.score_probabilities(1.31, 1.21)


def history_row(match_id=1, competition_id=2021, **overrides):
    row = {
        "id": match_id,
        "competition_id": competition_id,
        "utc_date": datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
        "home_team_id": 65,
        "away_team_id": 57,
        "home_goals": 2,
        "away_goals": 1,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# fetch_match_history — the training set
# --------------------------------------------------------------------------


async def test_history_maps_rows_to_match_results():
    conn = FakeConnection(fetch_results=[history_row(1), history_row(2)])
    pool = FakePool(conn)

    results = await db.fetch_match_history(2021, pool)

    assert [r.match_id for r in results] == [1, 2]
    assert results[0].home_goals == 2
    assert results[0].competition_id == 2021


async def test_history_excludes_unplayed_fixtures():
    """A NULL score has nothing to teach either model, so the filtering happens in
    SQL rather than in the caller."""
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_match_history(2021, pool)

    query = conn.executed[0][0]
    assert "status = 'FINISHED'" in query
    assert "home_goals IS NOT NULL" in query
    assert "away_goals IS NOT NULL" in query


async def test_history_is_competition_scoped():
    """Ratings and strengths are keyed by competition, so the history must be too."""
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_match_history(2021, pool)

    query, args = conn.executed[0]
    assert "s.competition_id = $1" in query
    assert args[0] == 2021


async def test_history_is_ordered_oldest_first():
    """Elo's replay is path-dependent; an unordered training set is a different
    model every time."""
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_match_history(2021, pool)

    assert "ORDER BY m.utc_date, m.id" in conn.executed[0][0]


async def test_the_before_cutoff_is_passed_through():
    """This argument is the only thing standing between the walk-forward backtest
    and a future leak."""
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_match_history(2021, pool, before=cutoff)

    _, args = conn.executed[0]
    assert args[1] == cutoff


async def test_no_cutoff_means_the_whole_history():
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    await db.fetch_match_history(2021, pool)

    query, args = conn.executed[0]
    assert args[1] is None
    assert "$2::TIMESTAMPTZ IS NULL" in query


async def test_an_empty_history_is_not_an_error():
    """A brand-new competition. `fit` and `run` both handle it; this must not
    raise on the way there."""
    conn = FakeConnection(fetch_results=[])
    pool = FakePool(conn)

    assert await db.fetch_match_history(9999, pool) == []


# --------------------------------------------------------------------------
# store_prediction
# --------------------------------------------------------------------------


async def test_prediction_arguments_map_to_the_right_columns():
    conn = FakeConnection()

    await db.store_prediction(conn, 538107, "0.1.0", PROBS, 1643.2, 1677.8)

    _, args = conn.executed[0]
    assert args[0] == 538107
    assert args[1] == "0.1.0"
    assert args[2] == round(PROBS.prob_home, 4)
    assert args[3] == round(PROBS.prob_draw, 4)
    assert args[4] == round(PROBS.prob_away, 4)


async def test_probabilities_are_rounded_to_the_column_scale():
    """Postgres would round on insert anyway. Doing it here means the sum-to-1
    CHECK is applied to the values actually stored, not to fuller originals that
    happen to add up."""
    conn = FakeConnection()

    await db.store_prediction(conn, 1, "0.1.0", PROBS, 1500.0, 1500.0)

    _, args = conn.executed[0]
    for value in args[2:9]:
        assert value == round(float(value), 4)


async def test_the_rounded_probabilities_still_sum_to_one():
    """The CHECK allows 0.0005 of slack; this is the assertion it encodes."""
    conn = FakeConnection()

    await db.store_prediction(conn, 1, "0.1.0", PROBS, 1500.0, 1500.0)

    _, args = conn.executed[0]
    assert abs(args[2] + args[3] + args[4] - 1) <= 0.0005


async def test_elo_ratings_are_rounded_to_two_places():
    conn = FakeConnection()

    await db.store_prediction(conn, 1, "0.1.0", PROBS, 1643.216, 1677.849)

    _, args = conn.executed[0]
    assert args[10] == 1643.22
    assert args[11] == 1677.85


async def test_most_likely_scores_are_serialised_as_json():
    conn = FakeConnection()

    await db.store_prediction(conn, 1, "0.1.0", PROBS, 1500.0, 1500.0)

    _, args = conn.executed[0]
    decoded = json.loads(args[9])
    assert len(decoded) == 3
    assert "home" in decoded[0]


async def test_prediction_upserts_on_match_and_version():
    """A recompute under the same version overwrites; a version bump inserts
    alongside — that is what keeps the calibration comparison possible."""
    conn = FakeConnection()

    await db.store_prediction(conn, 1, "0.1.0", PROBS, 1500.0, 1500.0)

    query = conn.executed[0][0]
    assert "ON CONFLICT (match_id, engine_version)" in query
    assert "computed_at        = NOW()" in query


async def test_a_failed_prediction_write_raises():
    """It must not be logged and swallowed: `process_job` wraps both writes in one
    transaction, and a swallowed error would commit a half-written fixture."""
    conn = FakeConnection(execute_error=("a_game.prediction", RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await db.store_prediction(conn, 1, "0.1.0", PROBS, 1500.0, 1500.0)


# --------------------------------------------------------------------------
# store_commentary
# --------------------------------------------------------------------------


async def test_commentary_arguments_map_to_the_right_columns():
    conn = FakeConnection()

    await db.store_commentary(
        conn, 538107, "anthropic/claude-haiku", "prose", "Draw", "because"
    )

    _, args = conn.executed[0]
    assert args == (538107, "anthropic/claude-haiku", "prose", "Draw", "because")


async def test_commentary_suggested_bet_defaults_to_empty():
    """Older callers pass three arguments; the columns are NOT NULL DEFAULT ''."""
    conn = FakeConnection()

    await db.store_commentary(conn, 538107, "model", "prose")

    _, args = conn.executed[0]
    assert args[3] == ""
    assert args[4] == ""


async def test_commentary_upserts_on_match_id_alone():
    """One current preview per fixture, overwritten on recompute — unlike
    prediction, which keys on the engine version too."""
    conn = FakeConnection()

    await db.store_commentary(conn, 1, "model", "prose")

    query = conn.executed[0][0]
    assert "ON CONFLICT (match_id)" in query
    assert "updated_at = NOW()" in query


async def test_the_upsert_refreshes_the_suggested_bet():
    """A recompute that produces a different market must not leave the old one
    behind next to new prose."""
    conn = FakeConnection()

    await db.store_commentary(conn, 1, "model", "prose", "Draw", "reason")

    query = conn.executed[0][0]
    assert "suggested_bet = excluded.suggested_bet" in query
    assert "suggested_bet_reason = excluded.suggested_bet_reason" in query


async def test_a_failed_commentary_write_raises():
    conn = FakeConnection(execute_error=("a_game.commentary", RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await db.store_commentary(conn, 1, "model", "prose")


# --------------------------------------------------------------------------
# fetch_match_via_id
# --------------------------------------------------------------------------


async def test_fetch_match_returns_a_model():
    conn = FakeConnection(
        fetchrow_result={
            "id": 538107,
            "season_id": 2502,
            "competition_id": 2021,
            "home_team_id": 65,
            "away_team_id": 57,
            "home_team": "Manchester City FC",
            "away_team": "Arsenal FC",
            "matchday": 2,
            "utc_date": datetime(2026, 8, 22, 14, 0, tzinfo=UTC),
            "status": "SCHEDULED",
            "home_goals": None,
            "away_goals": None,
            "fulltime_outcome": None,
        }
    )
    pool = FakePool(conn)

    match = await db.fetch_match_via_id(538107, pool)

    assert match.id == 538107
    assert match.competition_id == 2021
    assert match.home_team == "Manchester City FC"


async def test_fetch_match_returns_none_when_missing():
    """An unknown id is a normal outcome, not an exception — the consumer treats
    it as nothing to do."""
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    assert await db.fetch_match_via_id(999, pool) is None


async def test_fetch_match_joins_in_the_competition():
    """`match` has no competition_id of its own — it's reachable via season, and
    the engine needs it to key ratings and strengths."""
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    await db.fetch_match_via_id(1, pool)

    query = conn.executed[0][0]
    assert "s.competition_id" in query
    assert "JOIN a_game.season" in query
