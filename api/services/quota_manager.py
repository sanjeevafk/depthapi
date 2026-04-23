"""Quota reservation and accounting helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from api.constants import RATE_LIMIT_HOURLY_WINDOW_MINUTES, RATE_LIMIT_HOURLY_WINDOW_SECONDS
from services.redis_safe import safe_redis_call


@dataclass
class QuotaResult:
    """Result from token quota checks."""

    allowed: bool
    consumed: int
    limit: int
    retry_after: int


@dataclass
class TokenReservation:
    """Reserved token accounting for one request lifecycle."""

    identifier: str
    mode: str
    reserved_tokens: int
    daily_key: str
    hourly_key: str
    hourly_bucket: int
    is_anonymous: bool


class QuotaManager:
    """Encapsulates daily/hourly quota checks and refund logic."""

    def quota_keys(self, identifier: str, mode: str) -> tuple[str, str]:
        mode_label = (mode or "default").strip().lower()
        return (
            f"knowbear:quota:{identifier}:{mode_label}",
            f"knowbear:quota_hour:{identifier}:{mode_label}",
        )

    async def check_daily_quota(
        self,
        *,
        key: str,
        limit: int,
        requested: int,
        window_seconds: int,
        get_redis_fn: Callable[[], Awaitable[Any]],
    ) -> QuotaResult:
        if limit <= 0:
            return QuotaResult(allowed=True, consumed=0, limit=0, retry_after=max(window_seconds, 1))

        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return QuotaResult(allowed=True, consumed=0, limit=limit, retry_after=max(window_seconds, 1))
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

        result = await safe_redis_call(
            redis.eval,
            script,
            1,
            key,
            requested_tokens,
            limit,
            window_seconds,
            operation="eval",
        )
        if not isinstance(result, (list, tuple)):
            return QuotaResult(allowed=True, consumed=0, limit=limit, retry_after=max(window_seconds, 1))
        allowed_flag = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
        consumed = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
        ttl = int(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else window_seconds

        return QuotaResult(
            allowed=allowed_flag == 1,
            consumed=consumed,
            limit=limit,
            retry_after=max(ttl, 1),
        )

    async def check_hourly_quota(
        self,
        *,
        key: str,
        limit: int,
        requested: int,
        now_minute: int,
        get_redis_fn: Callable[[], Awaitable[Any]],
    ) -> QuotaResult:
        if limit <= 0:
            return QuotaResult(allowed=True, consumed=0, limit=0, retry_after=RATE_LIMIT_HOURLY_WINDOW_SECONDS)

        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return QuotaResult(allowed=True, consumed=0, limit=limit, retry_after=RATE_LIMIT_HOURLY_WINDOW_SECONDS)
        requested_tokens = max(int(requested), 1)
        window_minutes = RATE_LIMIT_HOURLY_WINDOW_MINUTES

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

        result = await safe_redis_call(
            redis.eval,
            script,
            1,
            key,
            now_minute,
            requested_tokens,
            limit,
            window_minutes,
            operation="eval",
        )
        if not isinstance(result, (list, tuple)):
            return QuotaResult(allowed=True, consumed=0, limit=limit, retry_after=RATE_LIMIT_HOURLY_WINDOW_SECONDS)
        allowed_flag = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
        consumed = int(result[1]) if isinstance(result, (list, tuple)) and len(result) > 1 else 0
        ttl = int(result[2]) if isinstance(result, (list, tuple)) and len(result) > 2 else RATE_LIMIT_HOURLY_WINDOW_SECONDS

        return QuotaResult(
            allowed=allowed_flag == 1,
            consumed=consumed,
            limit=limit,
            retry_after=max(ttl, 1),
        )

    async def reserve_tokens(
        self,
        *,
        identifier: str,
        mode: str,
        reserved_tokens: int,
        is_anonymous: bool,
        hourly_bucket: int | None = None,
    ) -> TokenReservation:
        now_minute = int(hourly_bucket) if hourly_bucket is not None else int(time.time() // 60)
        daily_key, hourly_key = self.quota_keys(identifier, mode)
        return TokenReservation(
            identifier=identifier,
            mode=mode,
            reserved_tokens=max(int(reserved_tokens), 1),
            daily_key=daily_key,
            hourly_key=hourly_key,
            hourly_bucket=now_minute,
            is_anonymous=is_anonymous,
        )

    async def commit_tokens(self, _reservation: TokenReservation) -> None:
        return None

    async def refund_tokens(
        self,
        reservation: TokenReservation,
        actual_tokens: int,
        *,
        get_redis_fn: Callable[[], Awaitable[Any]],
    ) -> None:
        if actual_tokens < 0:
            return
        refund = max(reservation.reserved_tokens - int(actual_tokens), 0)
        if refund <= 0:
            return

        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return

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
        await safe_redis_call(redis.eval, daily_script, 1, reservation.daily_key, refund, operation="eval")

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
        await safe_redis_call(
            redis.eval,
            hourly_script,
            1,
            reservation.hourly_key,
            hourly_bucket,
            refund,
            operation="eval",
        )
