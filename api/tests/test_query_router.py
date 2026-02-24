import asyncio
import json

import pytest

import auth as auth_module
import routers.query as query_module


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

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = lambda: None

    resp = app_client.post(
        "/api/query",
        json={"topic": "Cats", "levels": ["eli5"], "mode": "fast"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["explanations"]["eli5"] == "cached"


@pytest.mark.asyncio
async def test_query_invalid_topic(app_client):
    resp = app_client.post(
        "/api/query",
        json={"topic": "bad<topic>", "levels": ["eli5"], "mode": "fast"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_premium_downgrade_and_level_filter(app_client, monkeypatch, fake_user):
    calls = []

    async def fake_ensemble_generate(_topic, _level, use_premium, mode):
        calls.append((use_premium, mode))
        return "ok"

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_check_is_pro(_user_id):
        return False

    monkeypatch.setattr(query_module, "ensemble_generate", fake_ensemble_generate)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "check_is_pro", fake_check_is_pro)

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/query",
        json={
            "topic": "Space",
            "levels": ["eli5", "classic60"],
            "mode": "ensemble",
            "premium": True
        }
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "eli5" in data["explanations"]
    assert "classic60" not in data["explanations"]
    assert calls and calls[0] == (False, "fast")


@pytest.mark.asyncio
async def test_query_stream_emits_done(app_client, monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield "Hello "
        yield "World"

    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(query_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)

    resp = app_client.post(
        "/api/query/stream",
        json={"topic": "Ocean", "levels": ["eli5"], "mode": "fast"}
    )
    assert resp.status_code == 200
    text = resp.text
    assert "data: [DONE]" in text
    assert "chunk" in text
