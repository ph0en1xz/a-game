import logging

import asyncpg

log = logging.getLogger("brain.db")


async def fetch_match_via_id(match_id: int, pool: asyncpg.Pool) -> asyncpg.Record | None:
    # Columns named rather than SELECT *: blob holds the entire raw football-data
    # payload and nothing downstream reads it.
    QUERY = """
        SELECT id, season_id, home_team_id, away_team_id, matchday,
               utc_date, status, home_goals, away_goals, fulltime_outcome
        FROM a_game.match
        WHERE id = $1
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(QUERY, match_id)

    if row is None:
        log.warning("Match %d not found", match_id)

    return row
