"""Rate-limit orchestration for quotas, burst limits, and circuit safety.

Responsibilities:
- Estimate token reservation cost before model invocation.
- Apply unified burst/sustained/quota/circuit controls via Redis Lua.
- Delegate quota accounting and refunds to `QuotaManager`.
- Delegate circuit-state decisions to `CircuitBreaker`.
"""

import time
from typing import Any

from fastapi import HTTPException

from api.services.circuit_breaker import CircuitBreaker, CircuitBreakerResult
from api.config import get_settings
from api.logging_config import anonymize_user_id, logger
from api.services.cache import get_redis
from api.services.api_key_auth import ApiKeyRecord, PLAN_MONTHLY_BUDGETS, PLAN_RPM
from api.services.quota_manager import QuotaManager, QuotaResult, TokenReservation
from api.services.redis_safe import safe_redis_call
from api.services.token_count import count_prompt_tokens

UNIFIED_CONTROLS_SCRIPT = """
-- unified_controls
-- Returns: {burst_ok, sustained_ok, daily_ok, hourly_ok, circuit_ok,
--           burst_count, sustained_count, daily_consumed, hourly_consumed,
--           burst_ttl, sustained_ttl, daily_ttl, hourly_ttl, circuit_ttl}
local burst_key = KEYS[1]
local sustained_key = KEYS[2]
local daily_key = KEYS[3]
local hourly_key = KEYS[4]
local circuit_open_key = KEYS[5]
local circuit_usage_key = KEYS[6]

local burst_limit = tonumber(ARGV[1])
local sustained_limit = tonumber(ARGV[2])
local daily_limit = tonumber(ARGV[3])
local hourly_limit = tonumber(ARGV[4])
local circuit_threshold = tonumber(ARGV[5])
local requested_tokens = tonumber(ARGV[6])
local now_minute = tonumber(ARGV[7])
local burst_window = tonumber(ARGV[8])
local sustained_window = tonumber(ARGV[9])
local daily_window = tonumber(ARGV[10])
local hourly_window = tonumber(ARGV[11])
local circuit_open_seconds = tonumber(ARGV[12])

local burst_ok = 1
local sustained_ok = 1
local daily_ok = 1
local hourly_ok = 1
local circuit_ok = 1

local burst_count = 0
local sustained_count = 0
local daily_consumed = 0
local hourly_consumed = 0

local burst_ttl = 0
local sustained_ttl = 0
local daily_ttl = 0
local hourly_ttl = 0
local circuit_ttl = 0

if circuit_threshold and circuit_threshold > 0 then
    local is_circuit_open = redis.call('GET', circuit_open_key)
    if is_circuit_open then
        circuit_ttl = redis.call('TTL', circuit_open_key)
        return {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, circuit_ttl}
    end
end

if burst_limit and burst_limit > 0 then
    burst_count = tonumber(redis.call('INCR', burst_key))
    if burst_count == 1 then
        redis.call('EXPIRE', burst_key, burst_window)
    end
    burst_ttl = redis.call('TTL', burst_key)
    if burst_ttl < 0 then
        redis.call('EXPIRE', burst_key, burst_window)
        burst_ttl = burst_window
    end
    burst_ok = (burst_count <= burst_limit) and 1 or 0
end

if sustained_limit and sustained_limit > 0 then
    sustained_count = tonumber(redis.call('INCR', sustained_key))
    if sustained_count == 1 then
        redis.call('EXPIRE', sustained_key, sustained_window)
    end
    sustained_ttl = redis.call('TTL', sustained_key)
    if sustained_ttl < 0 then
        redis.call('EXPIRE', sustained_key, sustained_window)
        sustained_ttl = sustained_window
    end
    sustained_ok = (sustained_count <= sustained_limit) and 1 or 0
end

if daily_limit and daily_limit > 0 then
    daily_consumed = tonumber(redis.call('GET', daily_key) or '0')
    local daily_new = daily_consumed + requested_tokens
    daily_ok = (daily_new <= daily_limit) and 1 or 0
    if daily_ok == 1 then
        redis.call('INCRBY', daily_key, requested_tokens)
        daily_ttl = redis.call('TTL', daily_key)
        if daily_ttl < 0 then
            redis.call('EXPIRE', daily_key, daily_window)
            daily_ttl = daily_window
        end
        daily_consumed = daily_new
    else
        daily_ttl = redis.call('TTL', daily_key)
        if daily_ttl < 0 then
            daily_ttl = daily_window
        end
    end
end

if hourly_limit and hourly_limit > 0 then
    local buckets = redis.call('HGETALL', hourly_key)
    hourly_consumed = 0
    local stale_before = now_minute - hourly_window + 1
    for i = 1, #buckets, 2 do
        local bucket = tonumber(buckets[i])
        local value = tonumber(buckets[i + 1]) or 0
        if bucket == nil then
            redis.call('HDEL', hourly_key, buckets[i])
        elseif bucket < stale_before then
            redis.call('HDEL', hourly_key, buckets[i])
        else
            hourly_consumed = hourly_consumed + value
        end
    end

    local hourly_new = hourly_consumed + requested_tokens
    hourly_ok = (hourly_new <= hourly_limit) and 1 or 0
    if hourly_ok == 1 then
        redis.call('HINCRBY', hourly_key, now_minute, requested_tokens)
        redis.call('EXPIRE', hourly_key, hourly_window * 60 + 120)
        hourly_ttl = hourly_window * 60
        hourly_consumed = hourly_new
    else
        hourly_ttl = hourly_window * 60
    end
end

if circuit_threshold and circuit_threshold > 0 then
    local circuit_usage = tonumber(redis.call('INCRBY', circuit_usage_key, requested_tokens))
    if circuit_usage <= requested_tokens then
        redis.call('EXPIRE', circuit_usage_key, 120)
    end
    if circuit_usage > circuit_threshold then
        redis.call('SETEX', circuit_open_key, circuit_open_seconds, '1')
        circuit_ok = 0
        circuit_ttl = circuit_open_seconds
    end
end

return {burst_ok, sustained_ok, daily_ok, hourly_ok, circuit_ok,
                burst_count, sustained_count, daily_consumed, hourly_consumed,
                burst_ttl, sustained_ttl, daily_ttl, hourly_ttl, circuit_ttl}
"""
class RateLimitResult:
    """Result from request-rate checks."""

    def __init__(self, allowed: bool, limit: int, remaining: int, retry_after: int, reason: str = "ok") -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.retry_after = retry_after
        self.reason = reason


_quota_manager = QuotaManager()
_circuit_breaker = CircuitBreaker()


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
    key = f"depthapi:ratelimit:{namespace}:{identifier}:{mode_label}"

    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            raise RuntimeError("redis unavailable")
        script = (
            "local count = redis.call('INCR', KEYS[1])\n"
            "local ttl = redis.call('TTL', KEYS[1])\n"
            "if ttl < 0 then\n"
            "  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))\n"
            "  ttl = tonumber(ARGV[1])\n"
            "end\n"
            "return {count, ttl}\n"
        )
        result = await safe_redis_call(redis.eval, script, 1, key, window_seconds, operation="eval")
        if not isinstance(result, (list, tuple)):
            raise RuntimeError("redis result unavailable")
        count = int(result[0] if result else 0)
        ttl = int(result[1] if result and len(result) > 1 else window_seconds)

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
    return await _quota_manager.check_daily_quota(
        key=key,
        limit=limit,
        requested=requested,
        window_seconds=window_seconds,
        get_redis_fn=get_redis,
    )


async def check_hourly_quota(*, key: str, limit: int, requested: int, now_minute: int) -> QuotaResult:
    return await _quota_manager.check_hourly_quota(
        key=key,
        limit=limit,
        requested=requested,
        now_minute=now_minute,
        get_redis_fn=get_redis,
    )


async def refund_tokens(reservation: TokenReservation, actual_tokens: int) -> None:
    await _quota_manager.refund_tokens(
        reservation,
        actual_tokens,
        get_redis_fn=get_redis,
    )


async def check_circuit_breaker(*, estimated_tokens: int, fail_open: bool) -> CircuitBreakerResult:
    settings = get_settings()
    threshold = max(int(getattr(settings, "circuit_breaker_tokens_per_minute", 0)), 0)
    open_seconds = max(int(getattr(settings, "circuit_breaker_open_seconds", 60)), 1)
    action = str(getattr(settings, "circuit_breaker_action", "reject") or "reject").lower()
    return await _circuit_breaker.should_allow_request(
        estimated_tokens=estimated_tokens,
        fail_open=fail_open,
        threshold=threshold,
        open_seconds=open_seconds,
        action=action,
        get_redis_fn=get_redis,
    )


def _quota_keys(identifier: str, mode: str) -> tuple[str, str]:
    return _quota_manager.quota_keys(identifier, mode)


def _resolve_limits(
    *,
    settings: Any,
    api_key: ApiKeyRecord,
) -> tuple[int, int, int, int, int, int]:
    """Resolve token quotas and rate limits based on the API key plan and overrides."""
    burst_window = max(int(getattr(settings, "rate_limit_burst_window_seconds", 10)), 1)
    sustained_window = max(int(getattr(settings, "rate_limit_sustained_window_seconds", 60)), 1)

    # Monthly budget from key (or plan default) / 30 for approximate daily floor
    # Enterprise keys with 0/Unlimited budget skip daily quota in Lua script if daily_limit=0
    monthly_budget = api_key.monthly_token_budget
    daily_limit = int(monthly_budget / 30) if monthly_budget > 0 else 0
    
    # Hourly is 1/6th of daily
    hourly_limit = int(daily_limit / 6) if daily_limit > 0 else 0
    
    # RPM from key or plan default
    rpm = api_key.requests_per_minute or PLAN_RPM.get(api_key.plan, 10)
    
    # Burst is 1.5x RPM by default
    burst = int(rpm * 1.5)
    
    return daily_limit, hourly_limit, rpm, burst, sustained_window, burst_window


async def enforce_request_controls(
    *,
    user_id: str,  # This is the api_key.id in the new system
    client_ip: str | None,
    api_key: ApiKeyRecord | None = None, # Added for direct limit resolution
    reserved_tokens: int | None = None,
    estimated_tokens: int | None = None,
    mode: str = "learn",
    is_pro: bool = False,
) -> TokenReservation:
    """Apply API-key scoped quota, distributed rate limiting, and circuit breaker checks."""
    settings = get_settings()
    strategy = str(getattr(settings, "rate_limit_strategy", "upstash_redis") or "upstash_redis").lower()
    if strategy != "upstash_redis":
        logger.warning("unsupported_rate_limit_strategy", strategy=strategy)

    # In DepthAPI, all requests MUST be authenticated with an API key.
    # The user_id passed here is api_key.id.
    identifier = f"key:{user_id}"
    fail_open = True # Always fail-open for authenticated B2B keys to avoid blocking business traffic on Redis blips

    if api_key:
        daily_limit, hourly_limit, rpm, burst_limit, sustained_window, burst_window = _resolve_limits(
            settings=settings,
            api_key=api_key
        )
    else:
        # Fallback for code paths where the record wasn't passed down (deprecated)
        daily_limit, hourly_limit, rpm, burst_limit, sustained_window, burst_window = (
            10000, 2000, 10, 15, 60, 10
        )

    mode_label = (mode or "default").strip().lower()
    burst_key = f"depthapi:ratelimit:burst:{identifier}:{mode_label}"
    sustained_key = f"depthapi:ratelimit:sustained:{identifier}:{mode_label}"
    daily_key, hourly_key = _quota_keys(identifier, mode)
    now_minute = int(time.time() // 60)

    daily_window = max(int(getattr(settings, "quota_window_seconds", 86400)), 1)
    circuit_threshold = max(int(getattr(settings, "circuit_breaker_tokens_per_minute", 0)), 0)
    circuit_open_seconds = max(int(getattr(settings, "circuit_breaker_open_seconds", 60)), 1)
    circuit_action = str(getattr(settings, "circuit_breaker_action", "reject") or "reject").lower()
    if circuit_action != "reject":
        circuit_threshold = 0

    token_hint = reserved_tokens if reserved_tokens is not None else estimated_tokens
    requested_tokens = max(int(token_hint or 0), 1)

    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            raise RuntimeError("redis unavailable")
        result = await safe_redis_call(
            redis.eval,
            UNIFIED_CONTROLS_SCRIPT,
            6,
            burst_key,
            sustained_key,
            daily_key,
            hourly_key,
            "depthapi:circuit:open",
            f"depthapi:circuit:tokens:{now_minute}",
            burst_limit,
            rpm,
            daily_limit,
            hourly_limit,
            circuit_threshold,
            requested_tokens,
            now_minute,
            burst_window,
            sustained_window,
            daily_window,
            60,
            circuit_open_seconds,
            operation="eval",
        )
        if not isinstance(result, (list, tuple)):
            raise RuntimeError("redis result unavailable")
    except Exception as exc:
        logger.warning(
            "unified_controls_failed",
            user_id_hash=anonymize_user_id(str(user_id) if user_id is not None else None),
            fail_open=fail_open,
            error=str(exc),
        )
        if fail_open:
            return await _quota_manager.reserve_tokens(
                identifier=identifier,
                mode=mode,
                reserved_tokens=requested_tokens,
                is_anonymous=not is_authenticated,
                hourly_bucket=now_minute,
            )
        raise HTTPException(status_code=503, detail={"type": "rate_limiter_unavailable"})

    burst_ok = bool(int(result[0])) if len(result) > 0 else True
    sustained_ok = bool(int(result[1])) if len(result) > 1 else True
    daily_ok = bool(int(result[2])) if len(result) > 2 else True
    hourly_ok = bool(int(result[3])) if len(result) > 3 else True
    circuit_ok = bool(int(result[4])) if len(result) > 4 else True

    daily_consumed = int(result[7]) if len(result) > 7 else 0
    hourly_consumed = int(result[8]) if len(result) > 8 else 0
    burst_ttl = int(result[9]) if len(result) > 9 else burst_window
    sustained_ttl = int(result[10]) if len(result) > 10 else sustained_window
    daily_ttl = int(result[11]) if len(result) > 11 else daily_window
    hourly_ttl = int(result[12]) if len(result) > 12 else 3600
    circuit_ttl = int(result[13]) if len(result) > 13 else circuit_open_seconds

    if not daily_ok or not hourly_ok:
        retry_after = max(daily_ttl, hourly_ttl, 1)
        raise HTTPException(
            status_code=429,
            detail={
                "type": "quota_exceeded",
                "retry_allowed": False,
                "limit": max(daily_limit, hourly_limit),
                "consumed": max(daily_consumed, hourly_consumed),
            },
            headers={"Retry-After": str(retry_after)},
        )

    if not burst_ok:
        raise HTTPException(
            status_code=429,
            detail={"type": "rate_limit_exceeded", "scope": "burst"},
            headers={"Retry-After": str(max(burst_ttl, 1))},
        )

    if not sustained_ok:
        raise HTTPException(
            status_code=429,
            detail={"type": "rate_limit_exceeded", "scope": "sustained"},
            headers={"Retry-After": str(max(sustained_ttl, 1))},
        )

    if not circuit_ok:
        raise HTTPException(
            status_code=503,
            detail={"type": "circuit_breaker_open", "action": "reject"},
            headers={"Retry-After": str(max(circuit_ttl, 1))},
        )

    return await _quota_manager.reserve_tokens(
        identifier=identifier,
        mode=mode,
        reserved_tokens=requested_tokens,
        is_anonymous=not is_authenticated,
        hourly_bucket=now_minute,
    )
