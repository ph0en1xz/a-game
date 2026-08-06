import logging

from app.commentary import Commentary, write_preview

from asyncpg import Pool # type: ignore
from redis.asyncio import Redis # type: ignore
from openai import AsyncOpenAI # type: ignore

from app.db import fetch_match_via_id


log = logging.getLogger("brain.handlers")

SEVEN_DAYS = 604800

async def process_job(match_id: int, pg: Pool, redis: Redis, client: AsyncOpenAI) -> Commentary | None:
    """Process one "match changed" event from the queue.

    Args:
        match_id (int): football-data's match id, as published by the worker.
        pg (asyncpg.Pool): The PostgreSQL connection pool.
        redis (redis.Redis): The Redis client.
        client (AsyncOpenAI): The LiteLLM-backed client used to write previews.

    Returns:
        Commentary | None: The generated preview, or None if the match was not
        found, the model returned nothing usable, or the job raised.
    """
    try:
        log.info("Processing match %d", match_id)

        match = await fetch_match_via_id(match_id, pg)
        if match is None:
            return

        await redis.set("brain:last_match", match_id, ex=SEVEN_DAYS)

        preview = await write_preview(match, client)
        if preview is None:
            log.warning("no preview for match %d", match_id)
            return

        log.info("processed match %d (%s, status=%s): %s",
                 match_id, 
                 match.fulltime_outcome, 
                 match.status, 
                 preview.text)
        
        return preview
    
    except Exception:  # broad on purpose - one bad message must not kill the consumer
        log.exception("Error processing job")