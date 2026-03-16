"""FastAPI main application."""

import asyncio
import os
import structlog
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import Limiter, Rate, Duration
from routers import pinned, query, export, history, webhooks, payments, messages
from services.cache import close_redis, get_redis
from services.inference import close_client
from services.llm_errors import LLMError, LLMBadRequest, LLMUnavailable
from logging_config import setup_logging, logger
from config import get_settings


redis_available = False
rate_limiter: Limiter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    """App lifespan: startup/shutdown."""
    setup_logging()
    
    global redis_available, rate_limiter
    redis_available = False
    rate_limiter = None
    
    try:
        r = await get_redis()
        await r.ping()
        redis_available = True
        if get_settings().rate_limit_per_user > 0:
            rate_limiter = Limiter(Rate(get_settings().rate_limit_per_user, Duration.MINUTE))
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
        "connect-src 'self' https://*.supabase.co https://*.groq.com https://api.groq.com; "
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
        content={"error": "Service Unavailable", "detail": str(exc)}
    )


@app.exception_handler(LLMBadRequest)
async def llm_bad_request_handler(request: Request, exc: LLMBadRequest):
    """Handle invalid LLM requests."""
    logger.error("llm_bad_request", error=str(exc))
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request", "detail": str(exc)}
    )


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    """Handle general LLM errors."""
    logger.error("llm_error", error=str(exc))
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request", "detail": str(exc)}
    )


# app.include_router(pinned.router, prefix="/api") removed - duplicate below

async def conditional_rate_limit(request: Request, response: Response):
    """
    Apply rate limiting ONLY if Redis is available.
    In development (when Redis fails), this becomes a no-op.
    """
    if not redis_available or rate_limiter is None:
        return

    try:
        if get_settings().environment == "production":
             await RateLimiter(rate_limiter)(request, response)
        else:
            try:
                await RateLimiter(rate_limiter)(request, response)
            except Exception:
                pass
    except Exception:
        pass



app.include_router(pinned.router, prefix="/api")
app.include_router(
    messages.router,
    prefix="/api",
    dependencies=[Depends(conditional_rate_limit)],
)
app.include_router(
    query.router,
    prefix="/api",
    dependencies=[Depends(conditional_rate_limit)],
)
app.include_router(export.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(webhooks.router)  # No prefix - webhooks use full path
app.include_router(payments.router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health():
    """Health check with dependency status."""
    settings = get_settings()
    status: dict = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "environment": settings.environment,
    }


    try:
        r = await get_redis()
        await r.ping()
        status["redis"] = "✓ healthy"
    except Exception as e:
        status["redis"] = f"✗ error: {str(e)}"
        is_prod = settings.environment == "production"
        if is_prod:

            return JSONResponse(status_code=503, content=status)

    try:
        from google import genai
        status["google_genai"] = "✓ installed"
    except Exception as e:
        status["google_genai"] = f"✗ {str(e)}"

    status["dodo"] = {
        "payment_link_id": "configured" if settings.dodo_payment_link_id else "missing",
        "webhook_secret": "configured" if settings.dodo_webhook_secret else (
            "fallback_api_key" if settings.dodo_api_key else "missing"
        ),
    }

    return status


# Catch-all route for debugging (should be last)
@app.get("/{path:path}")
async def catch_all(path: str):
    return {"message": f"Catch-all route hit: /{path}", "status": "Backend is running!"}

