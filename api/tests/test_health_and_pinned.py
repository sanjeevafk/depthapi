import pytest

import main as main_app
import api.main as api_main_app
import routers.pinned as pinned_module
import routers.query as query_module
from services.llm_errors import LLMInvalidAPIKey


@pytest.mark.asyncio
async def test_health_ok(app_client, monkeypatch):
    class DummyRedis:
        async def ping(self):
            return True

    async def fake_get_redis():
        return DummyRedis()

    class DummyResponse:
        status_code = 200

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    monkeypatch.setattr(api_main_app.httpx, "AsyncClient", lambda **_kwargs: DummyClient())
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert set(data.keys()) >= {"status", "litellm", "rate_limit", "db"}
    assert data["litellm"]["status"] == "ok"
    assert isinstance(data["litellm"]["latency_ms"], int)
    assert data["rate_limit"]["status"] == "ok"
    assert data["db"]["status"] == "ok"


@pytest.mark.asyncio
async def test_health_redis_failure_in_prod(app_client, monkeypatch, test_settings):
    old_env = test_settings.environment
    test_settings.environment = "production"

    class DummyRedis:
        async def ping(self):
            raise RuntimeError("down")

    async def fake_get_redis():
        return DummyRedis()

    class DummyResponse:
        status_code = 200

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    monkeypatch.setattr(api_main_app.httpx, "AsyncClient", lambda **_kwargs: DummyClient())
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "down"
    assert data["rate_limit"]["status"] == "down"
    test_settings.environment = old_env


@pytest.mark.asyncio
async def test_health_missing_litellm_config_degrades(app_client, test_settings):
    old_base = test_settings.litellm_base_url
    old_key = test_settings.litellm_virtual_key
    old_master = test_settings.litellm_master_key

    test_settings.litellm_base_url = ""
    test_settings.litellm_virtual_key = ""
    test_settings.litellm_master_key = ""

    try:
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"degraded", "down"}
        assert data["litellm"]["status"] == "degraded"
        assert data.get("chat_enabled") is False
    finally:
        test_settings.litellm_base_url = old_base
        test_settings.litellm_virtual_key = old_key
        test_settings.litellm_master_key = old_master


@pytest.mark.asyncio
async def test_query_degraded_when_litellm_missing(app_client, test_settings):
    old_base = test_settings.litellm_base_url
    old_key = test_settings.litellm_virtual_key
    old_master = test_settings.litellm_master_key

    test_settings.litellm_base_url = ""
    test_settings.litellm_virtual_key = ""
    test_settings.litellm_master_key = ""

    try:
        resp = await app_client.post(
            "/api/query",
            json={"topic": "lite llm", "levels": ["eli15"], "mode": "learning"},
        )
        assert resp.status_code == 503
        payload = resp.json()
        assert payload["error"]["type"] == "service_degraded"
    finally:
        test_settings.litellm_base_url = old_base
        test_settings.litellm_virtual_key = old_key
        test_settings.litellm_master_key = old_master


@pytest.mark.asyncio
async def test_invalid_litellm_key_returns_structured_error(app_client, monkeypatch):
    async def invalid_key(*_args, **_kwargs):
        raise LLMInvalidAPIKey("LiteLLM rejected credentials.")

    monkeypatch.setattr(query_module, "generate_explanation", invalid_key)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "lite llm invalid key", "levels": ["eli15"], "mode": "learning", "bypass_cache": True},
    )
    assert resp.status_code == 502
    payload = resp.json()
    assert payload["error"]["type"] == "invalid_api_key"
    assert payload["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_pinned_topics():
    topics = await pinned_module.get_pinned()
    assert topics
    assert topics[0]["id"]
