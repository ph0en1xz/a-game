import asyncio

import asyncpg
import redis.asyncio as redis

from app.config import settings


async def make_pg_pool() -> asyncpg.Pool:
    retry_count = 5
    pool = None
    while retry_count > 0:
        try:
            pool = await asyncpg.create_pool(dsn=settings.postgres_url, min_size=1, max_size=5)
            if pool is not None:
                break
        except Exception as e:
            retry_count -= 1
            if retry_count == 0:
                raise ConnectionError(f"Failed to connect to PostgreSQL after multiple attempts: {e}") from e
            await asyncio.sleep(5)  # Wait before retrying
    if pool is None:
        raise ConnectionError("Failed to connect to PostgreSQL: retries exhausted")
    return pool



async def make_redis() -> redis.Redis:
    retry_count = 5
    redis_client = None
    while retry_count > 0:
        try:
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            await redis_client.ping()
            if redis_client is not None:
                break
        except Exception as e:
            retry_count -= 1
            if retry_count == 0:
                raise ConnectionError(f"Failed to connect to Redis after multiple attempts: {e}") from e
            await asyncio.sleep(5)  # Wait before retrying
    if redis_client is None:
        raise ConnectionError("Failed to connect to Redis: retries exhausted")
    return redis_client