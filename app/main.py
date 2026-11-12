import logging
import os  # THROWAWAY: deliberate unused import (F401) to prove only the lint check fails
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import PREFIX as V1_PREFIX
from app.api.v1 import metrics, services, status
from app.workers.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Service Monitoring API",
    description="Monitor external and internal services, track uptime, and get real-time observability.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(services.router, prefix=V1_PREFIX)
app.include_router(metrics.router, prefix=V1_PREFIX)
app.include_router(status.router, prefix=V1_PREFIX)


# response_model=None keeps the OpenAPI schema unchanged: without it FastAPI would
# derive a response model from the return annotation.
@app.get("/", tags=["root"], response_model=None)
def root() -> dict[str, str]:
    return {"name": "Service Monitoring API", "version": "1.0.0", "docs": "/docs"}


@app.get("/health", tags=["root"], response_model=None)
def health() -> dict[str, str]:
    return {"status": "ok"}
