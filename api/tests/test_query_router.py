import asyncio
import json
from types import SimpleNamespace

import pytest

import auth as auth_module
import routers.query as query_module
import services.rate_limit as rate_limit_module


@pytest.mark.asyncio
async def test_query_cache_hit_returns_cached(app_client, monkeypatch):
    async def fake_cache_get(_key):
        return {"text": "cached"}

    async def fake_cache_set(_key, _value):
        pytest.fail("cache_set should not be called")

    async def fake_ensemble_generate(*_args, **_kwargs):
        pytest.fail("ensemble_generate should not be called")

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "ensemble_generate", fake_ensemble_generate)

    async def fake_auth():
        return None

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth

    resp = await app_client.post(
        "/api/query",
        json={"topic": "Cats", "levels": ["eli5"], "mode": "learning"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["explanations"]["eli5"] == "cached"


@pytest.mark.asyncio
async def test_query_invalid_topic(app_client):
    resp = await app_client.post(
        "/api/query",
        json={"topic": "bad<topic>", "levels": ["eli5"], "mode": "learning"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_technical_mode_rejects_non_pro_user(app_client, monkeypatch, fake_user):
    calls = []

    async def fake_ensemble_generate(_topic, _level, use_premium, mode, **_kwargs):
        calls.append((use_premium, mode))
        return "ok"

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_check_is_pro(_user_id):
        return False

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_module, "ensemble_generate", fake_ensemble_generate)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)

    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth

    resp = await app_client.post(
        "/api/query",
        json={
            "topic": "Space",
            "levels": ["eli5"],
            "mode": "technical",
            "premium": True
        }
    )

    assert resp.status_code == 403
    assert "Pro feature" in resp.json()["detail"]
    assert calls == []


@pytest.mark.asyncio
async def test_query_technical_mode_requires_authentication(app_client):
    resp = await app_client.post(
        "/api/query",
        json={
            "topic": "Space",
            "levels": ["eli5"],
            "mode": "technical",
        }
    )

    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_query_stream_emits_done(app_client, monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield "Hello "
        yield "World"

    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(query_module, "ensemble_stream_generate", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "Ocean", "levels": ["eli5"], "mode": "learning"}
    )
    assert resp.status_code == 200
    text = resp.text
    assert "data: [DONE]" in text
    assert "chunk" in text


@pytest.mark.asyncio
async def test_query_anonymous_rate_limit_exceeded(app_client, monkeypatch, test_settings):
    test_settings.anonymous_rate_limit_burst = 1
    test_settings.anonymous_rate_limit_per_ip = 1
    test_settings.daily_token_quota_per_user = 50000
    test_settings.circuit_breaker_tokens_per_minute = 300000

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_ensemble_generate(*_args, **_kwargs):
        return "ok"

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "ensemble_generate", fake_ensemble_generate)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    first = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["eli5"], "mode": "learning"},
    )
    assert first.status_code == 200

    second = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["eli5"], "mode": "learning"},
    )
    assert second.status_code == 429
    assert second.json()["detail"]["type"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_query_quota_exhaustion_blocks_inference(app_client, monkeypatch, test_settings):
    test_settings.daily_token_quota_per_user = 1
    test_settings.circuit_breaker_tokens_per_minute = 300000
    test_settings.rate_limit_burst = 100
    test_settings.rate_limit_per_user = 100

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when quota is exceeded")

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_auth():
        return {"user": SimpleNamespace(id="quota-user", email="quota@example.com", user_metadata={})}

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth
    monkeypatch.setattr(query_module, "ensemble_generate", fail_if_called)
    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    try:
        resp = await app_client.post(
            "/api/query",
            json={"topic": "quota", "levels": ["eli5"], "mode": "learning"},
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["type"] == "quota_exceeded"
        assert detail["retry_allowed"] is False
    finally:
        app_client.app.dependency_overrides.pop(auth_module.verify_token_optional, None)


@pytest.mark.asyncio
async def test_query_circuit_breaker_trigger_rejects(app_client, monkeypatch, test_settings):
    test_settings.circuit_breaker_tokens_per_minute = 1
    test_settings.daily_token_quota_per_user = 50000  # High enough to not trigger quota exceeded
    test_settings.anonymous_rate_limit_burst = 100
    test_settings.anonymous_rate_limit_per_ip = 100

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when circuit breaker is open")

    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(query_module, "ensemble_generate", fail_if_called)
    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "breaker", "levels": ["eli5"], "mode": "learning"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["type"] == "circuit_breaker_open"
