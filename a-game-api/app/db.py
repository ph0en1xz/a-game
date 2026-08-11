import logging

import asyncpg

log = logging.getLogger("api.db")


async def get_prediction(conn: asyncpg.Connection, match_id: int) -> str | None:
    """Fetch the prediction commentary text for a given match.

    Args:
        conn: An asyncpg.Connection instance connected to the database.
        match_id: The numeric identifier for the match whose prediction is requested.

    Returns:
        The prediction text from the commentary table if a row exists, otherwise None.

    Notes:
        If no commentary row exists for the given match_id, the function logs a warning
        and returns None.
    """

    QUERY = """
        SELECT prediction from a_game.commentary where match_id = $1
    """

    row = await conn.fetchrow(QUERY, match_id)
    if row is not None:
        return row["prediction"]

    log.warning("No prediction row exists in commentary table with match id of %d", match_id)
    return None