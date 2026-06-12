"""FastAPI main application for DepthAPI."""

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from api.routers import (
    query,
    ingest,
    export,
    demo,
)
from api.services.infra.cache import close_redis
from api.services.infra.redis_safe import redis_circuit_active
from api.services.inference.inference import close_client
from api.services.rag.search import close_search_client
from api.services.inference.llm_client import get_provider_config_state
from api.services.rag.rag_dimension_guard import (
    get_dimension_guard_status,
    validate_embedding_dimension_or_raise,
)
from api.logging_config import setup_logging, logger
from api.middlewares import (
    DEFAULT_ALLOWED_ORIGINS,
    resolve_allowed_origins,
    security_headers,
    structlog_middleware,
)
from api.config import get_settings
from api.monitoring import init_sentry
from api.exception_handlers import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: startup/shutdown."""
    setup_logging()
    init_sentry(get_settings())
    
    config_state = get_provider_config_state()
    issues = config_state.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            level = str(issue.get("severity", "warning"))
            payload = {
                "severity": level,
                "issue_code": issue.get("code"),
                "message": issue.get("message"),
                "chat_enabled": bool(config_state.get("chat_enabled", False)),
            }
            if level == "error":
                logger.error("provider_config_validation", **payload)
            else:
                logger.warning("provider_config_validation", **payload)
    
    logger.info("startup")
    await validate_embedding_dimension_or_raise()
    yield
    await asyncio.gather(close_redis(), close_client(), close_search_client())


app = FastAPI(
    title="DepthAPI",
    description="Depth-Aware RAG inference API — one endpoint, configurable cognitive depth, automatic provider failover.",
    version="2.0.0",
    lifespan=lifespan,
)

settings = get_settings()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=(
        [f"{max(settings.slowapi_default_limit_per_minute or 120, 1)}/minute"]
        if settings.slowapi_enabled
        else []
    ),
)
app.state.limiter = limiter
if settings.slowapi_enabled:
    app.add_exception_handler(RateLimitExceeded, lambda request, exc: JSONResponse(
        status_code=429,
        content={"error": "Too many requests"},
    ))
    app.add_middleware(SlowAPIMiddleware)

allowed_origins = resolve_allowed_origins(os.getenv("ALLOWED_ORIGINS"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization", "x-request-id"],
    max_age=3600,
)


app.middleware("http")(security_headers)
app.middleware("http")(structlog_middleware)
register_exception_handlers(app)


app.include_router(query.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(demo.router)


@app.get("/api/health", tags=["health"])
async def health():
    """System health report."""
    settings = get_settings()
    config_state = get_provider_config_state()

    async def check_provider_stack():
        chat_enabled = bool(config_state.get("chat_enabled", False))
        has_api_key = bool(config_state.get("has_api_key", False))
        return {
            "status": "ok" if chat_enabled else "degraded",
            "reachable": chat_enabled,
            "key_valid": has_api_key,
        }

    async def check_rate_limit():
        return {"status": "degraded" if redis_circuit_active() else "ok"}

    stack = await check_provider_stack()
    limits = await check_rate_limit()

    return {
        "status": "ok" if stack["status"] == "ok" and limits["status"] == "ok" else "degraded",
        "environment": settings.environment,
        "version": "2.0.0",
        "provider_stack": stack,
        "rate_limit_backend": limits,
        "rag_dimension_guard": await get_dimension_guard_status(),
    }
