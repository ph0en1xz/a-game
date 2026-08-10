import json
import logging
from datetime import datetime

import asyncpg  # type: ignore

from app.db_model import Match
from app.engine.elo import MatchResult
from app.engine.poisson import ScoreProbabilities

log = logging.getLogger("brain.db")


async def fetch_match_history(
    competition_id: int, pool: asyncpg.Pool, before: datetime | None = None
) -> list[MatchResult]:
    """Every finished match in one competition, as the engine's input shape.

    This is the training data. Both `elo.run` and `poisson.fit` take the list
    this returns and derive everything from it - there is no persisted model,
    so this query IS the training set (ADR 0010 §4, three seasons).

    Args:
        competition_id (int): Ratings and strengths are competition-scoped
            (§2), so the history must be too.
        pool (asyncpg.Pool): The PostgreSQL connection pool.
        before (datetime | None): When set, only matches played strictly before
            this instant are returned. The live path leaves it None; the
            walk-forward backtest (§17) passes the matchday being predicted,
            and that argument is the only thing standing between the protocol
            and a future leak.

    Returns:
        list[MatchResult]: Finished matches with both goal columns populated,
        oldest first. Fixtures without a result are excluded here rather than
        by the caller - a NULL score has nothing to teach either model.
    """
    QUERY = """
        SELECT m.id, s.competition_id, m.utc_date, m.home_team_id, m.away_team_id,
        m.home_goals, m.away_goals
        FROM a_game.match AS m
        INNER JOIN a_game.season AS s ON m.season_id = s.id
        WHERE s.competition_id = $1
          AND m.status = 'FINISHED'
          AND m.home_goals IS NOT NULL
          AND m.away_goals IS NOT NULL
          AND ($2::TIMESTAMPTZ IS NULL OR m.utc_date < $2)
        ORDER BY m.utc_date, m.id
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(QUERY, competition_id, before)

    log.info("Loaded %d finished matches for competition %d", len(rows), competition_id)

    return [
        MatchResult(
            match_id=row["id"],
            competition_id=row["competition_id"],
            utc_date=row["utc_date"],
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            home_goals=row["home_goals"],
            away_goals=row["away_goals"],
        )
        for row in rows
    ]


async def store_prediction(
    conn: asyncpg.Connection,
    match_id: int,
    engine_version: str,
    probabilities: ScoreProbabilities,
    elo_home: float,
    elo_away: float,
) -> None:
    """Persist one fixture's numbers for one engine version.

    Takes a connection, not the pool, so this and store_commentary land in the
    same transaction - a fixture with prose but no probabilities is a broken row.

    The upsert targets (match_id, engine_version): recomputing under the same
    version overwrites, while a version bump inserts alongside instead of
    replacing. That is what keeps ADR 0010 §16's "beat the previous version"
    comparison possible.

    Args:
        conn (asyncpg.Connection): An acquired connection, inside the caller's
            transaction.
        match_id (int): The fixture being predicted.
        engine_version (str): app.engine.params.ENGINE_VERSION.
        probabilities (ScoreProbabilities): Output of score_probabilities().
        elo_home (float): Home rating at kickoff.
        elo_away (float): Away rating at kickoff.
    """
    QUERY = """
        INSERT INTO a_game.prediction (
            match_id, engine_version, prob_home, prob_draw, prob_away,
            over_2_5, btts, lambda_home, lambda_away, most_likely_scores,
            elo_home, elo_away
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (match_id, engine_version) DO UPDATE SET
            prob_home          = excluded.prob_home,
            prob_draw          = excluded.prob_draw,
            prob_away          = excluded.prob_away,
            over_2_5           = excluded.over_2_5,
            btts               = excluded.btts,
            lambda_home        = excluded.lambda_home,
            lambda_away        = excluded.lambda_away,
            most_likely_scores = excluded.most_likely_scores,
            elo_home           = excluded.elo_home,
            elo_away           = excluded.elo_away,
            computed_at        = NOW()
    """

    try:
        await conn.execute(
            QUERY,
            match_id,
            engine_version,
            # Rounded to the column's own scale. Postgres would round anyway;
            # doing it here means the CHECK that the three sum to 1 is applied
            # to the values actually stored, not to their fuller originals.
            round(probabilities.prob_home, 4),
            round(probabilities.prob_draw, 4),
            round(probabilities.prob_away, 4),
            round(probabilities.over_2_5, 4),
            round(probabilities.btts, 4),
            round(probabilities.lambda_home, 4),
            round(probabilities.lambda_away, 4),
            json.dumps(probabilities.most_likely_scores),
            round(elo_home, 2),
            round(elo_away, 2),
        )
        log.info(
            "Stored prediction for match %d (engine %s)", match_id, engine_version
        )

    except Exception as e:
        log.error("Failed to store prediction for match %d: %s", match_id, str(e))
        raise


async def store_commentary(
    conn: asyncpg.Connection,
    match_id: int,
    source_model: str,
    prediction: str,
    suggested_bet: str = "",
    suggested_bet_reason: str = "",
) -> None:
    """Persist the preview for one fixture, overwriting any earlier one.

    Takes a connection rather than the pool so the caller controls the
    transaction: once predictions are written too, the preview and its numbers
    must land together or not at all.

    Args:
        conn (asyncpg.Connection): An acquired connection, inside a transaction
            if the caller has one open.
        match_id (int): The fixture the preview belongs to.
        source_model (str): The model that produced the preview, not the alias, so a fallback is recorded honestly.
        prediction (str): The preview prose.
        suggested_bet (str): The market the model called, already validated
            against SUGGESTED_BETS by the Commentary model. Empty when it
            declined to pick one.
        suggested_bet_reason (str): One sentence justifying that call.
    """
    QUERY = """
        INSERT INTO a_game.commentary (
            match_id, source_model, prediction, suggested_bet, suggested_bet_reason
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (match_id) DO
        UPDATE SET
            source_model = excluded.source_model,
            prediction = excluded.prediction,
            suggested_bet = excluded.suggested_bet,
            suggested_bet_reason = excluded.suggested_bet_reason,
            updated_at = NOW()
    """

    try:
        await conn.execute(
            QUERY,
            match_id,
            source_model,
            prediction,
            suggested_bet,
            suggested_bet_reason)
        log.info(
            "Stored commentary for match %d with model %s (bet=%s)",
            match_id,
            source_model,
            suggested_bet or "none",
        )

    except Exception as e:
        log.error("Failed to store commentary for match %d: %s", match_id, str(e))
        raise


async def fetch_match_via_id(match_id: int, pool: asyncpg.Pool) -> Match | None:
    QUERY = """
        SELECT m.id, m.season_id, s.competition_id, m.home_team_id, m.away_team_id,
        ht.name AS home_team, at.name AS away_team, m.matchday, m.utc_date, m.status,
        m.home_goals, m.away_goals, m.fulltime_outcome
        FROM a_game.match AS m
        INNER JOIN a_game.season AS s ON m.season_id = s.id
        INNER JOIN a_game.team AS ht ON m.home_team_id = ht.id
        INNER JOIN a_game.team AS at ON m.away_team_id = at.id
        WHERE m.id = $1
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(QUERY, match_id)

    if row is None:
        log.warning("Match %d not found", match_id)

    return Match(**row) if row else None
