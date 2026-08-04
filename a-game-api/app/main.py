from fastapi import FastAPI
from pydantic import BaseModel
from enum import Enum

from app.models.prediction import Prediction

from app.__version__ import __version__

app = FastAPI(
    title="A-Game API",
    description="A-Game API",
    version=__version__)

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

@app.post("/{team}", response_model=Prediction)
def prediction() -> Prediction:
    return Prediction(description="dummy description", match="Chelse FC vs Man Utd") # dummy response for now