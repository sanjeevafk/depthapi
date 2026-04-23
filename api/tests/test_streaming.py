import time
from types import SimpleNamespace

import pytest

import main as main_app
import routers.messages as messages_module
import api.services.message_gate as message_gate
from conftest import FakeSupabase


def _allow_gatekeeper(monkeypatch):
    async def _allow(**_kwargs):
        return message_gate.GatekeeperResult(
            allowed=True,
            retry_after=0,
            idempotency_status=None,
            idempotency_response=None,
            degraded=True,
            redis_eval_ms=0.0,
        )

    monkeypatch.setattr(message_gate, "gatekeep_message_request", _allow)
    monkeypatch.setattr(messages_module, "gatekeep_message_request", _allow)


@pytest.mark.asyncio
async def test_partial_stream_not_cached(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-stream", email="user@example.com", user_metadata={})
    _allow_gatekeeper(monkeypatch)

    async def fake_verify_token():
        return {"user": user, "is_pro": False, "exp": time.time() + 600}

    async def failing_stream(*_args, **_kwargs):
        yield "partial"
        raise RuntimeError("stream failure")

    cache_called = {"value": False}

    async def fake_cache_set_value(*_args, **_kwargs):
        cache_called["value"] = True
        return True

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {
                "id": "conv-stream",
                "user_id": user.id,
                "mode": "learn",
                "settings": {},
            },
            "messages": [{"id": "assistant-stream"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "generate_stream_explanation", failing_stream)
    monkeypatch.setattr(messages_module, "cache_set_value", fake_cache_set_value)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-stream",
            "content": "hello",
            "client_generated_id": "4edc7e52-59d6-4ca9-9a96-8c89f5a0b1b2",
            "assistant_client_id": "bda1c865-9c4f-4f3a-8d6b-3e9b0e8a2b10",
            "mode": "learn",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "partial" in resp.text
        assert cache_called["value"] is False
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)
