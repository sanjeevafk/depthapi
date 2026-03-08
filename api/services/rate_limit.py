"""Rate limiting helpers backed by Upstash Redis."""

from dataclasses import dataclass

from logging_config import logger
from services.cache import get_redis


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


async def check_rate_limit(identifier: str, limit: int, window_seconds: int) -> RateLimitResult:
    """Apply a fixed-window rate limit using INCR + EXPIRE."""
    key = f"knowbear:ratelimit:{identifier}"

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
            )

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after=max(ttl, 1),
        )
    except Exception as e:
        # Fail-open to avoid blocking chat if Redis is temporarily unavailable.
        logger.warning("rate_limit_check_failed", identifier=identifier, error=str(e))
        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=limit,
            retry_after=window_seconds,
        )
