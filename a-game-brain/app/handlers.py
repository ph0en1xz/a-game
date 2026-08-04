import logging

from asyncpg import Pool
from redis.asyncio import Redis

from app.db import fetch_match_via_id

log = logging.getLogger("brain.handlers")


async def process_job(match_id: int, pg: Pool, redis: Redis):
    """Process one "match changed" event from the queue.

    Args:
        match_id (int): football-data's match id, as published by the worker.
        pg (asyncpg.Pool): The PostgreSQL connection pool.
        redis (redis.Redis): The Redis client.
    """
    try:
        log.info("Processing match %d", match_id)

        match = await fetch_match_via_id(match_id, pg)
        if match is None:
            return

        await redis.set("brain:last_match", match_id)     # Redis writable

        # TODO: swap this for real Elo + Poisson compute + result writes once the
        # predictions schema exists.
        log.info("processed match %d (%s, status=%s) (stub)",
                 match_id, match["fulltime_outcome"], match["status"])
    except Exception:  # broad on purpose - one bad message must not kill the consumer
        log.exception("Error processing job")