import pytest

import main as main_app


@pytest.mark.asyncio
async def test_conditional_rate_limit_no_redis(monkeypatch):
    main_app.redis_available = False

    async def fake_rate_limiter(*_args, **_kwargs):
        raise AssertionError("RateLimiter should not be called")

    monkeypatch.setattr(main_app, "RateLimiter", fake_rate_limiter)
    await main_app.conditional_rate_limit(object(), object())


@pytest.mark.asyncio
async def test_conditional_rate_limit_calls_limiter(monkeypatch, test_settings):
    main_app.redis_available = True
    test_settings.environment = "production"

    calls = {"count": 0}

    def fake_rate_limiter(*_args, **_kwargs):
        async def _inner(_req, _resp):
            calls["count"] += 1
        return _inner

    monkeypatch.setattr(main_app, "RateLimiter", fake_rate_limiter)
    monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)

    await main_app.conditional_rate_limit(object(), object())
    assert calls["count"] == 1
