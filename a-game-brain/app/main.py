import logging
from asyncio import CancelledError, create_task
from contextlib import asynccontextmanager
from enum import Enum

from fastapi import FastAPI, Response
from pydantic import BaseModel

from app.__version__ import version as __version__
from app.consumer import run_consumer
from app.stores import make_pg_pool, make_redis, make_llm_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("brain.main")
log.info(f"Starting brain service, version {__version__}")

@asynccontextmanager
async def service_context(app: FastAPI):
    app.state.is_rabbitmq_ready = False
    app.state.pg_pool = await make_pg_pool()
    app.state.redis_client = await make_redis()
    app.state.llm_client = make_llm_client()

    task = create_task(run_consumer(app.state))
    log.info("brain service started; consumer task launched")

    try:
        yield # pause the execution of the context manager 
              # until the app is shutting down
    finally:
        task.cancel()
        try:
            await task
        except CancelledError:
            pass
        await app.state.pg_pool.close()
        await app.state.redis_client.aclose()
        await app.state.llm_client.close()
        log.info("brain service stopped")

app = FastAPI(title="a-game-brain", version=__version__, lifespan=service_context)

class HealthStatus(Enum):
    OK = "ok"
    ERROR = "error"
    READY = "ready"

class Health(BaseModel):
    status: HealthStatus
    version: str

@app.get("/health", response_model=Health)
def healthz() -> Health:
    return Health(status=HealthStatus.OK, version=__version__)

@app.get("/readyz", response_model=Health)
def readyz(response: Response) -> Health:
    if not app.state.is_rabbitmq_ready:
        response.status_code = 503   # K8s judges readiness by status code only
        return Health(status=HealthStatus.ERROR, version=__version__)
    return Health(status=HealthStatus.READY, version=__version__)