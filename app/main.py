from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.workers.scheduler import start_scheduler
from app.api.v1 import PREFIX as V1_PREFIX
from app.api.v1 import services
from app.api.v1 import metrics
from app.api.v1 import status


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(services.router, prefix=V1_PREFIX)
app.include_router(metrics.router, prefix=V1_PREFIX)
app.include_router(status.router, prefix=V1_PREFIX)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/health")
def read_health():
    return {"status": "ok"}
