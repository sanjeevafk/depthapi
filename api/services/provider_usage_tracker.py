"""Provider usage accounting and runtime limit checks."""

from __future__ import annotations

import time

from api.config import get_settings
from api.constants import PROVIDER_USAGE_TTL_SECONDS
from api.logging_config import logger
from api.services.cache import get_redis
from api.services.redis_safe import safe_redis_call
from api.services.provider_registry import ProviderName

OPENROUTER_DAILY_REQUEST_LIMIT = 45
CEREBRAS_MIN_TOKENS_REMAINING = 10000
CEREBRAS_DAILY_TOKEN_BUDGET_DEFAULT = 100000


class ProviderUsageTracker:
    def _day_bucket(self) -> str:
        return time.strftime("%Y%m%d", time.gmtime())

    def _provider_requests_key(self, provider: ProviderName) -> str:
        return f"knowbear:provider_usage:{provider}:requests:{self._day_bucket()}"

    def _provider_tokens_key(self, provider: ProviderName) -> str:
        return f"knowbear:provider_usage:{provider}:tokens:{self._day_bucket()}"

    async def record_usage(self, provider: ProviderName, usage: dict[str, int] | None) -> None:
        try:
            redis = await safe_redis_call(get_redis, operation="connect")
            if redis is None:
                return
            request_key = self._provider_requests_key(provider)
            raw_requests_total = await safe_redis_call(redis.incrby, request_key, 1, operation="incrby")
            requests_total = int(raw_requests_total or 0)
            if requests_total <= 1:
                await safe_redis_call(redis.expire, request_key, PROVIDER_USAGE_TTL_SECONDS, operation="expire")

            total_tokens = int((usage or {}).get("total_tokens") or 0)
            if total_tokens > 0:
                token_key = self._provider_tokens_key(provider)
                raw_token_total = await safe_redis_call(redis.incrby, token_key, total_tokens, operation="incrby")
                token_total = int(raw_token_total or 0)
                if token_total <= total_tokens:
                    await safe_redis_call(redis.expire, token_key, PROVIDER_USAGE_TTL_SECONDS, operation="expire")
        except Exception as exc:
            # Never block inference on usage accounting.
            logger.debug("provider_usage_tracking_failed", provider=provider, error=str(exc))

    async def within_runtime_limits(self, provider: ProviderName) -> bool:
        try:
            redis = await safe_redis_call(get_redis, operation="connect")
            if redis is None:
                return True
            if provider == "openrouter":
                req_count_raw = await safe_redis_call(
                    redis.get,
                    self._provider_requests_key("openrouter"),
                    operation="get",
                )
                req_count = int(req_count_raw or 0)
                if req_count >= OPENROUTER_DAILY_REQUEST_LIMIT:
                    logger.warning(
                        "provider_runtime_limit_reached",
                        provider=provider,
                        limit_type="daily_requests",
                        request_count=req_count,
                        limit=OPENROUTER_DAILY_REQUEST_LIMIT,
                    )
                    return False

            if provider == "cerebras":
                settings = get_settings()
                budget = max(int(getattr(settings, "cerebras_daily_token_budget", CEREBRAS_DAILY_TOKEN_BUDGET_DEFAULT)), 0)
                used_tokens_raw = await safe_redis_call(
                    redis.get,
                    self._provider_tokens_key("cerebras"),
                    operation="get",
                )
                used_tokens = int(used_tokens_raw or 0)
                remaining = max(budget - used_tokens, 0)
                if remaining < CEREBRAS_MIN_TOKENS_REMAINING:
                    logger.warning(
                        "provider_runtime_limit_reached",
                        provider=provider,
                        limit_type="remaining_tokens",
                        remaining_tokens=remaining,
                        min_required=CEREBRAS_MIN_TOKENS_REMAINING,
                    )
                    return False
        except Exception as exc:
            # Fail open when runtime limits cannot be read.
            logger.debug("provider_runtime_limits_read_failed", provider=provider, error=str(exc))
            return True
        return True

    async def record_tokens(self, provider: ProviderName, tokens: int) -> None:
        await self.record_usage(provider, {"total_tokens": max(int(tokens), 0)})

    async def get_daily_usage(self, provider: ProviderName, user_id: str) -> dict[str, int | str]:
        _ = user_id
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            return {"provider": provider, "requests": 0, "total_tokens": 0}
        requests_raw = await safe_redis_call(redis.get, self._provider_requests_key(provider), operation="get")
        tokens_raw = await safe_redis_call(redis.get, self._provider_tokens_key(provider), operation="get")
        return {
            "provider": provider,
            "requests": int(requests_raw or 0),
            "total_tokens": int(tokens_raw or 0),
        }

    def get_cost_estimate(self, provider: str, tokens: int) -> float:
        # Coarse default estimate; provider-specific pricing can be wired later.
        _ = provider
        return float(max(int(tokens), 0)) * 0.000001

    async def is_rate_limited(self, provider: ProviderName) -> bool:
        return not await self.within_runtime_limits(provider)

    async def mark_rate_limited(self, provider: ProviderName, reset_time: int) -> None:
        _ = reset_time
        logger.warning("provider_manually_marked_rate_limited", provider=provider)
