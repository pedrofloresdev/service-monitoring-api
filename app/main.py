import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import PREFIX as V1_PREFIX
from app.api.v1 import metrics, services, status
from app.core import demo_settings, runtime
from app.core.config import settings
from app.core.demo_settings import load_demo_config
from app.services.demo_seeding import seed_at_startup, validate_seed_urls
from app.services.url_safety import UnsafeDestinationError
from app.workers.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Any exception here fails the lifespan startup, so the server refuses to start.
    if settings.demo_mode:
        if not runtime.started_by_serve():  # spec 003d RF-2, before any demo step
            raise RuntimeError(runtime.START_COMMAND_MESSAGE)
        config = load_demo_config()  # every invalid setting at once (spec 003a RF-3)
        validate_seed_urls(config)  # RF-4
        first_check_ids = seed_at_startup(config)  # one transaction (RF-5 to RF-7)
        demo_settings.demo_config = config
        start_scheduler(first_check_ids=first_check_ids)  # RF-8
    else:
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
