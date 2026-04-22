from __future__ import annotations

import pytest

from services.quota_manager import QuotaManager


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.ttl_map: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args):
        if "HGETALL" in script:
            key = str(args[0])
            now_min = int(args[1])
            requested = int(args[2])
            limit = int(args[3])
            window = int(args[4])
            bucket_data = self.hashes.get(key, {})
            total = 0
            stale_before = now_min - window + 1
            for bucket, value in list(bucket_data.items()):
                if int(bucket) < stale_before:
                    bucket_data.pop(bucket, None)
                else:
                    total += int(value)
            if total + requested > limit:
                return [0, total, window * 60]
            bucket_data[str(now_min)] = int(bucket_data.get(str(now_min), 0)) + requested
            self.hashes[key] = bucket_data
            return [1, total + requested, window * 60]

        key = str(args[0])
        requested = int(args[1])
        limit = int(args[2])
        window = int(args[3])
        current = int(self.data.get(key, 0))
        consumed = current + requested
        if consumed > limit:
            ttl = int(self.ttl_map.get(key, window))
            return [0, current, ttl]
        self.data[key] = consumed
        self.ttl_map[key] = window
        return [1, consumed, window]


@pytest.mark.asyncio
async def test_quota_manager_daily_quota_reject_preserves_total() -> None:
    manager = QuotaManager()
    redis = FakeRedis()

    async def _get_redis():
        return redis

    rejected = await manager.check_daily_quota(
        key="knowbear:quota:user-1:learn",
        limit=10,
        requested=15,
        window_seconds=100,
        get_redis_fn=_get_redis,
    )
    allowed = await manager.check_daily_quota(
        key="knowbear:quota:user-1:learn",
        limit=10,
        requested=5,
        window_seconds=100,
        get_redis_fn=_get_redis,
    )

    assert rejected.allowed is False
    assert rejected.consumed == 0
    assert allowed.allowed is True
    assert allowed.consumed == 5


@pytest.mark.asyncio
async def test_quota_manager_hourly_quota_accumulates_buckets() -> None:
    manager = QuotaManager()
    redis = FakeRedis()

    async def _get_redis():
        return redis

    first = await manager.check_hourly_quota(
        key="knowbear:quota_hour:user-1:learn",
        limit=20,
        requested=6,
        now_minute=100,
        get_redis_fn=_get_redis,
    )
    second = await manager.check_hourly_quota(
        key="knowbear:quota_hour:user-1:learn",
        limit=20,
        requested=7,
        now_minute=101,
        get_redis_fn=_get_redis,
    )
    third = await manager.check_hourly_quota(
        key="knowbear:quota_hour:user-1:learn",
        limit=20,
        requested=9,
        now_minute=101,
        get_redis_fn=_get_redis,
    )

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.consumed == 13


@pytest.mark.asyncio
async def test_quota_manager_reserve_tokens_uses_identifier_and_mode() -> None:
    manager = QuotaManager()
    reservation = await manager.reserve_tokens(
        identifier="user:abc",
        mode="technical",
        reserved_tokens=42,
        is_anonymous=False,
    )

    assert reservation.identifier == "user:abc"
    assert reservation.mode == "technical"
    assert reservation.reserved_tokens == 42
    assert reservation.daily_key.endswith("user:abc:technical")


@pytest.mark.asyncio
async def test_quota_manager_reserve_tokens_uses_explicit_hourly_bucket() -> None:
    manager = QuotaManager()
    reservation = await manager.reserve_tokens(
        identifier="user:abc",
        mode="learn",
        reserved_tokens=7,
        is_anonymous=False,
        hourly_bucket=123456,
    )

    assert reservation.hourly_bucket == 123456
