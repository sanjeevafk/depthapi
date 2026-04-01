import asyncio
import time
from types import SimpleNamespace

import pytest

import main as main_app
import routers.messages as messages_module
import services.message_gate as message_gate
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
async def test_snapshot_warming_awaited(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-warm", email="user@example.com", user_metadata={})
    _allow_gatekeeper(monkeypatch)

    async def fake_verify_token():
        return {"user": user, "is_pro": False, "exp": time.time() + 600}

    warm_event = asyncio.Event()

    async def warm_snapshot(_conversation_id: str, _user_id: str | None):
        await asyncio.sleep(0.2)
        warm_event.set()

    async def fast_stream(*_args, **_kwargs):
        assert warm_event.is_set()
        yield "ok"

    async def fake_fetch_snapshot(**_kwargs):
        return None, []

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {
                "id": "conv-warm",
                "user_id": user.id,
                "mode": "learn",
                "settings": {},
            },
            "messages": [{"id": "assistant-warm"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "warm_conversation_snapshot", warm_snapshot)
    monkeypatch.setattr(messages_module, "fetch_conversation_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-warm",
            "content": "hello",
            "client_generated_id": "f9d454fb-29ac-4be8-9c25-0a64d9fb0b3a",
            "assistant_client_id": "a70c9c6f-13a8-4e95-8e0a-c1f59f0a3f1f",
            "mode": "learn",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_concurrent_lock_serialization(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-lock", email="user@example.com", user_metadata={})
    _allow_gatekeeper(monkeypatch)

    async def fake_verify_token():
        return {"user": user, "is_pro": False, "exp": time.time() + 600}

    async def slow_stream(*_args, **_kwargs):
        await asyncio.sleep(1.5)
        yield "ok"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {
                "id": "conv-lock",
                "user_id": user.id,
                "mode": "learn",
                "settings": {},
            },
            "messages": [{"id": "assistant-lock"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "generate_stream_explanation", slow_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload_two = {
            "conversation_id": "conv-lock",
            "content": "second",
            "client_generated_id": "5a2c635d-47c9-4ec8-bf44-7d0a4c79c4c9",
            "assistant_client_id": "76b79877-4d8a-4d7b-83c2-66a7f2f0d1a9",
            "mode": "learn",
            "prompt_mode": "eli5",
        }
        lock_acquired = await messages_module._acquire_conversation_lock(
            payload_two["conversation_id"],
            timeout_seconds=0.1,
        )
        assert lock_acquired is True
        try:
            resp_two = await app_client.post("/api/messages", json=payload_two)
            assert resp_two.status_code == 429
        finally:
            messages_module._release_conversation_lock(payload_two["conversation_id"])
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)
