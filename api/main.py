"""FastAPI main application."""

import asyncio
import os
import time
import structlog
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import pinned, query, export, history, webhooks, payments, messages
from auth import get_supabase_admin
from services.cache import close_redis, get_redis
from services.inference import close_client
from services.llm_client import get_litellm_config_state
from services.llm_errors import LLMError, LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable
from logging_config import setup_logging, logger
from config import get_settings


redis_available = False


@asynccontextmanager
async def lifespan(app: FastAPI):

    """App lifespan: startup/shutdown."""
    setup_logging()
    
    global redis_available
    redis_available = False

    config_state = get_litellm_config_state()
    issues = config_state.get("issues")
    if not isinstance(issues, list):
        issues = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        level = str(issue.get("severity", "warning"))
        event = "litellm_config_validation"
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
    
    try:
        r = await get_redis()
        await r.ping()
        redis_available = True
        logger.info("redis_connected_rate_limiter_init")
    except Exception as e:

        logger.error("redis_connection_failed", error=str(e))

        # Soften enforcement to prevent total site blackout if Redis is just flapping
        is_prod = get_settings().environment == "production"
        if is_prod:
            logger.error("PROD_REDIS_FAILURE_CONTINUING_UNPROTECTED", error=str(e))
            # Site will still run, but rate limiting will be off. 
            # This prevents the "Failed to Fetch" error caused by the app crashing on startup.
        else:
            logger.warning("redis_unavailable_dev_mode_continuing", error=str(e))

    logger.info("startup")
    
    yield
    await asyncio.gather(close_redis(), close_client())


app = FastAPI(
    title="KnowBear API",
    description="AI-powered layered explanations",
    version="1.0.0",
    lifespan=lifespan,
)

allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "*" 
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization"],
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
        "connect-src 'self' https://*.supabase.co; "
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
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        path=request.url.path,
        method=request.method,
        client_ip=request.client.host if request.client else None,
    )
    
    try:
        response = await call_next(request)
        structlog.contextvars.bind_contextvars(
            status_code=response.status_code,
        )
        if response.status_code >= 400:
             logger.warning("http_request_failed")
        else:
             logger.info("http_request_success")
        return response
    except Exception as e:
        logger.error("http_request_exception", error=str(e))
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global error handler."""
    logger.error("global_exception", error=str(exc))
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.exception_handler(LLMUnavailable)
async def llm_unavailable_handler(request: Request, exc: LLMUnavailable):
    """Handle missing LLM configuration."""
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
    """Handle invalid LiteLLM credentials."""
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
app.include_router(webhooks.router)  # No prefix - webhooks use full path
app.include_router(payments.router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health():
    """Lightweight dependency health checks with degraded state semantics."""
    settings = get_settings()
    config_state = get_litellm_config_state()
    litellm_base_url = str(config_state.get("base_url") or "")
    litellm_api_key = settings.litellm_virtual_key or settings.litellm_master_key

    async def check_litellm() -> dict[str, object]:
        if not bool(config_state.get("chat_enabled", False)):
            return {
                "status": "degraded",
                "latency_ms": 0,
                "reachable": False,
                "key_valid": False,
                "chat_enabled": False,
            }

        url = litellm_base_url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        models_url = f"{url}/models"

        start = time.perf_counter()
        try:
            timeout = min(max(float(settings.litellm_timeout_seconds), 1.0), 2.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {litellm_api_key}"},
                )
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code in {401, 403}:
                logger.error("litellm_invalid_key_detected", severity="error", status_code=response.status_code)
                return {
                    "status": "down",
                    "latency_ms": latency_ms,
                    "reachable": True,
                    "key_valid": False,
                    "chat_enabled": False,
                }

            if response.status_code >= 500:
                return {
                    "status": "down",
                    "latency_ms": latency_ms,
                    "reachable": True,
                    "key_valid": True,
                    "chat_enabled": False,
                }
            return {
                "status": "ok",
                "latency_ms": latency_ms,
                "reachable": True,
                "key_valid": True,
                "chat_enabled": True,
            }
        except Exception as exc:
            logger.warning("litellm_health_probe_failed", severity="warning", error=str(exc))
            return {
                "status": "down",
                "latency_ms": 0,
                "reachable": False,
                "key_valid": bool(litellm_api_key),
                "chat_enabled": False,
            }

    async def check_rate_limit() -> dict[str, str]:
        try:
            redis = await asyncio.wait_for(get_redis(), timeout=0.35)
            await asyncio.wait_for(redis.ping(), timeout=0.35)
            return {"status": "ok"}
        except Exception as exc:
            is_prod = settings.environment == "production"
            status = "down" if is_prod else "degraded"
            log_fn = logger.error if is_prod else logger.warning
            log_fn("rate_limit_health_probe_failed", severity="error" if is_prod else "warning", error=str(exc))
            return {"status": status}

    async def check_db() -> dict[str, str]:
        try:
            if not settings.supabase_url or not settings.supabase_service_role_key:
                logger.warning("db_health_degraded_missing_config", severity="warning")
                return {"status": "degraded"}
            supabase = await asyncio.wait_for(asyncio.to_thread(get_supabase_admin), timeout=0.35)
            return {"status": "ok" if supabase else "degraded"}
        except Exception as exc:
            logger.error("db_health_probe_failed", severity="error", error=str(exc))
            return {"status": "down"}

    litellm, rate_limit, db = await asyncio.gather(check_litellm(), check_rate_limit(), check_db())

    component_statuses = [
        str(litellm.get("status", "down")),
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
        "litellm": {"status": litellm["status"], "latency_ms": litellm["latency_ms"]},
        "rate_limit": {"status": rate_limit["status"]},
        "db": {"status": db["status"]},
        "chat_enabled": bool(litellm.get("chat_enabled", False)),
        "key_valid": bool(litellm.get("key_valid", False)),
    }


# Catch-all route for debugging (should be last)
@app.get("/{path:path}")
async def catch_all(path: str):
    return {"message": f"Catch-all route hit: /{path}", "status": "Backend is running!"}

