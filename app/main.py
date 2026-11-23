import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import PREFIX as V1_PREFIX
from app.api.v1 import metrics, services, status
from app.services.url_safety import UnsafeDestinationError
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


@app.exception_handler(UnsafeDestinationError)
async def unsafe_destination_handler(request: Request, exc: UnsafeDestinationError) -> JSONResponse:
    """Same shape as FastAPI request-validation errors (spec 002 RF-5)."""
    error = {"type": "url_not_allowed", "loc": ["body", "url"], "msg": str(exc)}
    return JSONResponse(
        status_code=422, content={"detail": [{**error, "input": exc.submitted_url}]}
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
