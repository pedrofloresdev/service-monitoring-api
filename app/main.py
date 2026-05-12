import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.workers.scheduler import start_scheduler, stop_scheduler
from app.api.v1 import PREFIX as V1_PREFIX
from app.api.v1 import services, metrics, status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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


@app.get("/", tags=["root"])
def root():
    return {"name": "Service Monitoring API", "version": "1.0.0", "docs": "/docs"}


@app.get("/health", tags=["root"])
def health():
    return {"status": "ok"}
