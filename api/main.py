"""DepthAPI application."""
from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from api.adapters.pg_adapter import init_pool, close_pool
from api.config import get_settings
from api.routers import query, ingest, wiki

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool(get_settings().database_url)
    yield
    await close_pool()

app = FastAPI(title="DepthAPI", version="3.0.0", lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda _request, _exc: JSONResponse(status_code=429, content={"error": "rate limit exceeded"}))
app.add_middleware(SlowAPIMiddleware)
allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["authorization", "content-type"], max_age=3600)

@app.middleware("http")
async def security_headers(_request: Request, call_next):
    response = await call_next(_request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

app.include_router(query.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(wiki.router, prefix="/api")

@app.get("/api/health")
async def health():
    return {"status": "ok"}
