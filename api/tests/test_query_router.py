import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import auth as auth_module
import routers.query as query_module
import api.services.rate_limit as rate_limit_module


@pytest.mark.asyncio
async def test_query_cache_hit_returns_cached(app_client, monkeypatch):
    async def fake_cache_get(_key):
        return {"text": "cached"}

    async def fake_cache_set(_key, _value):
        pytest.fail("cache_set should not be called")

    async def fake_generate_explanation(*_args, **_kwargs):
        pytest.fail("generate_explanation should not be called")

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)

    async def fake_auth():
        return None

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth

    resp = await app_client.post(
        "/api/query",
        json={"topic": "Cats", "levels": ["eli5"], "mode": "learn"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["explanations"]["eli5"] == "cached"


@pytest.mark.asyncio
async def test_query_waits_for_history_persistence(app_client, monkeypatch, fake_user):
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

    async def fake_auth():
        return {"user": fake_user}

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)
    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth

    start = asyncio.get_event_loop().time()
    resp = await app_client.post(
        "/api/query",
        json={"topic": "Persistence", "levels": ["eli5"], "mode": "learn"},
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert resp.status_code == 200
    assert calls == [True]
    assert elapsed >= 0.05


@pytest.mark.asyncio
async def test_save_to_history_logs_error_and_returns_when_supabase_unavailable(
    monkeypatch,
):
    """save_to_history must not raise — it logs and returns on any failure."""
    user = SimpleNamespace(id="user-hist", email="h@example.com", user_metadata={})
    errors_logged = []

    async def fake_ensure_user_exists(_user):
        pass  # succeed

    def fake_get_supabase_admin():
        return None  # simulate unavailable

    def fake_log_error(event, **kwargs):
        errors_logged.append(event)

    monkeypatch.setattr(query_module, "ensure_user_exists", fake_ensure_user_exists)
    monkeypatch.setattr(query_module, "get_supabase_admin", fake_get_supabase_admin)
    monkeypatch.setattr(query_module.logger, "error", fake_log_error)

    # Must not raise
    await query_module.save_to_history(user, "topic", ["eli5"], "learn")

    assert any("no_supabase_admin" in e for e in errors_logged)


@pytest.mark.asyncio
async def test_save_to_history_logs_error_on_fetch_failure(monkeypatch):
    """save_to_history must log fetch errors and not propagate them."""
    user = SimpleNamespace(id="user-hist2", email="h2@example.com", user_metadata={})
    errors_logged = []

    async def fake_ensure_user_exists(_user):
        pass

    class BrokenSupabase:
        def table(self, _name):
            raise RuntimeError("connection refused")

    def fake_get_supabase_admin():
        return BrokenSupabase()

    def fake_log_error(event, **kwargs):
        errors_logged.append(event)

    monkeypatch.setattr(query_module, "ensure_user_exists", fake_ensure_user_exists)
    monkeypatch.setattr(query_module, "get_supabase_admin", fake_get_supabase_admin)
    monkeypatch.setattr(query_module.logger, "error", fake_log_error)

    await query_module.save_to_history(user, "topic", ["eli5"], "learn")

    assert any("fetch_failed" in e for e in errors_logged)


@pytest.mark.asyncio
async def test_save_to_history_scopes_topic_lookup_by_mode(monkeypatch):
    user = SimpleNamespace(id="user-mode", email="mode@example.com", user_metadata={})

    async def fake_ensure_user_exists(_user):
        return None

    class FakeHistoryTable:
        def __init__(self):
            self.phase = "idle"
            self.eq_calls: list[tuple[str, str, str]] = []
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

        def execute(self):
            if self.phase == "select":
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{"id": "row-1"}])

    class FakeSupabase:
        def __init__(self):
            self.history = FakeHistoryTable()

        def table(self, name):
            assert name == "history"
            return self.history

    fake_supabase = FakeSupabase()

    monkeypatch.setattr(query_module, "ensure_user_exists", fake_ensure_user_exists)
    monkeypatch.setattr(query_module, "get_supabase_admin", lambda: fake_supabase)

    await query_module.save_to_history(user, "same-topic", ["eli5"], "socratic")

    assert ("select", "mode", "socratic") in fake_supabase.history.eq_calls
    assert fake_supabase.history.insert_payload is not None
    assert fake_supabase.history.insert_payload["mode"] == "socratic"


@pytest.mark.asyncio
async def test_query_invalid_topic(app_client):
    resp = await app_client.post(
        "/api/query",
        json={"topic": "bad<topic>", "levels": ["eli5"], "mode": "learn"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_technical_mode_rejects_non_pro_user(app_client, monkeypatch, fake_user):
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

    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)

    async def fake_auth():
        return {"user": fake_user, "is_pro": False, "exp": time.time() + 600}

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

    monkeypatch.setattr(query_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "Ocean", "levels": ["eli5"], "mode": "learn"}
    )
    assert resp.status_code == 200
    text = resp.text
    assert "data: [DONE]" in text
    assert "chunk" in text


@pytest.mark.asyncio
async def test_query_anonymous_rate_limit_exceeded(app_client, monkeypatch, test_settings):
    test_settings.anon_rph = 1
    test_settings.anon_daily_token_quota = 50000
    test_settings.circuit_breaker_tokens_per_minute = 300000

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_generate_explanation(*_args, **_kwargs):
        return "ok"

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "generate_explanation", fake_generate_explanation)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    first = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["eli5"], "mode": "learn"},
    )
    assert first.status_code == 200

    second = await app_client.post(
        "/api/query",
        json={"topic": "rate", "levels": ["eli5"], "mode": "learn"},
    )
    assert second.status_code == 429
    assert second.json()["detail"]["type"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_query_quota_exhaustion_blocks_inference(app_client, monkeypatch, test_settings):
    test_settings.free_daily_token_quota_learning = 1
    test_settings.free_hourly_token_quota_learning = 1
    test_settings.circuit_breaker_tokens_per_minute = 300000
    test_settings.free_burst_learning = 100
    test_settings.free_rpm_learning = 100

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when quota is exceeded")

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value):
        return True

    async def fake_auth():
        return {"user": SimpleNamespace(id="quota-user", email="quota@example.com", user_metadata={})}

    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth
    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    try:
        resp = await app_client.post(
            "/api/query",
            json={"topic": "quota", "levels": ["eli5"], "mode": "learn"},
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
    test_settings.free_daily_token_quota_learning = 50000  # High enough to not trigger quota exceeded
    test_settings.free_hourly_token_quota_learning = 50000
    test_settings.anon_rph = 100

    async def fail_if_called(*_args, **_kwargs):
        pytest.fail("inference must not run when circuit breaker is open")

    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(query_module, "generate_explanation", fail_if_called)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query",
        json={"topic": "breaker", "levels": ["eli5"], "mode": "learn"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["type"] == "circuit_breaker_open"
