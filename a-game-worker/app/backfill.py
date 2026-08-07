import asyncio
import logging

import asyncpg  # type: ignore

from app.api_client import get_historic_matches, make_client
from app.config import settings
from app.db import get_league_codes, sync_historic_matches
from app.model import Match

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker.backfill")


async def main():
    log.info("Starting the worker.backfill operation...")
    client = make_client()
    pool = await asyncpg.create_pool(dsn=settings.postgres_url, min_size=1, max_size=5)

    try:
        league_codes: list[str] = await get_league_codes(pool)
        if league_codes is None or len(league_codes) == 0:
            log.warning("No league codes found")
            return

        matches: list[Match] = await get_historic_matches(client, league_codes)
        if (matches is None) or (len(matches) == 0):
            log.info("No historic matches found")
            return

        records = await sync_historic_matches(pool, matches)
        if records:
            log.info("Synced %d historic matches", records)

    except Exception as e:
        log.error("Error occurred while fetching historic matches: %s", e)

    finally:
        await client.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())