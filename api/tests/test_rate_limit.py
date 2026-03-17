import pytest

import main as main_app
from services.rate_limit import RateLimitResult


@pytest.mark.asyncio
async def test_conditional_rate_limit_no_redis(monkeypatch):
    main_app.redis_available = False

    await main_app.conditional_rate_limit(object(), object())


@pytest.mark.asyncio
async def test_conditional_rate_limit_calls_limiter(monkeypatch, test_settings):
    main_app.redis_available = True
    test_settings.environment = "production"

    calls = {"count": 0}

    async def fake_check_rate_limit(*_args, **_kwargs):
        calls["count"] += 1
        return RateLimitResult(allowed=True, limit=20, remaining=19, retry_after=60)

    monkeypatch.setattr(main_app, "check_rate_limit", fake_check_rate_limit)
    monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)

    await main_app.conditional_rate_limit(object(), object())
    assert calls["count"] == 1
