"""FastAPI main application."""

import asyncio
import os
import time
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from routers import (
    pinned,
    query,
    export,
    history,
    webhooks,
    payments,
    messages,
    analytics,
    legal,
    seo,
    emails,
    shares,
)
from auth import get_supabase_admin
from services.cache import close_redis
from services.redis_safe import redis_circuit_active
from services.inference import close_client
from services.search import close_search_client
from services.llm_client import get_provider_config_state
from services.llm_errors import LLMError, LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable
from logging_config import (
    setup_logging,
    logger,
    generate_request_id,
    is_valid_request_id,
    log_sampled_success,
)
from config import get_settings
from monitoring import init_sentry, capture_exception, continue_trace_from_headers, set_request_context


@asynccontextmanager
async def lifespan(app: FastAPI):

    """App lifespan: startup/shutdown."""
    setup_logging()
    init_sentry(get_settings())
    
    config_state = get_provider_config_state()
    issues = config_state.get("issues")
    if not isinstance(issues, list):
        issues = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        level = str(issue.get("severity", "warning"))
        event = "provider_config_validation"
        payload = {
            "severity": level,
            "issue_code": issue.get("code"),
            "message": issue.get("message"),
            "chat_enabled": bool(config_state.get("chat_enabled", False)),
        }
        if level == "error":
            logger.error(event, **payload)
        else:
            logger.warning(event, **payload)
    
    logger.info("redis_optional_cache_mode_enabled")

    logger.info("startup")
    
    yield
    await asyncio.gather(close_redis(), close_client(), close_search_client())


app = FastAPI(
    title="KnowBear API",
    description="AI-powered layered explanations",
    version="1.0.0",
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
    "https://knowbear.sanjeevkumar.me",
    "https://knowbear.vercel.app",
)


def resolve_allowed_origins(raw_allowed_origins: str | None) -> list[str]:
    """Build a secure allowlist for credentialed CORS."""
    if raw_allowed_origins is None or not raw_allowed_origins.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)

    parsed_origins = [origin.strip() for origin in raw_allowed_origins.split(",") if origin.strip()]
    if "*" in parsed_origins:
        logger.warning(
            "cors_wildcard_origin_sanitized",
            configured_origins=raw_allowed_origins,
            allow_credentials=True,
        )
        parsed_origins = [origin for origin in parsed_origins if origin != "*"]

    if not parsed_origins:
        logger.warning("cors_no_valid_origins_falling_back_to_defaults")
        return list(DEFAULT_ALLOWED_ORIGINS)

    return parsed_origins


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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://vercel.live; " 
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data: https://*.googleusercontent.com; "
        "connect-src 'self' https://*.supabase.co https://auth.knowbear.app; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none';"
    )
    
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    
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
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        client_ip=request.client.host if request.client else None,
    )
    
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        structlog.contextvars.bind_contextvars(
            status_code=response.status_code,
            latency_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        if response.status_code >= 400:
            logger.warning("http_request_failed", request_id=request_id, sampled=False)
        else:
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
        logger.error("http_request_exception", request_id=request_id, error=str(e), sampled=False)
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global error handler."""
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), path=request.url.path)
    logger.error("global_exception", error=str(exc))
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.exception_handler(LLMUnavailable)
async def llm_unavailable_handler(request: Request, exc: LLMUnavailable):
    """Handle missing LLM configuration."""
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), handler="llm_unavailable")
    logger.warning("llm_unavailable", error=str(exc))
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": getattr(exc, "error_type", "service_degraded"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
            },
            "detail": str(exc),
        },
    )


@app.exception_handler(LLMInvalidAPIKey)
async def llm_invalid_api_key_handler(request: Request, exc: LLMInvalidAPIKey):
    """Handle invalid provider credentials."""
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), handler="llm_invalid_api_key")
    logger.error("llm_invalid_api_key", error=str(exc))
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "type": getattr(exc, "error_type", "invalid_api_key"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
            },
            "detail": str(exc),
        },
    )


@app.exception_handler(LLMBadRequest)
async def llm_bad_request_handler(request: Request, exc: LLMBadRequest):
    """Handle invalid LLM requests."""
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), handler="llm_bad_request")
    logger.error("llm_bad_request", error=str(exc))
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "type": getattr(exc, "error_type", "bad_request"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
            },
            "detail": str(exc),
        },
    )


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    """Handle general LLM errors."""
    capture_exception(exc, request_id=getattr(request.state, "request_id", None), handler="llm_error")
    logger.error("llm_error", error=str(exc))
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "type": getattr(exc, "error_type", "llm_error"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", True),
            },
            "detail": str(exc),
        },
    )


# app.include_router(pinned.router, prefix="/api") removed - duplicate below

app.include_router(pinned.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(legal.router, prefix="/api")
app.include_router(seo.router)
app.include_router(emails.router, prefix="/api")
app.include_router(webhooks.router)  # No prefix - webhooks use full path
app.include_router(payments.router, prefix="/api")
app.include_router(shares.router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health():
    """Lightweight dependency checks with config-derived provider status semantics."""
    settings = get_settings()
    config_state = get_provider_config_state()

    async def check_provider_stack() -> dict[str, object]:
        """Return provider health from validated config state (no active network probe)."""
        chat_enabled = bool(config_state.get("chat_enabled", False))
        has_api_key = bool(config_state.get("has_api_key", False))
        if not chat_enabled:
            return {
                "status": "degraded",
                "reachable": False,
                "key_valid": has_api_key,
                "chat_enabled": chat_enabled,
            }

        return {
            "status": "ok",
            "reachable": chat_enabled,
            "key_valid": has_api_key,
            "chat_enabled": chat_enabled,
        }

    async def check_rate_limit() -> dict[str, str]:
        if redis_circuit_active():
            logger.info("rate_limit_health_degraded_redis_optional")
            return {"status": "degraded"}
        return {"status": "ok"}


    async def check_db() -> dict[str, str]:
        try:
            if not settings.supabase_url or not settings.supabase_secret_key:
                logger.warning("db_health_degraded_missing_config", severity="warning")
                return {"status": "degraded"}
            supabase = await asyncio.wait_for(asyncio.to_thread(get_supabase_admin), timeout=2.0)
            return {"status": "ok" if supabase else "degraded"}
        except Exception as exc:
            logger.error("db_health_probe_failed", severity="error", error=str(exc))
            return {"status": "down"}

    provider, rate_limit, db = await asyncio.gather(check_provider_stack(), check_rate_limit(), check_db())

    component_statuses = [
        str(provider.get("status", "down")),
        rate_limit["status"],
        db["status"],
    ]
    overall = "ok"
    if "down" in component_statuses:
        overall = "down"
    elif "degraded" in component_statuses:
        overall = "degraded"

    return {
        "status": overall,
        "provider": {
            "status": provider["status"],
            "reachable": bool(provider.get("reachable", False)),
            "key_valid": bool(provider.get("key_valid", False)),
        },
        "rate_limit": {"status": rate_limit["status"]},
        "db": {"status": db["status"]},
        "chat_enabled": bool(provider.get("chat_enabled", False)),
        "key_valid": bool(provider.get("key_valid", False)),
    }


@app.get("/api/keep-alive", tags=["health"])
async def keep_alive(request: Request):
    """Minimal keep-alive endpoint for Supabase and Vercel cold-start mitigation."""
    cron_secret = os.getenv("CRON_SECRET")
    if cron_secret:
        provided = request.query_params.get("key")
        if not provided or provided != cron_secret:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

    supabase = get_supabase_admin()
    if not supabase:
        return JSONResponse(status_code=503, content={"error": "supabase_unavailable"})

    await asyncio.to_thread(
        lambda: supabase.table("conversations").select("id").limit(1).execute()
    )
    return JSONResponse(status_code=200, content={"ok": True})


@app.head("/api/keep-alive", tags=["health"])
async def keep_alive_head(request: Request):
    """HEAD variant that avoids Supabase calls."""
    cron_secret = os.getenv("CRON_SECRET")
    if cron_secret:
        provided = request.query_params.get("key")
        if not provided or provided != cron_secret:
            return Response(status_code=401)
    return Response(status_code=200)


# Catch-all route for debugging (should be last)
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(request: Request, path: str):
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "detail": f"Route '{request.method} /{path}' does not exist.",
        },
    )

