"""Distributed abuse and cost controls backed by Upstash Redis."""

import time
from dataclasses import dataclass

from fastapi import HTTPException

from config import get_settings
from logging_config import logger
from services.cache import get_redis


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reason: str = "ok"


@dataclass
class QuotaResult:
    allowed: bool
    consumed: int
    limit: int
    retry_after: int


@dataclass
class CircuitBreakerResult:
    allowed: bool
    retry_after: int


def estimate_tokens_for_text(text: str, *, output_buffer: int | None = None) -> int:
    """Estimate request token cost before inference to enforce hard pre-call quotas."""
    settings = get_settings()
    response_tokens = int(
        output_buffer
        if output_buffer is not None
        else getattr(settings, "estimated_output_tokens_per_request", 900)
    )
    prompt_tokens = max(len((text or "").strip()) // 4, 1)
    return max(prompt_tokens + response_tokens, 1)


async def check_rate_limit(
    identifier: str,
    limit: int,
    window_seconds: int,
    *,
    namespace: str,
    fail_open: bool,
) -> RateLimitResult:
    """Apply a fixed-window distributed rate limit using INCR + EXPIRE."""
    key = f"knowbear:ratelimit:{namespace}:{identifier}"

    try:
        redis = await get_redis()
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, window_seconds)

        ttl = int(await redis.ttl(key))
        if ttl < 0:
            await redis.expire(key, window_seconds)
            ttl = window_seconds

        if count > limit:
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                retry_after=max(ttl, 1),
                reason="limit_exceeded",
            )

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after=max(ttl, 1),
        )
    except Exception as exc:
        logger.warning(
            "rate_limit_check_failed",
            identifier=identifier,
            namespace=namespace,
            fail_open=fail_open,
            error=str(exc),
        )
        if fail_open:
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=limit,
                retry_after=max(window_seconds, 1),
                reason="degraded_fail_open",
            )
        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after=1,
            reason="degraded_blocked",
        )


async def check_daily_quota(*, user_id: str, estimated_tokens: int) -> QuotaResult:
    """Enforce per-user daily token budget before model invocation."""
    settings = get_settings()
    limit = max(int(getattr(settings, "daily_token_quota_per_user", 0)), 0)
    window_seconds = max(int(getattr(settings, "quota_window_seconds", 86400)), 1)
    if limit <= 0:
        return QuotaResult(allowed=True, consumed=0, limit=0, retry_after=window_seconds)

    key = f"knowbear:quota:{user_id}"
    redis = await get_redis()
    requested = max(int(estimated_tokens), 1)

    script = (
        "local current = tonumber(redis.call('GET', KEYS[1]) or '0')\n"
        "local requested = tonumber(ARGV[1])\n"
        "local limit = tonumber(ARGV[2])\n"
        "local window = tonumber(ARGV[3])\n"
        "local consumed = current + requested\n"
        "if consumed > limit then\n"
        "  local ttl = redis.call('TTL', KEYS[1])\n"
        "  if ttl < 0 then ttl = window end\n"
        "  return {0, current, ttl}\n"
        "end\n"
        "local new_total = redis.call('INCRBY', KEYS[1], requested)\n"
        "local ttl = redis.call('TTL', KEYS[1])\n"
        "if ttl < 0 then\n"
        "  redis.call('EXPIRE', KEYS[1], window)\n"
        "  ttl = window\n"
        "end\n"
        "return {1, new_total, ttl}\n"
    )

    result = await redis.eval(script, 1, key, requested, limit, window_seconds)
    allowed_flag = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
    consumed = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
    ttl = int(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else window_seconds

    return QuotaResult(
        allowed=allowed_flag == 1,
        consumed=consumed,
        limit=limit,
        retry_after=max(ttl, 1),
    )


async def check_circuit_breaker(*, estimated_tokens: int, fail_open: bool) -> CircuitBreakerResult:
    """Track global token throughput and open breaker when threshold is exceeded."""
    settings = get_settings()
    threshold = max(int(getattr(settings, "circuit_breaker_tokens_per_minute", 0)), 0)
    open_seconds = max(int(getattr(settings, "circuit_breaker_open_seconds", 60)), 1)
    if threshold <= 0:
        return CircuitBreakerResult(allowed=True, retry_after=0)

    action = str(getattr(settings, "circuit_breaker_action", "reject") or "reject").lower()
    if action != "reject":
        return CircuitBreakerResult(allowed=True, retry_after=0)

    minute_bucket = int(time.time() // 60)
    usage_key = f"knowbear:circuit:tokens:{minute_bucket}"
    open_key = "knowbear:circuit:open"

    try:
        redis = await get_redis()
        already_open = await redis.get(open_key)
        if already_open:
            ttl = int(await redis.ttl(open_key))
            return CircuitBreakerResult(allowed=False, retry_after=max(ttl, 1))

        total = int(await redis.incrby(usage_key, max(int(estimated_tokens), 1)))
        if total <= max(int(estimated_tokens), 1):
            await redis.expire(usage_key, 120)

        if total > threshold:
            await redis.setex(open_key, open_seconds, "1")
            return CircuitBreakerResult(allowed=False, retry_after=open_seconds)

        return CircuitBreakerResult(allowed=True, retry_after=0)
    except Exception as exc:
        logger.warning("circuit_breaker_check_failed", fail_open=fail_open, error=str(exc))
        if fail_open:
            return CircuitBreakerResult(allowed=True, retry_after=0)
        return CircuitBreakerResult(allowed=False, retry_after=1)


async def enforce_request_controls(
    *,
    user_id: str | None,
    client_ip: str | None,
    estimated_tokens: int,
) -> None:
    """Apply auth-scoped quota, distributed rate limiting, and circuit breaker checks.

    Enforcement order: auth (handled by route dependency) -> quota -> rate limit -> inference.
    """
    settings = get_settings()
    strategy = str(getattr(settings, "rate_limit_strategy", "upstash_redis") or "upstash_redis").lower()
    if strategy != "upstash_redis":
        logger.warning("unsupported_rate_limit_strategy", strategy=strategy)

    is_authenticated = bool(user_id)
    fail_open = is_authenticated

    if is_authenticated:
        try:
            quota_result = await check_daily_quota(user_id=str(user_id), estimated_tokens=estimated_tokens)
        except Exception as exc:
            logger.warning("quota_check_failed", user_id=user_id, fail_open=fail_open, error=str(exc))
            if not fail_open:
                raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"}) from exc
            quota_result = QuotaResult(allowed=True, consumed=0, limit=0, retry_after=0)

        if not quota_result.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "type": "quota_exceeded",
                    "retry_allowed": False,
                    "limit": quota_result.limit,
                    "consumed": quota_result.consumed,
                },
                headers={"Retry-After": str(quota_result.retry_after)},
            )

    identifier = f"user:{user_id}" if is_authenticated else f"ip:{client_ip or 'unknown'}"
    burst_limit = max(
        int(getattr(settings, "rate_limit_burst", 5))
        if is_authenticated
        else int(getattr(settings, "anonymous_rate_limit_burst", 3)),
        0,
    )
    burst_window = max(int(getattr(settings, "rate_limit_burst_window_seconds", 10)), 1)
    sustained_limit = max(
        int(getattr(settings, "rate_limit_per_user", 20))
        if is_authenticated
        else int(getattr(settings, "anonymous_rate_limit_per_ip", 8)),
        0,
    )
    sustained_window = max(
        int(getattr(settings, "rate_limit_sustained_window_seconds", 60))
        if is_authenticated
        else int(getattr(settings, "anonymous_rate_limit_window_seconds", 60)),
        1,
    )

    if burst_limit > 0:
        burst = await check_rate_limit(
            identifier=identifier,
            limit=burst_limit,
            window_seconds=burst_window,
            namespace="burst",
            fail_open=fail_open,
        )
        if not burst.allowed:
            if burst.reason == "degraded_blocked":
                raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"})
            raise HTTPException(
                status_code=429,
                detail={"type": "rate_limit_exceeded", "scope": "burst"},
                headers={"Retry-After": str(burst.retry_after)},
            )

    if sustained_limit > 0:
        sustained = await check_rate_limit(
            identifier=identifier,
            limit=sustained_limit,
            window_seconds=sustained_window,
            namespace="sustained",
            fail_open=fail_open,
        )
        if not sustained.allowed:
            if sustained.reason == "degraded_blocked":
                raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"})
            raise HTTPException(
                status_code=429,
                detail={"type": "rate_limit_exceeded", "scope": "sustained"},
                headers={"Retry-After": str(sustained.retry_after)},
            )

    breaker = await check_circuit_breaker(estimated_tokens=estimated_tokens, fail_open=fail_open)
    if not breaker.allowed:
        raise HTTPException(
            status_code=503,
            detail={"type": "circuit_breaker_open", "action": "reject"},
            headers={"Retry-After": str(max(breaker.retry_after, 1))},
        )
