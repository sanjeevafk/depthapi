import asyncio
from types import SimpleNamespace

import pytest

import main as main_app
import routers.messages as messages_module
import routers.query as query_module
from conftest import FakeSupabase


@pytest.mark.asyncio
async def test_query_stream_fallback_on_start_timeout(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 1
    test_settings.stream_heartbeat_seconds = 0.05

    async def slow_stream(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        yield "late"

    async def fallback_generate(*_args, **_kwargs):
        return "fallback result"

    monkeypatch.setattr(query_module, "generate_stream_explanation", slow_stream)
    monkeypatch.setattr(query_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "test", "levels": ["eli5"], "mode": "socratic", "message_id": "b3f5d29c-7b1a-4d68-9a8b-ef0b3b3a1c5a"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: meta" in text
    assert "id:" in text
    assert "event: chunk" in text
    assert "fallback result" in text
    assert "event: done" in text


@pytest.mark.asyncio
async def test_messages_idempotency_replay(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.5
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.1

    user = SimpleNamespace(id="user-123", email="user@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fast_stream(*_args, **_kwargs):
        yield "hello"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-1", "user_id": user.id, "mode": "socratic", "settings": {}},
            "messages": [{"id": "assistant-1"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-1",
            "content": "hello",
            "client_generated_id": "5c6f8d49-8330-4b8b-93a1-42f5e59f00f9",
            "assistant_client_id": "e6b7b0f4-3a71-4fd4-bf62-9c9c9d38937a",
            "mode": "socratic",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
        assert "id:" in resp.text
        assert "hello" in resp.text
        assert len(fake_supabase.inserts) == 2

        replay = await app_client.post("/api/messages", json=payload)
        assert replay.status_code == 200
        assert "\"replay\":true" in replay.text.replace(" ", "")
        assert len(fake_supabase.inserts) == 2
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_abort_logs_confirmation(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.5
    test_settings.stream_max_seconds = 1
    test_settings.stream_heartbeat_seconds = 0.1

    user = SimpleNamespace(id="user-999", email="user@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fast_stream(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        yield "hello"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-2", "user_id": user.id, "mode": "socratic", "settings": {}},
            "messages": [{"id": "assistant-2"}],
            "users": {"is_pro": False},
        }
    )

    calls = []

    def fake_info(event, **kwargs):
        calls.append((event, kwargs))

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(messages_module.logger, "info", fake_info)

    async def always_disconnected(self):
        return True

    monkeypatch.setattr(messages_module.Request, "is_disconnected", always_disconnected, raising=False)

    try:
        payload = {
            "conversation_id": "conv-2",
            "content": "hello",
            "client_generated_id": "ac62a2d6-6d44-4a3e-89b5-6c5a9b9d99a0",
            "assistant_client_id": "7e3e31c6-22c6-4f9c-8a6a-2aa5e1141bc5",
            "mode": "socratic",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        abort_logs = [entry for entry in calls if entry[0] == "messages_abort_confirmed"]
        assert abort_logs
        assert abort_logs[0][1].get("tokens_after_abort") == 0
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)
