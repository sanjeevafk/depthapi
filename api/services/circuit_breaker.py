"""Circuit breaker extraction for rate limiting and protective throttling."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from api.logging_config import logger
from services.redis_safe import safe_redis_call


@dataclass
class CircuitBreakerResult:
    """Result from circuit-breaker checks."""

    allowed: bool
    retry_after: int


@dataclass
class CircuitState:
    """Snapshot of circuit-breaker state."""

    is_open: bool
    retry_after: int


class CircuitBreaker:
    """Encapsulates circuit-breaker state machine backed by Redis."""

    @staticmethod
    def usage_key(now_minute: int) -> str:
        return f"knowbear:circuit:tokens:{now_minute}"

    @staticmethod
    def open_key() -> str:
        return "knowbear:circuit:open"

    async def should_allow_request(
        self,
        *,
        estimated_tokens: int,
        fail_open: bool,
        threshold: int,
        open_seconds: int,
        action: str,
        get_redis_fn: Callable[[], Awaitable[Any]],
    ) -> CircuitBreakerResult:
        if threshold <= 0:
            return CircuitBreakerResult(allowed=True, retry_after=0)
        if (action or "reject").lower() != "reject":
            return CircuitBreakerResult(allowed=True, retry_after=0)

        minute_bucket = int(time.time() // 60)
        usage_key = self.usage_key(minute_bucket)
        open_key = self.open_key()

        try:
            redis = await safe_redis_call(get_redis_fn, operation="connect")
            if redis is None:
                raise RuntimeError("redis unavailable")
            script = (
                "local open = redis.call('GET', KEYS[2])\n"
                "if open then\n"
                "  local ttl = redis.call('TTL', KEYS[2])\n"
                "  if ttl < 0 then ttl = tonumber(ARGV[3]) end\n"
                "  return {0, ttl}\n"
                "end\n"
                "local total = redis.call('INCRBY', KEYS[1], tonumber(ARGV[1]))\n"
                "if total <= tonumber(ARGV[1]) then\n"
                "  redis.call('EXPIRE', KEYS[1], 120)\n"
                "end\n"
                "if total > tonumber(ARGV[2]) then\n"
                "  redis.call('SETEX', KEYS[2], tonumber(ARGV[3]), '1')\n"
                "  return {0, tonumber(ARGV[3])}\n"
                "end\n"
                "return {1, 0}\n"
            )
            result = await safe_redis_call(
                redis.eval,
                script,
                2,
                usage_key,
                open_key,
                max(int(estimated_tokens), 1),
                max(int(threshold), 0),
                max(int(open_seconds), 1),
                operation="eval",
            )
            if not isinstance(result, (list, tuple)):
                raise RuntimeError("redis result unavailable")
            allowed_flag = int(result[0] if result else 0)
            retry_after = int(result[1] if result and len(result) > 1 else 1)

            if allowed_flag == 0:
                return CircuitBreakerResult(allowed=False, retry_after=max(retry_after, 1))
            return CircuitBreakerResult(allowed=True, retry_after=0)
        except Exception as exc:
            logger.warning("circuit_breaker_check_failed", fail_open=fail_open, error=str(exc))
            if fail_open:
                return CircuitBreakerResult(allowed=True, retry_after=0)
            return CircuitBreakerResult(allowed=False, retry_after=1)

    async def mark_failure(
        self,
        *,
        open_seconds: int,
        get_redis_fn: Callable[[], Awaitable[Any]],
    ) -> None:
        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return
        await safe_redis_call(redis.setex, self.open_key(), max(int(open_seconds), 1), "1", operation="setex")

    async def mark_success(self, *, get_redis_fn: Callable[[], Awaitable[Any]]) -> None:
        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return
        await safe_redis_call(redis.delete, self.open_key(), operation="delete")

    async def reset(self, *, get_redis_fn: Callable[[], Awaitable[Any]]) -> None:
        await self.mark_success(get_redis_fn=get_redis_fn)

    async def get_state(self, *, get_redis_fn: Callable[[], Awaitable[Any]]) -> CircuitState:
        redis = await safe_redis_call(get_redis_fn, operation="connect")
        if redis is None:
            return CircuitState(is_open=False, retry_after=0)
        value = await safe_redis_call(redis.get, self.open_key(), operation="get")
        if value is None:
            return CircuitState(is_open=False, retry_after=0)
        ttl = await safe_redis_call(redis.ttl, self.open_key(), operation="ttl")
        return CircuitState(is_open=True, retry_after=max(int(ttl or 1), 1))
