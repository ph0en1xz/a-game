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

        changed: int = await postgres.sync_matches_per_competition(pool, matches)
        log.info("Synced %d changed matches", changed)

        events = await postgres.fetch_rabbit_events(pool)
        if events:
            await rabbitmq.run_producer([e["match_id"] for e in events])
            await postgres.delete_rabbit_event(pool, [e["id"] for e in events])

    finally:
        await pool.close()

    log.info("Worker finished")


if __name__ == "__main__":
    asyncio.run(main())
