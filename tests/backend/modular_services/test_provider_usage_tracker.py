from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.provider_usage_tracker as tracker_module
from services.provider_usage_tracker import ProviderUsageTracker


class DummyRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        value = int(self.store.get(key, 0)) + int(amount)
        self.store[key] = value
        return value

    async def expire(self, key: str, _ttl: int) -> bool:
        self.store.setdefault(key, int(self.store.get(key, 0)))
        return True

    async def get(self, key: str):
        return self.store.get(key)


@pytest.mark.asyncio
async def test_record_usage_increments_request_and_token_counters(monkeypatch) -> None:
    redis = DummyRedis()

    async def fake_safe_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(tracker_module, "safe_redis_call", fake_safe_call)
    monkeypatch.setattr(tracker_module, "get_redis", fake_get_redis)

    tracker = ProviderUsageTracker()
    await tracker.record_usage("groq", {"total_tokens": 123})

    usage = await tracker.get_daily_usage("groq", "user-1")
    assert usage["requests"] == 1
    assert usage["total_tokens"] == 123


@pytest.mark.asyncio
async def test_within_runtime_limits_blocks_openrouter_over_limit(monkeypatch) -> None:
    redis = DummyRedis()

    async def fake_safe_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(tracker_module, "safe_redis_call", fake_safe_call)
    monkeypatch.setattr(tracker_module, "get_redis", fake_get_redis)

    tracker = ProviderUsageTracker()
    redis.store[tracker._provider_requests_key("openrouter")] = tracker_module.OPENROUTER_DAILY_REQUEST_LIMIT

    assert await tracker.within_runtime_limits("openrouter") is False


@pytest.mark.asyncio
async def test_within_runtime_limits_blocks_cerebras_when_budget_nearly_exhausted(monkeypatch) -> None:
    redis = DummyRedis()

    async def fake_safe_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(tracker_module, "safe_redis_call", fake_safe_call)
    monkeypatch.setattr(tracker_module, "get_redis", fake_get_redis)

    settings = SimpleNamespace(cerebras_daily_token_budget=1000)
    monkeypatch.setattr(tracker_module, "get_settings", lambda: settings)

    tracker = ProviderUsageTracker()
    redis.store[tracker._provider_tokens_key("cerebras")] = 995

    assert await tracker.within_runtime_limits("cerebras") is False
