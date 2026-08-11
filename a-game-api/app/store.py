import asyncio
import logging

import asyncpg
import redis.asyncio as redis  # type: ignore

from app.config import settings

log = logging.getLogger("api.store")


async def make_pg_pool() -> asyncpg.Pool:
    retry_count: int = 5
    postgres_client_pool: asyncpg.Pool | None = None
    while retry_count > 0:
        try:
            postgres_client_pool = await asyncpg.create_pool(
                dsn=settings.postgres_url,
                min_size=1,
                max_size=5)

            if postgres_client_pool is None:
                raise ConnectionError("asyncpg.create_pool returned no pool")
            break

        except Exception as e:
            retry_count -= 1
            if retry_count == 0:
                raise ConnectionError(f"Failed to connect to PostgreSQL after multiple attempts: {e}") from e
            log.info("Postgres not ready (%s), retrying in 5s", e)
            await asyncio.sleep(5)

    if postgres_client_pool is None:
        raise ConnectionError("Failed to connect to PostgreSQL: retries exhausted")

    log.info("Postgres pool ready")
    return postgres_client_pool


async def make_redis() -> redis.Redis:
    retry_count: int = 5
    redis_client: redis.Redis | None = None
    while retry_count > 0:
        try:
            redis_client = redis.from_url(
                url=settings.redis_url,
                decode_responses=True)

            await redis_client.ping()
            break

        except Exception as e:
            retry_count -= 1
            if retry_count == 0:
                raise ConnectionError(f"Failed to connect to Redis after multiple attempts: {e}") from e
            log.info("Redis not ready (%s), retrying in 5s", e)
            await asyncio.sleep(5)

    if redis_client is None:
        raise ConnectionError("Failed to connect to Redis: retries exhausted")

    log.info("Redis client ready")
    return redis_client
