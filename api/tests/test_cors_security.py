import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import api.main as api_main
import api.routers.query as query_module
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key


@pytest.mark.asyncio
async def test_missing_allowed_origins_uses_strict_defaults():
    assert api_main.DEFAULT_ALLOWED_ORIGINS
    origins = api_main.resolve_allowed_origins(None)

    assert origins == list(api_main.DEFAULT_ALLOWED_ORIGINS)
    assert "*" not in origins


@pytest.mark.asyncio
async def test_wildcard_origin_is_sanitized_with_warning(monkeypatch):
    origins = api_main.resolve_allowed_origins("*, https://depthapi.vercel.app")

    assert origins == ["https://depthapi.vercel.app"]


@pytest.mark.asyncio
async def test_credentialed_cors_blocks_arbitrary_origin_by_default():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_main.resolve_allowed_origins(None),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "authorization", "x-request-id"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        blocked = await client.get("/health", headers={"Origin": "https://evil.example"})
        allowed = await client.get(
            "/health",
            headers={"Origin": api_main.DEFAULT_ALLOWED_ORIGINS[0]},
        )

    assert blocked.headers.get("access-control-allow-origin") is None
    assert allowed.headers.get("access-control-allow-origin") == api_main.DEFAULT_ALLOWED_ORIGINS[0]
    assert allowed.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_health_and_query_still_work_with_cors_patch(app_client, monkeypatch):
    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_generate_explanation(*_args, **_kwargs):
        return "ok"

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    monkeypatch.setattr(query_module, "get_provider_config_state", lambda: {"chat_enabled": True})
    async def fake_key():
        return ApiKeyRecord(
            id="test-key-uuid-1234",
            prefix="sk-depth-test",
            project_name="Test Project",
            owner_email="test@example.com",
            plan="pro",
            monthly_token_budget=10_000_000,
            requests_per_minute=100,
        )
    app_client.app.dependency_overrides[verify_api_key] = fake_key
    health_resp = await app_client.get("/api/health")
    assert health_resp.status_code == 200

    query_resp = await app_client.post(
        "/api/query",
        json={"topic": "CORS hardening", "prompt_spec": {"depth": "simple"}, "mode": "learn"},
    )
    assert query_resp.status_code == 200
    payload = query_resp.json()
    assert payload["explanations"]["simple"] == "ok"
