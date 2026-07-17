import asyncio
import logging

import asyncpg

import app.api_client as sports_client
import app.db as postgres
from app.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker.main")


async def main():
    log.info("Starting the worker...")

    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=settings.postgres_url, min_size=1, max_size=5
    )
    try:
        async with sports_client.make_client() as client:
            competitions = await sports_client.get_all_competitions(client)

        count = await postgres.sync_competitions(pool, competitions)
        log.info("Synced %d competitions", count)

    finally:
        await pool.close()

    log.info("Worker finished")


if __name__ == "__main__":
    asyncio.run(main())
