import logging

import asyncpg

from app.db_model import Match

log = logging.getLogger("brain.db")


async def fetch_match_via_id(match_id: int, pool: asyncpg.Pool) -> Match | None:
    QUERY = """
        SELECT m.id, m.season_id, m.home_team_id, m.away_team_id, ht.name AS home_team, 
        at.name AS away_team, m.matchday, m.utc_date, m.status, m.home_goals, m.away_goals, 
        m.fulltime_outcome
        FROM a_game.match AS m
        INNER JOIN a_game.team AS ht ON m.home_team_id = ht.id
        INNER JOIN a_game.team AS at ON m.away_team_id = at.id
        WHERE m.id = $1
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(QUERY, match_id)

    if row is None:
        log.warning("Match %d not found", match_id)

    return Match(**row) if row else None
