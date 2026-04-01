"""Distributed abuse and cost controls backed by Upstash Redis."""

import time
from dataclasses import dataclass

from fastapi import HTTPException

from config import get_settings
from logging_config import anonymize_user_id, logger
from services.cache import get_redis
from services.token_count import count_prompt_tokens


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


@dataclass
class TokenReservation:
    identifier: str
    mode: str
    reserved_tokens: int
    daily_key: str
    hourly_key: str
    hourly_bucket: int
    is_anonymous: bool


def estimate_tokens_for_text(text: str, *, output_buffer: int | None = None) -> int:
    """Estimate request token cost before inference to enforce hard pre-call quotas."""
    settings = get_settings()
    response_tokens = int(
        output_buffer
        if output_buffer is not None
        else getattr(settings, "estimated_output_tokens_per_request", 900)
    )
    prompt_tokens = count_prompt_tokens(text)
    return max(prompt_tokens + max(response_tokens, 0), 1)


async def check_rate_limit(
    identifier: str,
    limit: int,
    window_seconds: int,
    *,
    namespace: str,
    fail_open: bool,
    mode: str | None = None,
) -> RateLimitResult:
    """Apply a fixed-window distributed rate limit using INCR + EXPIRE."""
    mode_label = (mode or "default").strip().lower()
    key = f"knowbear:ratelimit:{namespace}:{identifier}:{mode_label}"

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


async def check_daily_quota(*, key: str, limit: int, requested: int, window_seconds: int) -> QuotaResult:
    """Enforce per-key daily token budget before model invocation."""
    if limit <= 0:
        return QuotaResult(allowed=True, consumed=0, limit=0, retry_after=max(window_seconds, 1))

    redis = await get_redis()
    requested_tokens = max(int(requested), 1)

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

    result = await redis.eval(script, 1, key, requested_tokens, limit, window_seconds)
    allowed_flag = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
    consumed = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
    ttl = int(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else window_seconds

    return QuotaResult(
        allowed=allowed_flag == 1,
        consumed=consumed,
        limit=limit,
        retry_after=max(ttl, 1),
    )


async def check_hourly_quota(*, key: str, limit: int, requested: int, now_minute: int) -> QuotaResult:
    """Enforce rolling hourly quota via per-minute buckets stored in a Redis hash."""
    if limit <= 0:
        return QuotaResult(allowed=True, consumed=0, limit=0, retry_after=3600)

    redis = await get_redis()
    requested_tokens = max(int(requested), 1)
    window_minutes = 60

    script = (
        "local key = KEYS[1]\n"
        "local now_min = tonumber(ARGV[1])\n"
        "local requested = tonumber(ARGV[2])\n"
        "local limit = tonumber(ARGV[3])\n"
        "local window = tonumber(ARGV[4])\n"
        "local buckets = redis.call('HGETALL', key)\n"
        "local total = 0\n"
        "for i = 1, #buckets, 2 do\n"
        "  local bucket = tonumber(buckets[i])\n"
        "  local value = tonumber(buckets[i + 1]) or 0\n"
        "  if bucket == nil then\n"
        "    redis.call('HDEL', key, buckets[i])\n"
        "  elseif bucket < (now_min - window + 1) then\n"
        "    redis.call('HDEL', key, buckets[i])\n"
        "  else\n"
        "    total = total + value\n"
        "  end\n"
        "end\n"
        "if (total + requested) > limit then\n"
        "  return {0, total, window * 60}\n"
        "end\n"
        "redis.call('HINCRBY', key, now_min, requested)\n"
        "redis.call('EXPIRE', key, window * 60 + 120)\n"
        "return {1, total + requested, window * 60}\n"
    )

    result = await redis.eval(script, 1, key, now_minute, requested_tokens, limit, window_minutes)
    allowed_flag = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
    consumed = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
    ttl = int(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else 3600

    return QuotaResult(
        allowed=allowed_flag == 1,
        consumed=consumed,
        limit=limit,
        retry_after=max(ttl, 1),
    )


async def refund_tokens(reservation: TokenReservation, actual_tokens: int) -> None:
    if actual_tokens < 0:
        return
    refund = max(reservation.reserved_tokens - int(actual_tokens), 0)
    if refund <= 0:
        return

    redis = await get_redis()

    daily_script = (
        "local key = KEYS[1]\n"
        "local refund = tonumber(ARGV[1])\n"
        "local current = tonumber(redis.call('GET', key) or '0')\n"
        "local ttl = redis.call('TTL', key)\n"
        "local next = current - refund\n"
        "if next < 0 then next = 0 end\n"
        "redis.call('SET', key, next)\n"
        "if ttl > 0 then redis.call('EXPIRE', key, ttl) end\n"
        "return next\n"
    )
    await redis.eval(daily_script, 1, reservation.daily_key, refund)

    hourly_bucket = str(reservation.hourly_bucket)
    hourly_script = (
        "local key = KEYS[1]\n"
        "local bucket = ARGV[1]\n"
        "local refund = tonumber(ARGV[2])\n"
        "local current = tonumber(redis.call('HGET', key, bucket) or '0')\n"
        "local next = current - refund\n"
        "if next < 0 then next = 0 end\n"
        "redis.call('HSET', key, bucket, next)\n"
        "return next\n"
    )
    await redis.eval(hourly_script, 1, reservation.hourly_key, hourly_bucket, refund)


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


def _quota_keys(identifier: str, mode: str) -> tuple[str, str]:
    mode_label = (mode or "default").strip().lower()
    return (
        f"knowbear:quota:{identifier}:{mode_label}",
        f"knowbear:quota_hour:{identifier}:{mode_label}",
    )


def _resolve_limits(
    *,
    settings,
    is_authenticated: bool,
    is_pro: bool,
    mode: str,
) -> tuple[int, int, int, int, int, int]:
    burst_window = max(int(getattr(settings, "rate_limit_burst_window_seconds", 10)), 1)
    sustained_window = max(int(getattr(settings, "rate_limit_sustained_window_seconds", 60)), 1)
    if not is_authenticated:
        daily_limit = max(int(getattr(settings, "anon_daily_token_quota", 0)), 0)
        hourly_limit = 0
        rpm = max(int(getattr(settings, "anon_rph", 0)), 0)
        return daily_limit, hourly_limit, rpm, 0, 3600, burst_window

    if is_pro:
        daily_limit = max(int(getattr(settings, "pro_daily_token_quota", 0)), 0)
        hourly_limit = max(int(getattr(settings, "pro_hourly_token_quota", 0)), 0)
        rpm = max(int(getattr(settings, "pro_rpm", 0)), 0)
        burst = max(int(getattr(settings, "pro_burst", 0)), 0)
        return daily_limit, hourly_limit, rpm, burst, sustained_window, burst_window

    daily_limit = max(int(getattr(settings, "free_daily_token_quota_learning", 0)), 0)
    hourly_limit = max(int(getattr(settings, "free_hourly_token_quota_learning", 0)), 0)
    rpm = max(int(getattr(settings, "free_rpm_learning", 0)), 0)
    burst = max(int(getattr(settings, "free_burst_learning", 0)), 0)
    return daily_limit, hourly_limit, rpm, burst, sustained_window, burst_window


async def enforce_request_controls(
    *,
    user_id: str | None,
    client_ip: str | None,
    reserved_tokens: int,
    mode: str,
    is_pro: bool = False,
) -> TokenReservation:
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
        identifier = f"user:{user_id}"
    elif client_ip:
        identifier = f"ip:{client_ip}"
    else:
        raise HTTPException(
            status_code=400,
            detail={"type": "missing_client_identifier"},
        )

    daily_limit, hourly_limit, rpm, burst_limit, sustained_window, burst_window = _resolve_limits(
        settings=settings,
        is_authenticated=is_authenticated,
        is_pro=is_pro,
        mode=mode,
    )

    daily_key, hourly_key = _quota_keys(identifier, mode)
    now_minute = int(time.time() // 60)

    try:
        daily_result = await check_daily_quota(
            key=daily_key,
            limit=daily_limit,
            requested=reserved_tokens,
            window_seconds=max(int(getattr(settings, "quota_window_seconds", 86400)), 1),
        )
        hourly_result = await check_hourly_quota(
            key=hourly_key,
            limit=hourly_limit,
            requested=reserved_tokens,
            now_minute=now_minute,
        )
    except Exception as exc:
        logger.warning(
            "quota_check_failed",
            user_id_hash=anonymize_user_id(str(user_id) if user_id is not None else None),
            fail_open=fail_open,
            error=str(exc),
        )
        daily_result = QuotaResult(allowed=True, consumed=0, limit=0, retry_after=0)
        hourly_result = QuotaResult(allowed=True, consumed=0, limit=0, retry_after=0)

    if not daily_result.allowed or not hourly_result.allowed:
        retry_after = max(daily_result.retry_after, hourly_result.retry_after, 1)
        raise HTTPException(
            status_code=429,
            detail={
                "type": "quota_exceeded",
                "retry_allowed": False,
                "limit": max(daily_result.limit, hourly_result.limit),
                "consumed": max(daily_result.consumed, hourly_result.consumed),
            },
            headers={"Retry-After": str(retry_after)},
        )

    if burst_limit > 0:
        burst = await check_rate_limit(
            identifier=identifier,
            limit=burst_limit,
            window_seconds=burst_window,
            namespace="burst",
            fail_open=fail_open,
            mode=mode,
        )
        if not burst.allowed:
            if burst.reason == "degraded_blocked":
                raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"})
            raise HTTPException(
                status_code=429,
                detail={"type": "rate_limit_exceeded", "scope": "burst"},
                headers={"Retry-After": str(burst.retry_after)},
            )

    if rpm > 0:
        sustained = await check_rate_limit(
            identifier=identifier,
            limit=rpm,
            window_seconds=sustained_window,
            namespace="sustained",
            fail_open=fail_open,
            mode=mode,
        )
        if not sustained.allowed:
            if sustained.reason == "degraded_blocked":
                raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"})
            raise HTTPException(
                status_code=429,
                detail={"type": "rate_limit_exceeded", "scope": "sustained"},
                headers={"Retry-After": str(sustained.retry_after)},
            )

    breaker = await check_circuit_breaker(estimated_tokens=reserved_tokens, fail_open=fail_open)
    if not breaker.allowed:
        raise HTTPException(
            status_code=503,
            detail={"type": "circuit_breaker_open", "action": "reject"},
            headers={"Retry-After": str(max(breaker.retry_after, 1))},
        )

    return TokenReservation(
        identifier=identifier,
        mode=mode,
        reserved_tokens=reserved_tokens,
        daily_key=daily_key,
        hourly_key=hourly_key,
        hourly_bucket=now_minute,
        is_anonymous=not is_authenticated,
    )
