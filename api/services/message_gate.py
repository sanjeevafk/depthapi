import time
from dataclasses import dataclass
from typing import Any

from api.constants import (
    MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
    STREAM_IDEMPOTENCY_STALE_MIN_SECONDS,
    STREAM_IDEMPOTENCY_TTL_MAX_SECONDS,
    STREAM_IDEMPOTENCY_TTL_MIN_SECONDS,
)
from api.config import get_settings
from api.logging_config import logger
from services.cache import get_redis
from services.redis_safe import safe_redis_call


@dataclass
class GatekeeperResult:
    allowed: bool
    retry_after: int
    idempotency_status: str | None
    idempotency_response: str | None
    degraded: bool
    redis_eval_ms: float | None


GATEKEEP_LUA = """
-- KEYS:
-- 1) token_bucket_key
-- 2) quota_key
-- 3) circuit_minute_key
-- 4) circuit_open_key
-- 5) idempotency_key
-- ARGV:
-- 1) now_ts
-- 2) bucket_capacity
-- 3) refill_per_sec
-- 4) bucket_cost
-- 5) quota_limit
-- 6) quota_window
-- 7) reserved_tokens
-- 8) circuit_threshold
-- 9) circuit_open_seconds
-- 10) idempotency_ttl
-- 11) idempotency_stale

local now_ts = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_per_sec = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local quota_limit = tonumber(ARGV[5])
local quota_window = tonumber(ARGV[6])
local reserved_tokens = tonumber(ARGV[7])
local circuit_threshold = tonumber(ARGV[8])
local circuit_open_seconds = tonumber(ARGV[9])
local idempotency_ttl = tonumber(ARGV[10])
local idempotency_stale = tonumber(ARGV[11])

-- Idempotency check (hash)
local status = redis.call('HGET', KEYS[5], 'status')
if status == 'COMPLETED' then
  local response = redis.call('HGET', KEYS[5], 'response')
  return {1, 0, 'COMPLETED', response}
elseif status == 'PENDING' then
  local started_at = tonumber(redis.call('HGET', KEYS[5], 'started_at') or '0')
  if started_at > 0 and (now_ts - started_at) < idempotency_stale then
    local ttl = redis.call('TTL', KEYS[5])
    if ttl < 0 then ttl = idempotency_ttl end
    return {0, ttl, 'PENDING', nil}
  end
  redis.call('HSET', KEYS[5], 'status', 'EXPIRED')
  redis.call('HSET', KEYS[5], 'expired_at', now_ts)
end

-- Circuit breaker
if circuit_threshold > 0 then
  local is_open = redis.call('GET', KEYS[4])
  if is_open then
    local ttl = redis.call('TTL', KEYS[4])
    if ttl < 0 then ttl = circuit_open_seconds end
    return {0, ttl, 'CIRCUIT_OPEN', nil}
  end
end

-- Token bucket
if capacity > 0 then
  local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or tostring(capacity))
  local last_ts = tonumber(redis.call('HGET', KEYS[1], 'last_ts') or tostring(now_ts))
  local delta = math.max(0, now_ts - last_ts)
  local refill = delta * refill_per_sec
  local new_tokens = math.min(capacity, tokens + refill)
  if new_tokens < cost then
    local retry_after = math.ceil((cost - new_tokens) / refill_per_sec)
    return {0, retry_after, 'RATE_LIMITED', nil}
  end
  redis.call('HSET', KEYS[1], 'tokens', new_tokens - cost)
  redis.call('HSET', KEYS[1], 'last_ts', now_ts)
  redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill_per_sec) + 10)
end

-- Quota
if quota_limit > 0 then
  local consumed = tonumber(redis.call('GET', KEYS[2]) or '0')
  if (consumed + reserved_tokens) > quota_limit then
    local ttl = redis.call('TTL', KEYS[2])
    if ttl < 0 then ttl = quota_window end
    return {0, ttl, 'QUOTA_EXCEEDED', nil}
  end
  local new_total = redis.call('INCRBY', KEYS[2], reserved_tokens)
  if new_total == reserved_tokens then
    redis.call('EXPIRE', KEYS[2], quota_window)
  end
end

-- Circuit breaker increment
if circuit_threshold > 0 then
  local total = tonumber(redis.call('INCRBY', KEYS[3], reserved_tokens))
  if total == reserved_tokens then
    redis.call('EXPIRE', KEYS[3], 120)
  end
  if total > circuit_threshold then
    redis.call('SETEX', KEYS[4], circuit_open_seconds, '1')
    return {0, circuit_open_seconds, 'CIRCUIT_OPEN', nil}
  end
end

-- Set idempotency PENDING
redis.call('HSET', KEYS[5], 'status', 'PENDING')
redis.call('HSET', KEYS[5], 'started_at', now_ts)
redis.call('EXPIRE', KEYS[5], idempotency_ttl)

return {1, 0, 'PENDING', nil}
"""


APPEND_MESSAGE_LUA = """
-- KEYS:
-- 1) sequence_key
-- 2) messages_key
local seq = redis.call('INCR', KEYS[1])
local payload_obj = cjson.decode(ARGV[1])
payload_obj.sequence_id = seq
local payload = cjson.encode(payload_obj)
redis.call('RPUSH', KEYS[2], payload)
if tonumber(ARGV[2]) > 0 then
  redis.call('LTRIM', KEYS[2], -tonumber(ARGV[2]), -1)
end
local ttl = tonumber(ARGV[3]) or 0
if ttl > 0 then
  redis.call('EXPIRE', KEYS[1], ttl)
  redis.call('EXPIRE', KEYS[2], ttl)
end
return seq
"""


SNAPSHOT_LUA = """
-- KEYS:
-- 1) meta_key
-- 2) messages_key
-- ARGV:
-- 1) max_messages
local meta = redis.call('GET', KEYS[1])
local msgs = redis.call('LRANGE', KEYS[2], -tonumber(ARGV[1]), -1)
return {meta, msgs}
"""

CACHE_GET_LUA = "return redis.call('GET', KEYS[1])"
CACHE_SET_LUA = "return redis.call('SETEX', KEYS[1], ARGV[1], ARGV[2])"


async def gatekeep_message_request(
    *,
    identifier: str,
    reserved_tokens: int,
    token_bucket_capacity: int,
    token_bucket_refill_per_sec: float,
    token_bucket_cost: int,
    daily_quota_limit: int,
    daily_quota_window: int,
    circuit_threshold: int,
    circuit_open_seconds: int,
    idempotency_key: str,
    timeout_seconds: float = MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
) -> GatekeeperResult:
    """Gate incoming message requests using idempotency and quota checks."""
    settings = get_settings()
    now_ts = int(time.time())
    token_bucket_key = f"depthapi:rate:bucket:{identifier}"
    quota_key = f"depthapi:quota:daily:{identifier}"
    circuit_minute_key = f"depthapi:circuit:tokens:{int(now_ts // 60)}"
    circuit_open_key = "depthapi:circuit:open"
    idempotency_ttl = min(
        max(int(getattr(settings, "stream_idempotency_ttl_seconds", 90)), STREAM_IDEMPOTENCY_TTL_MIN_SECONDS),
        STREAM_IDEMPOTENCY_TTL_MAX_SECONDS,
    )
    idempotency_stale = max(
        STREAM_IDEMPOTENCY_STALE_MIN_SECONDS,
        min(int(getattr(settings, "stream_idempotency_stale_seconds", 20)), idempotency_ttl),
    )

    args = [
        now_ts,
        token_bucket_capacity,
        token_bucket_refill_per_sec,
        token_bucket_cost,
        daily_quota_limit,
        daily_quota_window,
        reserved_tokens,
        circuit_threshold,
        circuit_open_seconds,
        idempotency_ttl,
        idempotency_stale,
    ]

    redis = await safe_redis_call(get_redis, timeout=timeout_seconds, operation="connect")
    if redis is None:
        return GatekeeperResult(
            allowed=True,
            retry_after=0,
            idempotency_status=None,
            idempotency_response=None,
            degraded=True,
            redis_eval_ms=0.0,
        )
    start = time.perf_counter()
    try:
        result = await safe_redis_call(
            redis.eval,
            GATEKEEP_LUA,
            5,
            token_bucket_key,
            quota_key,
            circuit_minute_key,
            circuit_open_key,
            idempotency_key,
            *args,
            timeout=timeout_seconds,
            operation="eval",
        )
        if result is None:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return GatekeeperResult(
                allowed=True,
                retry_after=0,
                idempotency_status=None,
                idempotency_response=None,
                degraded=True,
                redis_eval_ms=round(elapsed_ms, 2),
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        allowed = bool(int(result[0])) if isinstance(result, (list, tuple)) else False
        retry_after = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
        status = str(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else None
        response = result[3] if isinstance(result, (list, tuple)) and len(result) > 3 else None
        return GatekeeperResult(
            allowed=allowed,
            retry_after=max(retry_after, 0),
            idempotency_status=status,
            idempotency_response=str(response) if response else None,
            degraded=False,
            redis_eval_ms=round(elapsed_ms, 2),
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "messages_gatekeeper_failed",
            identifier=identifier,
            error=str(exc),
        )
        return GatekeeperResult(
            allowed=True,
            retry_after=0,
            idempotency_status=None,
            idempotency_response=None,
            degraded=True,
            redis_eval_ms=round(elapsed_ms, 2),
        )


async def append_conversation_message(
    *,
    conversation_id: str,
    message_json: str,
    max_messages: int,
    timeout_seconds: float = MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
) -> int | None:
    settings = get_settings()
    ttl_seconds = int(getattr(settings, "message_cache_ttl_seconds", 3600))
    seq_key = f"depthapi:conversation:{conversation_id}:seq"
    list_key = f"depthapi:conversation:{conversation_id}:messages"
    redis = await safe_redis_call(get_redis, timeout=timeout_seconds, operation="connect")
    if redis is None:
        return None
    try:
        result = await safe_redis_call(
            redis.eval,
            APPEND_MESSAGE_LUA,
            2,
            seq_key,
            list_key,
            message_json,
            max_messages,
            ttl_seconds,
            timeout=timeout_seconds,
            operation="eval",
        )
        if result is None:
            return None
        return int(result) if result is not None else None
    except Exception as exc:
        logger.warning(
            "messages_redis_append_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
        return None


async def fetch_conversation_snapshot(
    *,
    conversation_id: str,
    max_messages: int,
    timeout_seconds: float = MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str | None, list[str]]:
    meta_key = f"depthapi:conversation:{conversation_id}:meta"
    list_key = f"depthapi:conversation:{conversation_id}:messages"
    redis = await safe_redis_call(get_redis, timeout=timeout_seconds, operation="connect")
    if redis is None:
        return (None, [])
    try:
        result = await safe_redis_call(
            redis.eval,
            SNAPSHOT_LUA,
            2,
            meta_key,
            list_key,
            max_messages,
            timeout=timeout_seconds,
            operation="eval",
        )
        if result is None:
            return (None, [])
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            meta = result[0]
            msgs = result[1] if isinstance(result[1], list) else []
            return (str(meta) if meta else None, [str(item) for item in msgs])
        return (None, [])
    except Exception as exc:
        logger.warning(
            "messages_redis_snapshot_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
        return (None, [])


async def cache_get_value(
    key: str,
    *,
    timeout_seconds: float = MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    redis = await safe_redis_call(get_redis, timeout=timeout_seconds, operation="connect")
    if redis is None:
        return None
    try:
        result = await safe_redis_call(
            redis.eval,
            CACHE_GET_LUA,
            1,
            key,
            timeout=timeout_seconds,
            operation="eval",
        )
        if result is None:
            return None
        return str(result) if result is not None else None
    except Exception as exc:
        logger.warning("messages_cache_get_failed", key=key, error=str(exc))
        return None


async def cache_set_value(
    key: str,
    value: str,
    ttl_seconds: int,
    *,
    timeout_seconds: float = MESSAGE_GATE_DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    redis = await safe_redis_call(get_redis, timeout=timeout_seconds, operation="connect")
    if redis is None:
        return False
    try:
        result = await safe_redis_call(
            redis.eval,
            CACHE_SET_LUA,
            1,
            key,
            ttl_seconds,
            value,
            timeout=timeout_seconds,
            operation="eval",
        )
        return result is not None
    except Exception as exc:
        logger.warning("messages_cache_set_failed", key=key, error=str(exc))
        return False
