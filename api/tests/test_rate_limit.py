import pytest

import services.rate_limit as rate_limit_module


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args):
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
    test_settings.rate_limit_per_user = 1
    test_settings.rate_limit_burst = 1
    test_settings.daily_token_quota_per_user = 0
    test_settings.circuit_breaker_tokens_per_minute = 0

    async def broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", broken_get_redis)

    await rate_limit_module.enforce_request_controls(
        user_id="user-1",
        client_ip="127.0.0.1",
        estimated_tokens=100,
    )


@pytest.mark.asyncio
async def test_anonymous_requests_fail_closed_when_store_unavailable(monkeypatch, test_settings):
    test_settings.anonymous_rate_limit_per_ip = 1
    test_settings.anonymous_rate_limit_burst = 1
    test_settings.circuit_breaker_tokens_per_minute = 0

    async def broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", broken_get_redis)

    with pytest.raises(Exception) as exc_info:
        await rate_limit_module.enforce_request_controls(
            user_id=None,
            client_ip="127.0.0.1",
            estimated_tokens=100,
        )

    assert getattr(exc_info.value, "status_code", None) == 503


@pytest.mark.asyncio
async def test_quota_does_not_consume_tokens_on_reject(monkeypatch, test_settings):
    test_settings.daily_token_quota_per_user = 10
    test_settings.quota_window_seconds = 100

    fake_redis = FakeRedis()

    async def get_fake_redis():
        return fake_redis

    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", get_fake_redis)

    result = await rate_limit_module.check_daily_quota(user_id="user-1", estimated_tokens=15)
    assert result.allowed is False
    assert result.consumed == 0
    assert "knowbear:quota:user-1" not in fake_redis.data

    allowed = await rate_limit_module.check_daily_quota(user_id="user-1", estimated_tokens=5)
    assert allowed.allowed is True
    assert allowed.consumed == 5
