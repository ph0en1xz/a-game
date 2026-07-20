import asyncio
import logging

import asyncpg

import app.api_client as sports_client
import app.db as postgres
import app.producer as rabbitmq
from app.config import settings
from app.model import Match

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker.main")


async def main():
    log.info("Starting the worker...")

    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=settings.postgres_url, min_size=1, max_size=5
    )
    try:
        async with sports_client.make_client() as client:
            competitions: list[dict] = await sports_client.get_all_competitions(client)
            competitions_count: int = await postgres.sync_competitions(pool, competitions)
            log.info("Synced %d competitions", competitions_count)

            league_codes: list[str] = await postgres.get_league_codes(pool)
            matches: list[Match] = await sports_client.get_matches_per_competition(league_codes, client)

        match_ids: list[int] = await postgres.sync_matches_per_competition(pool, matches)
        if len(match_ids) > 0:
            await rabbitmq.run_producer(match_ids)
        log.info("Synced %d changed matches", len(match_ids))

    finally:
        await pool.close()

    log.info("Worker finished")


if __name__ == "__main__":
    asyncio.run(main())
