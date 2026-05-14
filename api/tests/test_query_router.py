import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.auth as api_auth_module
import routers.query as query_module
import api.services.security.rate_limit as rate_limit_module
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key


@pytest.fixture
def override_default_api_key(app_client):
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
    yield
    app_client.app.dependency_overrides.pop(verify_api_key, None)


@pytest.mark.asyncio
async def test_query_cache_hit_returns_cached(app_client, monkeypatch, override_default_api_key):
    async def fake_cache_get(_key):
        return {"text": "cached"}

    async def fake_cache_set(_key, _value):
        pytest.fail("cache_set should not be called")

    async def fake_generate_explanation(*_args, **_kwargs):
        pytest.fail("generate_explanation should not be called")

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "Cats", "levels": ["simple"], "mode": "learn"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["explanations"]["simple"] == "cached"


@pytest.mark.asyncio
async def test_query_waits_for_history_persistence(app_client, monkeypatch, override_default_api_key):
    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_generate_explanation(*_args, **_kwargs):
        return "ok"

    calls = []

    async def fake_save_to_history(*_args, **_kwargs):
        await asyncio.sleep(0.06)
        calls.append(True)

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)

    start = asyncio.get_event_loop().time()
    resp = await app_client.post(
        "/api/query",
        json={"topic": "Persistence", "levels": ["simple"], "mode": "learn"},
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert resp.status_code == 200
    assert calls == [True]
    assert elapsed >= 0.05


@pytest.mark.asyncio
async def test_save_to_history_logs_error_and_returns_when_supabase_unavailable(monkeypatch):
    errors_logged = []

    def fake_get_supabase_admin():
        return None

    def fake_log_error(event, **_kwargs):
        errors_logged.append(event)

    monkeypatch.setattr(api_auth_module, "get_supabase_admin", fake_get_supabase_admin)
    monkeypatch.setattr(query_module.logger, "error", fake_log_error)

    await query_module.save_to_history("key-1", "topic", ["simple"], "learn")

    assert any("save_to_history_no_supabase_admin" in e for e in errors_logged)


@pytest.mark.asyncio
async def test_save_to_history_logs_error_on_fetch_failure(monkeypatch):
    errors_logged = []

    class BrokenSupabase:
        def table(self, _name):
            raise RuntimeError("connection refused")

    def fake_get_supabase_admin():
        return BrokenSupabase()

    def fake_log_error(event, **_kwargs):
        errors_logged.append(event)

    monkeypatch.setattr(api_auth_module, "get_supabase_admin", fake_get_supabase_admin)
    monkeypatch.setattr(query_module.logger, "error", fake_log_error)

    await query_module.save_to_history("key-2", "topic", ["simple"], "learn")

    assert any("save_to_history_write_failed" in e for e in errors_logged)


@pytest.mark.asyncio
async def test_save_to_history_scopes_topic_lookup_by_mode(monkeypatch):
    class FakeHistoryQuery:
        def __init__(self):
            self.phase = "idle"
            self.eq_calls = []
            self.insert_payload = None

        def select(self, _fields):
            self.phase = "select"
            return self

        def eq(self, column, value):
            self.eq_calls.append((self.phase, str(column), str(value)))
            return self

        def insert(self, payload):
            self.phase = "insert"
            self.insert_payload = payload
            return self

        def update(self, _payload):
            self.phase = "update"
            return self

        async def execute(self):
            if self.phase == "select":
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{"id": "row-1"}])

    class FakeSupabase:
        def __init__(self):
            self.history = FakeHistoryQuery()

        def table(self, name):
            assert name == "history"
            return self.history

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(api_auth_module, "get_supabase_admin", lambda: fake_supabase)

    await query_module.save_to_history("key-mode", "same-topic", ["simple"], "socratic")

    assert ("select", "mode", "socratic") in fake_supabase.history.eq_calls
    assert fake_supabase.history.insert_payload is not None
    assert fake_supabase.history.insert_payload["mode"] == "socratic"


@pytest.mark.asyncio
async def test_query_invalid_topic(app_client, override_default_api_key):
    resp = await app_client.post(
        "/api/query",
        json={"topic": "bad<topic>", "levels": ["simple"], "mode": "learn"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_technical_mode_rejects_non_pro_user(app_client, monkeypatch, override_default_api_key):
    calls = []

    async def fake_generate_explanation(*_args, **_kwargs):
        calls.append(True)
        return "ok"

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    async def fake_non_pro_key():
        return ApiKeyRecord(
            id="starter-key",
            prefix="sk-depth-starter",
            project_name="Starter",
            owner_email="starter@example.com",
            plan="starter",
            monthly_token_budget=2_000_000,
            requests_per_minute=60,
        )

    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    app_client.app.dependency_overrides[verify_api_key] = fake_non_pro_key

    resp = await app_client.post(
        "/api/query",
        json={
            "topic": "Space",
            "levels": ["simple"],
            "mode": "technical",
            "premium": True,
        },
    )

    assert resp.status_code == 403
    assert "requires a Pro or Enterprise plan" in resp.json()["detail"]
    assert calls == []


@pytest.mark.asyncio
async def test_query_technical_mode_requires_authentication(app_client, override_default_api_key):
    async def fake_missing_key():
        raise HTTPException(
            status_code=401,
            detail={
                "type": "missing_api_key",
                "message": "No API key provided.",
            },
        )

    app_client.app.dependency_overrides[verify_api_key] = fake_missing_key

    resp = await app_client.post(
        "/api/query",
        json={
            "topic": "Space",
            "levels": ["simple"],
            "mode": "technical",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["type"] == "missing_api_key"


@pytest.mark.asyncio
@pytest.mark.skip(reason="ASGI test client hangs on streaming teardown in this harness; covered by modular streaming tests.")
async def test_query_stream_emits_done(app_client, monkeypatch, override_default_api_key):
    async def fake_stream(*_args, **_kwargs):
        yield "Hello "
        yield "World"

    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(query_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)

    async with app_client.stream(
        "POST",
        "/api/query/stream",
        json={"topic": "Ocean", "levels": ["simple"], "mode": "learn"},
    ) as resp:
        assert resp.status_code == 200
        text = await resp.aread()
        text = text.decode("utf-8")
    assert "data: [DONE]" in text
    assert "chunk" in text


@pytest.mark.asyncio
async def test_query_rate_limit_exceeded(app_client, monkeypatch, test_settings, override_default_api_key):
    test_settings.circuit_breaker_tokens_per_minute = 300000

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_generate_explanation(*_args, **_kwargs):
        return "ok"

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    async def fake_limited_key():
        return ApiKeyRecord(
            id="rate-key",
            prefix="sk-depth-rate",
            project_name="Rate Limited",
            owner_email="rate@example.com",
            plan="starter",
            monthly_token_budget=2_000_000,
            requests_per_minute=1,
        )

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    app_client.app.dependency_overrides[verify_api_key] = fake_limited_key

    first = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["simple"], "mode": "learn"},
    )
    assert first.status_code == 200

    second = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["simple"], "mode": "learn"},
    )
    assert second.status_code == 429
    assert second.json()["detail"]["type"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_query_quota_exhaustion_blocks_inference(app_client, monkeypatch, test_settings, override_default_api_key):
    test_settings.circuit_breaker_tokens_per_minute = 300000

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when quota is exceeded")

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_quota_key():
        return ApiKeyRecord(
            id="quota-key",
            prefix="sk-depth-quota",
            project_name="Quota Limited",
            owner_email="quota@example.com",
            plan="starter",
            monthly_token_budget=30,
            requests_per_minute=100,
        )

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)
    app_client.app.dependency_overrides[verify_api_key] = fake_quota_key

    resp = await app_client.post(
        "/api/query",
        json={"topic": "quota", "levels": ["simple"], "mode": "learn"},
    )
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["type"] == "quota_exceeded"
    assert detail["retry_allowed"] is False


@pytest.mark.asyncio
async def test_query_circuit_breaker_trigger_rejects(app_client, monkeypatch, test_settings, override_default_api_key):
    test_settings.circuit_breaker_tokens_per_minute = 1

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when circuit breaker is open")

    async def fake_cache_get(_key):
        return None

    async def fake_save_to_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "breaker", "levels": ["simple"], "mode": "learn"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["type"] == "circuit_breaker_open"
