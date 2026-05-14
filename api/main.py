"""FastAPI main application for DepthAPI."""

import asyncio
import os
import time
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from routers import (
    query,
    ingest,
    export,
    history,
    webhooks,
    payments,
    messages,
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
from api.services.inference.llm_errors import LLMError, LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable
from api.logging_config import (
    setup_logging,
    logger,
    generate_request_id,
    is_valid_request_id,
    log_sampled_success,
)
from api.config import get_settings
from monitoring import init_sentry, capture_exception, continue_trace_from_headers, set_request_context


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: startup/shutdown."""
    setup_logging()
    init_sentry(get_settings())
    
    config_state = get_provider_config_state()
    issues = config_state.get("issues", [])
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
        [f"{max(int(settings.slowapi_default_limit_per_minute or 120), 1)}/minute"]
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

DEFAULT_ALLOWED_ORIGINS = (
    "https://depthapi.dev",
    "https://api.depthapi.dev",
)


def resolve_allowed_origins(raw_allowed_origins: str | None) -> list[str]:
    """Build a secure allowlist for credentialed CORS."""
    if raw_allowed_origins is None or not raw_allowed_origins.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)

    parsed_origins = [origin.strip() for origin in raw_allowed_origins.split(",") if origin.strip()]
    if "*" in parsed_origins:
        parsed_origins = [origin for origin in parsed_origins if origin != "*"]

    return parsed_origins or list(DEFAULT_ALLOWED_ORIGINS)


allowed_origins = resolve_allowed_origins(os.getenv("ALLOWED_ORIGINS"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization", "x-request-id"],
    max_age=3600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    csp = (
        "default-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.middleware("http")
async def structlog_middleware(request: Request, call_next):
    """Log requests with structlog."""
    start = time.perf_counter()
    incoming_request_id = request.headers.get("x-request-id")
    request_id = incoming_request_id if is_valid_request_id(incoming_request_id) else generate_request_id()
    request.state.request_id = request_id

    structlog.contextvars.clear_contextvars()
    continue_trace_from_headers(
        {
            "sentry-trace": request.headers.get("sentry-trace", ""),
            "baggage": request.headers.get("baggage", ""),
        }
    )
    set_request_context(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        client_ip=request.client.host if request.client else None,
    )
    
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        if response.status_code < 400:
            log_sampled_success(
                "http_request_success",
                request_id=request_id,
                status_code=response.status_code,
                latency_ms=duration_ms,
                sampled=True,
            )
        return response
    except Exception as e:
        capture_exception(e, request_id=request_id, path=request.url.path, method=request.method)
        logger.error("http_request_exception", request_id=request_id, error=str(e))
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), path=request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.exception_handler(LLMUnavailable)
async def llm_unavailable_handler(request: Request, exc: LLMUnavailable):
    return JSONResponse(status_code=503, content={"error": {"type": "service_degraded", "message": str(exc)}})


@app.exception_handler(LLMInvalidAPIKey)
async def llm_invalid_api_key_handler(request: Request, exc: LLMInvalidAPIKey):
    return JSONResponse(status_code=502, content={"error": {"type": "invalid_api_key", "message": str(exc)}})


@app.exception_handler(LLMBadRequest)
async def llm_bad_request_handler(request: Request, exc: LLMBadRequest):
    return JSONResponse(status_code=400, content={"error": {"type": "bad_request", "message": str(exc)}})


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    return JSONResponse(status_code=400, content={"error": {"type": "llm_error", "message": str(exc)}})


# --- Core DepthAPI Routes ---
app.include_router(query.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(webhooks.router)
app.include_router(payments.router, prefix="/api")


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
