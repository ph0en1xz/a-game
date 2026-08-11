import logging
from contextlib import asynccontextmanager
from enum import Enum

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.__version__ import __version__
from app.db import get_prediction
from app.models.prediction import Prediction
from app.store import make_pg_pool, make_redis

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api.main")

CACHE_TTL_SECONDS = 86400


@asynccontextmanager
async def service_context(app: FastAPI):
    app.state.pg_pool = await make_pg_pool()
    app.state.redis_client = await make_redis()
    log.info("api service started")

    try:
        yield
    finally:
        await app.state.pg_pool.close()
        await app.state.redis_client.aclose()
        log.info("api service stopped")


app = FastAPI(
    title="A-Game API",
    description="A-Game API",
    version=__version__,
    lifespan=service_context)

class HealthStatus(Enum):
    OK = "ok"
    ERROR = "error"
    READY = "ready"

class Health(BaseModel):
    status: HealthStatus
    version: str

@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello World"}

@app.get("/health", response_model=Health)
def healthz() -> Health:
    return Health(status=HealthStatus.OK, version=__version__)

@app.get("/readyz", response_model=Health)
def readyz() -> Health:
    return Health(status=HealthStatus.READY, version=__version__)

@app.get("/{match_id}", response_model=Prediction)
async def asyncprediction(match_id: int, request: Request) -> Prediction:

    text: str | None = None

    redis = request.app.state.redis_client
    pgpool = request.app.state.pg_pool

    try:
        if redis is None:
            async with pgpool.acquire() as conn:
                text = await get_prediction(conn, match_id)
                log.info("Fetching prediction for match with id %d from Postgres", match_id)
        else:
            text = await redis.get(f"match_id:{match_id}")
            if text is None:
                async with pgpool.acquire() as conn:
                    text = await get_prediction(conn, match_id)
                if text is not None:
                    log.info("Fetching prediction for match with id %d from Postgres", match_id)
                    await redis.set(f"match_id:{match_id}", text, ex=CACHE_TTL_SECONDS)
            else:
                log.info("Fetching prediction for match with id %d from Redis", match_id)

    except Exception:
        log.exception("failed to fetch prediction for match %d", match_id)
        raise

    if text is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "suggestion_not_ready",
                "message": f"No suggestion for match {match_id}.",
            })

    return Prediction(description=text, match_id=match_id)
