import pytest

import main as main_app
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

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"ok", "degraded"}
    assert set(data.keys()) >= {"status", "provider", "rate_limit", "db"}
    assert data["provider"]["status"] == "ok"
    assert isinstance(data["provider"]["reachable"], bool)
    assert isinstance(data["provider"]["key_valid"], bool)
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

    monkeypatch.setattr(main_app, "get_redis", fake_get_redis)
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "down"
    assert data["rate_limit"]["status"] == "down"
    test_settings.environment = old_env


@pytest.mark.asyncio
async def test_health_missing_provider_config_degrades(app_client, test_settings):
    old_keys = (
        test_settings.groq_api_key,
        test_settings.cerebras_api_key,
        test_settings.gemini_api_key,
        test_settings.openrouter_api_key,
    )

    test_settings.groq_api_key = ""
    test_settings.cerebras_api_key = ""
    test_settings.gemini_api_key = ""
    test_settings.openrouter_api_key = ""

    try:
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"degraded", "down"}
        assert data["provider"]["status"] == "degraded"
        assert data.get("chat_enabled") is False
    finally:
        (
            test_settings.groq_api_key,
            test_settings.cerebras_api_key,
            test_settings.gemini_api_key,
            test_settings.openrouter_api_key,
        ) = old_keys


@pytest.mark.asyncio
async def test_query_degraded_when_provider_keys_missing(app_client, test_settings):
    old_keys = (
        test_settings.groq_api_key,
        test_settings.cerebras_api_key,
        test_settings.gemini_api_key,
        test_settings.openrouter_api_key,
    )

    test_settings.groq_api_key = ""
    test_settings.cerebras_api_key = ""
    test_settings.gemini_api_key = ""
    test_settings.openrouter_api_key = ""

    try:
        resp = await app_client.post(
            "/api/query",
            json={"topic": "provider routing", "levels": ["eli15"], "mode": "learning"},
        )
        assert resp.status_code == 503
        payload = resp.json()
        assert payload["error"]["type"] == "service_degraded"
    finally:
        (
            test_settings.groq_api_key,
            test_settings.cerebras_api_key,
            test_settings.gemini_api_key,
            test_settings.openrouter_api_key,
        ) = old_keys


@pytest.mark.asyncio
async def test_invalid_provider_key_returns_structured_error(app_client, monkeypatch):
    async def invalid_key(*_args, **_kwargs):
        raise LLMInvalidAPIKey("Provider rejected credentials.")

    monkeypatch.setattr(query_module, "generate_explanation", invalid_key)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "provider invalid key", "levels": ["eli15"], "mode": "learning", "bypass_cache": True},
    )
    assert resp.status_code == 502
    payload = resp.json()
    assert payload["error"]["type"] == "invalid_api_key"
    assert payload["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_unknown_route_returns_404_json(app_client):
    resp = await app_client.get("/definitely-not-a-route")
    assert resp.status_code == 404
    payload = resp.json()
    assert payload["error"] == "Not Found"
    assert "does not exist" in payload["detail"]


@pytest.mark.asyncio
async def test_known_route_not_caught_by_catch_all(app_client):
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert "status" in payload


@pytest.mark.asyncio
async def test_pinned_topics():
    topics = await pinned_module.get_pinned()
    assert topics
    assert topics[0]["id"]
