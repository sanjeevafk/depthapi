from __future__ import annotations

import pytest

from services.circuit_breaker import CircuitBreaker


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl_map: dict[str, int] = {}
        self.token_totals: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args):
        usage_key = str(args[0])
        open_key = str(args[1])
        estimated_tokens = int(args[2])
        threshold = int(args[3])
        open_seconds = int(args[4])

        if self.store.get(open_key):
            return [0, int(self.ttl_map.get(open_key, open_seconds))]

        total = int(self.token_totals.get(usage_key, 0)) + estimated_tokens
        self.token_totals[usage_key] = total
        if total > threshold:
            self.store[open_key] = "1"
            self.ttl_map[open_key] = open_seconds
            return [0, open_seconds]
        return [1, 0]

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.ttl_map[key] = ttl

    async def get(self, key: str):
        return self.store.get(key)

    async def ttl(self, key: str) -> int:
        return int(self.ttl_map.get(key, -1))

    async def delete(self, key: str) -> int:
        existed = 1 if key in self.store else 0
        self.store.pop(key, None)
        self.ttl_map.pop(key, None)
        return existed


@pytest.mark.asyncio
async def test_circuit_breaker_opens_when_threshold_exceeded() -> None:
    breaker = CircuitBreaker()
    redis = FakeRedis()

    async def _get_redis():
        return redis

    first = await breaker.should_allow_request(
        estimated_tokens=1,
        fail_open=False,
        threshold=2,
        open_seconds=9,
        action="reject",
        get_redis_fn=_get_redis,
    )
    second = await breaker.should_allow_request(
        estimated_tokens=2,
        fail_open=False,
        threshold=2,
        open_seconds=9,
        action="reject",
        get_redis_fn=_get_redis,
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.retry_after == 9


@pytest.mark.asyncio
async def test_circuit_breaker_state_reset_cycle() -> None:
    breaker = CircuitBreaker()
    redis = FakeRedis()

    async def _get_redis():
        return redis

    await breaker.mark_failure(open_seconds=11, get_redis_fn=_get_redis)
    open_state = await breaker.get_state(get_redis_fn=_get_redis)
    await breaker.reset(get_redis_fn=_get_redis)
    closed_state = await breaker.get_state(get_redis_fn=_get_redis)

    assert open_state.is_open is True
    assert open_state.retry_after == 11
    assert closed_state.is_open is False
    assert closed_state.retry_after == 0
