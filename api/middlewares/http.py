"""HTTP middleware utilities."""

from __future__ import annotations

import time
from fastapi import Request
import structlog

from api.logging_config import (
    generate_request_id,
    is_valid_request_id,
    log_sampled_success,
    logger,
)
from api.monitoring import capture_exception, continue_trace_from_headers, set_request_context

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
    except Exception as exc:
        capture_exception(exc, request_id=request_id, path=request.url.path, method=request.method)
        logger.error("http_request_exception", request_id=request_id, error=str(exc))
        raise
