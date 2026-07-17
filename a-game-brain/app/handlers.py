import redis.asyncio as Redis
from asyncpg import Pool

import logging
import json


log = logging.getLogger("brain.handlers")


async def process_job(body: bytes, pg: Pool, redis: Redis):
    """Process a job from the queue.

    Args:
        body (bytes): The job payload.
        pg (asyncpg.Pool): The PostgreSQL connection pool.
        redis (redis.Redis): The Redis client.
    """
    try:
        job_body = json.loads(body.decode("utf-8"))
        job_id = job_body.get("id", "unknown")
        log.info(f"Processing job {job_id} with body: {job_body}")

        # PLUMBING STUB: prove both downstream connections work WITHOUT depending on
        # a schema that isn't designed yet (DB schema design is still pending).
        async with pg.acquire() as connection:
            await connection.fetchval("SELECT 1")        # Postgres reachable + creds valid
        await redis.set("calc:last_job", job_id)        # Redis writable

        # TODO: swap this for real Elo + Poisson compute + result writes once the
        # predictions schema exists.
        log.info("processed job %s (stub)", job_id)
    except Exception as e:
        log.error(f"Error processing job: {e}")