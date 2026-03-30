import pytest

import services.rate_limit as rate_limit_module


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.ttl: dict[str, int] = {}
        self.hashes: dict[str, dict[str, int]] = {}

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
                bucket_int = int(bucket)
                if bucket_int < stale_before:
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
            ttl = int(self.ttl.get(key, window))
            return [0, current, ttl]

        new_total = current + requested
        self.data[key] = new_total
        self.ttl[key] = window
        return [1, new_total, self.ttl[key]]


@pytest.mark.asyncio
async def test_authenticated_requests_fail_open_when_store_unavailable(monkeypatch, test_settings):
    test_settings.free_rpm_learning = 1
    test_settings.free_burst_learning = 1
    test_settings.free_daily_token_quota_learning = 0
    test_settings.free_hourly_token_quota_learning = 0
    test_settings.circuit_breaker_tokens_per_minute = 0

    async def broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", broken_get_redis)

    await rate_limit_module.enforce_request_controls(
        user_id="user-1",
        client_ip="127.0.0.1",
        reserved_tokens=100,
        mode="learn",
    )


@pytest.mark.asyncio
async def test_anonymous_requests_fail_closed_when_store_unavailable(monkeypatch, test_settings):
    test_settings.anon_rph = 1
    test_settings.circuit_breaker_tokens_per_minute = 0

    async def broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", broken_get_redis)

    with pytest.raises(Exception) as exc_info:
        await rate_limit_module.enforce_request_controls(
            user_id=None,
            client_ip="127.0.0.1",
            reserved_tokens=100,
            mode="learn",
        )

    assert getattr(exc_info.value, "status_code", None) == 503


@pytest.mark.asyncio
async def test_quota_does_not_consume_tokens_on_reject(monkeypatch, test_settings):
    test_settings.free_daily_token_quota_learning = 10
    test_settings.quota_window_seconds = 100

    fake_redis = FakeRedis()

    async def get_fake_redis():
        return fake_redis

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", get_fake_redis)

    result = await rate_limit_module.check_daily_quota(
        key="knowbear:quota:user-1:learn",
        limit=10,
        requested=15,
        window_seconds=100,
    )
    assert result.allowed is False
    assert result.consumed == 0
    assert "knowbear:quota:user-1:learn" not in fake_redis.hashes

    allowed = await rate_limit_module.check_daily_quota(
        key="knowbear:quota:user-1:learn",
        limit=10,
        requested=5,
        window_seconds=100,
    )
    assert allowed.allowed is True
    assert allowed.consumed == 5


@pytest.mark.asyncio
async def test_quota_check_failed_log_uses_user_id_hash_only(monkeypatch, test_settings):
    test_settings.free_rpm_learning = 1
    test_settings.free_burst_learning = 1
    test_settings.free_daily_token_quota_learning = 100
    test_settings.free_hourly_token_quota_learning = 100
    test_settings.circuit_breaker_tokens_per_minute = 0

    async def fake_check_daily_quota(*, key: str, limit: int, requested: int, window_seconds: int):
        raise RuntimeError("quota backend error")

    async def always_allow_rate_limit(**_kwargs):
        return rate_limit_module.RateLimitResult(
            allowed=True,
            limit=1,
            remaining=1,
            retry_after=1,
            reason="ok",
        )

    async def always_allow_breaker(*, estimated_tokens: int, fail_open: bool):
        return rate_limit_module.CircuitBreakerResult(allowed=True, retry_after=0)

    warnings: list[tuple[str, dict]] = []

    def fake_warning(event, **kwargs):
        warnings.append((event, kwargs))

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "check_daily_quota", fake_check_daily_quota)
    monkeypatch.setattr(rate_limit_module, "check_hourly_quota", fake_check_daily_quota)
    monkeypatch.setattr(rate_limit_module, "check_rate_limit", always_allow_rate_limit)
    monkeypatch.setattr(rate_limit_module, "check_circuit_breaker", always_allow_breaker)
    monkeypatch.setattr(rate_limit_module.logger, "warning", fake_warning)

    await rate_limit_module.enforce_request_controls(
        user_id="user-123",
        client_ip="127.0.0.1",
        reserved_tokens=100,
        mode="learn",
    )

    quota_warning_payload = next(payload for event, payload in warnings if event == "quota_check_failed")
    assert "user_id" not in quota_warning_payload
    assert "user_id_hash" in quota_warning_payload
    assert quota_warning_payload["user_id_hash"] is not None
