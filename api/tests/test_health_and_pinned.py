import pytest

import main as main_app
import routers.pinned as pinned_module


@pytest.mark.asyncio
async def test_health_ok(app_client, monkeypatch):
    class DummyRedis:
        async def ping(self):
            return True

    async def fake_get_redis():
        return DummyRedis()

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    resp = app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "redis" in data


@pytest.mark.asyncio
async def test_health_redis_failure_in_prod(app_client, monkeypatch, test_settings):
    old_env = test_settings.environment
    test_settings.environment = "production"

    class DummyRedis:
        async def ping(self):
            raise RuntimeError("down")

    async def fake_get_redis():
        return DummyRedis()

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    resp = app_client.get("/api/health")
    assert resp.status_code == 503
    test_settings.environment = old_env


@pytest.mark.asyncio
async def test_pinned_topics():
    topics = await pinned_module.get_pinned()
    assert topics
    assert topics[0]["id"]
