"""FastAPI main application."""

import asyncio
import os
import structlog
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
try:
    from fastapi_limiter import FastAPILimiter
except Exception:
    FastAPILimiter = None
from fastapi_limiter.depends import RateLimiter
from routers import pinned, query, export, history, webhooks, payments, messages
from services.cache import close_redis, get_redis
from services.inference import close_client
from services.model_provider import ModelProvider, ModelError, RequiresPro, ModelUnavailable
from logging_config import setup_logging, logger
from config import get_settings


redis_available = False


@asynccontextmanager
async def lifespan(app: FastAPI):

    """App lifespan: startup/shutdown."""
    setup_logging()
    
    global redis_available
    redis_available = False
    
    r = await get_redis()

    try:
        await r.ping()
        if FastAPILimiter is not None:
            await FastAPILimiter.init(r)
            redis_available = True
            logger.info("redis_connected_rate_limiter_init")
        else:
            logger.warning("fastapi_limiter_unavailable_skipping_init")
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

    provider = ModelProvider.get_instance()
    await provider.initialize()
    
    logger.info("startup", 
                gemini_configured=provider.gemini_configured)
    
    yield
    await asyncio.gather(close_redis(), close_client(), ModelProvider.get_instance().close())


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


@app.exception_handler(ModelUnavailable)
async def model_unavailable_handler(request: Request, exc: ModelUnavailable):
    """Handle missing model configuration."""
    logger.warning("model_unavailable", error=str(exc))
    return JSONResponse(
        status_code=503,
        content={"error": "Service Unavailable", "detail": str(exc)}
    )


@app.exception_handler(RequiresPro)
async def requires_pro_handler(request: Request, exc: RequiresPro):
    """Handle pro-only feature access."""
    logger.info("requires_pro_access", error=str(exc))
    return JSONResponse(
        status_code=402,
        content={"error": "Payment Required", "detail": str(exc)}
    )


@app.exception_handler(ModelError)
async def model_error_handler(request: Request, exc: ModelError):
    """Handle general model errors."""
    logger.error("model_error", error=str(exc))
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
    if not redis_available:
        return

    try:
        if get_settings().environment == "production":
             await RateLimiter(times=get_settings().rate_limit_per_user, seconds=60)(request, response)
        else:
            try:
                await RateLimiter(times=get_settings().rate_limit_per_user, seconds=60)(request, response)
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
    status = {
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

