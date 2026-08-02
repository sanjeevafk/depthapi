"""DepthAPI application."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.adapters.pg_adapter import init_pool, close_pool
from api.config import get_settings
from api.routers import query, ingest

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool(get_settings().database_url)
    yield
    await close_pool()

app = FastAPI(title="DepthAPI", version="3.0.0", lifespan=lifespan)
app.include_router(query.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")

@app.get("/api/health")
async def health():
    return {"status": "ok"}
