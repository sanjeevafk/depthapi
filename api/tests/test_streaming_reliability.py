import asyncio
import time
from types import SimpleNamespace

import pytest

import main as main_app
import routers.messages as messages_module
import routers.query as query_module
from conftest import FakeSupabase
from services.rate_limit import TokenReservation


@pytest.mark.asyncio
async def test_query_stream_fallback_on_start_timeout(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 3
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
        json={"topic": "test", "levels": ["eli5"], "mode": "learning", "message_id": "b3f5d29c-7b1a-4d68-9a8b-ef0b3b3a1c5a"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: meta" in text
    assert "id:" in text
    assert "event: chunk" in text
    assert "fallback result" in text
    assert "event: done" in text
    assert text.count("event: done") == 1


@pytest.mark.asyncio
async def test_query_stream_allows_slow_first_chunk_without_cancel_loop(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    async def slow_first_chunk(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        yield "late first chunk"

    async def fallback_generate(*_args, **_kwargs):
        return "fallback should not be used"

    monkeypatch.setattr(query_module, "generate_stream_explanation", slow_first_chunk)
    monkeypatch.setattr(query_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "test", "levels": ["eli5"], "mode": "learning", "bypass_cache": True},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: chunk" in text
    assert "late first chunk" in text
    assert "fallback should not be used" not in text
    assert "event: done" in text


@pytest.mark.asyncio
async def test_query_stream_fallback_on_stream_exception(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("upstream stream failure")
        yield "unreachable"

    async def fallback_generate(*_args, **_kwargs):
        return "fallback after stream exception"

    monkeypatch.setattr(query_module, "generate_stream_explanation", crashing_stream)
    monkeypatch.setattr(query_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "test", "levels": ["eli5"], "mode": "learning"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: chunk" in text
    assert "fallback after stream exception" in text
    assert "event: done" in text
    assert text.count("event: done") == 1
    assert "event: error" not in text


@pytest.mark.asyncio
async def test_query_stream_fallback_allows_slow_generation_budget(app_client, monkeypatch, test_settings):
    test_settings.environment = "production"
    test_settings.stream_start_timeout_seconds = 0.2
    test_settings.stream_max_seconds = 3
    test_settings.stream_fallback_budget_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream exploded immediately")
        yield "unreachable"

    async def slow_fallback_generate(*_args, **_kwargs):
        await asyncio.sleep(1.2)
        return "slow fallback response"

    monkeypatch.setattr(query_module, "generate_stream_explanation", crashing_stream)
    monkeypatch.setattr(query_module, "generate_explanation", slow_fallback_generate)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "slow-fallback", "levels": ["eli5"], "mode": "learning"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: chunk" in text
    assert "slow fallback response" in text
    assert "event: done" in text
    assert "event: error" not in text


@pytest.mark.asyncio
async def test_query_stream_duplicate_in_progress_returns_wait(app_client, monkeypatch, test_settings):
    started_at = int(time.time())

    async def fake_cache_get(_key):
        return {"status": "in_progress", "started_at": started_at}

    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    payload = {
        "topic": "duplicate",
        "levels": ["eli5"],
        "mode": "learning",
        "message_id": "f9d454fb-29ac-4be8-9c25-0a64d9fb0b3a",
    }

    resp = await app_client.post("/api/query/stream", json=payload)
    assert resp.status_code == 200
    text = resp.text.replace(" ", "")
    assert "event:status" in text
    assert "\"status\":\"waiting\"" in text


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
            "conversations": {"id": "conv-1", "user_id": user.id, "mode": "learning", "settings": {}},
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
            "mode": "learning",
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
async def test_messages_reclaims_stale_in_progress_idempotency(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-reclaim", email="user@example.com", user_metadata={})
    stale_started_at = int(time.time()) - 999

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fast_stream(*_args, **_kwargs):
        yield "ok"

    async def fake_cache_get(key):
        if str(key).startswith("knowbear:idempotency:"):
            return {"status": "in_progress", "started_at": stale_started_at}
        return None

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-reclaim", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-reclaim"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(messages_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-reclaim",
            "content": "hello",
            "client_generated_id": "8a5f7736-2edb-4f7b-bf45-9b8f2ea1ea1e",
            "assistant_client_id": "8f2c9e58-0ae5-4fce-bc73-51f1ca6f43c4",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
        assert "ok" in resp.text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_query_stream_idempotency_replay_with_message_id(app_client, monkeypatch, test_settings):
    store: dict[str, dict] = {}

    async def fake_stream(*_args, **_kwargs):
        yield "hello replay"

    async def fake_cache_get(key):
        return store.get(str(key))

    async def fake_cache_set(key, value, ttl=None):
        store[str(key)] = value
        return True

    async def fake_cache_set_if_absent(key, value, ttl):
        k = str(key)
        if k in store:
            return False
        store[k] = value
        return True

    monkeypatch.setattr(query_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "cache_set_if_absent", fake_cache_set_if_absent)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    payload = {
        "topic": "test",
        "levels": ["eli5"],
        "mode": "learning",
        "message_id": "232c2670-6ad8-48fb-a9a4-b416cc654e79",
    }

    first = await app_client.post("/api/query/stream", json=payload)
    assert first.status_code == 200
    assert "hello replay" in first.text

    second = await app_client.post("/api/query/stream", json=payload)
    assert second.status_code == 200
    assert "\"replay\":true" in second.text.replace(" ", "")


@pytest.mark.asyncio
async def test_query_stream_stale_in_progress_does_not_clobber_completed_record(app_client, monkeypatch, test_settings):
    key_reads: dict[str, int] = {}
    cache_set_calls: list[tuple[str, dict, int | None]] = []

    async def fail_stream(*_args, **_kwargs):
        raise AssertionError("stream should not execute when replay is returned")
        yield ""  # pragma: no cover

    async def fake_cache_get(key):
        k = str(key)
        key_reads[k] = key_reads.get(k, 0) + 1
        if key_reads[k] == 1:
            return None
        if key_reads[k] == 2:
            return {"status": "in_progress", "started_at": 0}
        return {"status": "completed", "response": "already done"}

    async def fake_cache_set(key, value, ttl=None):
        cache_set_calls.append((str(key), dict(value), ttl))
        return True

    async def fake_cache_set_if_absent(_key, _value, _ttl):
        return False

    monkeypatch.setattr(query_module, "generate_stream_explanation", fail_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "cache_set_if_absent", fake_cache_set_if_absent)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    payload = {
        "topic": "race",
        "levels": ["eli5"],
        "mode": "learning",
        "message_id": "3e964baa-386f-4d2f-9cc4-3f0dc0855e61",
    }

    resp = await app_client.post("/api/query/stream", json=payload)
    assert resp.status_code == 200
    assert "\"replay\":true" in resp.text.replace(" ", "")
    assert "already done" in resp.text
    assert cache_set_calls == []


@pytest.mark.asyncio
async def test_messages_fallback_on_stream_exception(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-stream-fallback", email="user@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream exploded")
        yield "unreachable"

    async def fallback_generate(*_args, **_kwargs):
        return "message fallback response"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-fallback", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-fallback"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", crashing_stream)
    monkeypatch.setattr(messages_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-fallback",
            "content": "hello",
            "client_generated_id": "3d204b5f-fbf4-4ef7-b223-5f58eab1e7bf",
            "assistant_client_id": "4f8ae3b6-d23f-4b17-9405-118fd7f79ece",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
        assert "message fallback response" in resp.text
        assert "event: done" in resp.text
        assert "event: error" not in resp.text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_allows_slow_first_chunk_without_cancel_loop(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-slow-first-chunk", email="slow@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def slow_first_chunk(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        yield "late first chunk"

    async def fallback_generate(*_args, **_kwargs):
        return "fallback should not be used"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-slow-first", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-slow-first"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", slow_first_chunk)
    monkeypatch.setattr(messages_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-slow-first",
            "content": "hello",
            "client_generated_id": "9d78cfd2-4884-45dc-a5fc-cd6f8d415c62",
            "assistant_client_id": "b5a609ec-0e4c-46ca-b4a7-f3ec8e5f8499",
            "mode": "learning",
            "prompt_mode": "eli5",
            "regenerate": True,
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        text = resp.text
        assert "event: delta" in text
        assert "late first chunk" in text
        assert "fallback should not be used" not in text
        assert "event: done" in text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_fallback_allows_slow_generation_budget(app_client, monkeypatch, test_settings):
    test_settings.environment = "production"
    test_settings.stream_start_timeout_seconds = 0.2
    test_settings.stream_max_seconds = 3
    test_settings.stream_fallback_budget_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-stream-slow-fallback", email="user@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream exploded immediately")
        yield "unreachable"

    async def slow_fallback_generate(*_args, **_kwargs):
        await asyncio.sleep(1.2)
        return "message slow fallback response"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-slow-fallback", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-slow-fallback"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", crashing_stream)
    monkeypatch.setattr(messages_module, "generate_explanation", slow_fallback_generate)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-slow-fallback",
            "content": "hello",
            "client_generated_id": "c9f4ea73-a8ba-49fa-aef8-6b6bc8f4d7ca",
            "assistant_client_id": "1a2ecfdf-1ed2-49e9-ae18-22f6e3fbf54b",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        text = resp.text
        assert "event: delta" in text
        assert "message slow fallback response" in text
        assert "event: done" in text
        assert "event: error" not in text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_query_stream_partial_failure_returns_done_without_error(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05
    user = SimpleNamespace(id="user-query-partial", email="user@example.com", user_metadata={})

    async def fake_auth():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return True

    async def partial_then_fail(*_args, **_kwargs):
        yield "partial technical chunk"
        raise RuntimeError("stream interrupted")

    main_app.app.dependency_overrides[query_module.verify_token_optional] = fake_auth
    monkeypatch.setattr(query_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(query_module, "generate_stream_explanation", partial_then_fail)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    try:
        resp = await app_client.post(
            "/api/query/stream",
            json={"topic": "test", "levels": ["eli15"], "mode": "technical"},
        )

        assert resp.status_code == 200
        text = resp.text
        assert "partial technical chunk" in text
        assert "event: done" in text
        assert text.count("event: done") == 1
        assert "event: error" not in text
    finally:
        main_app.app.dependency_overrides.pop(query_module.verify_token_optional, None)


@pytest.mark.asyncio
async def test_query_stream_waits_for_history_persistence(app_client, monkeypatch, fake_user):
    async def fake_auth():
        return {"user": fake_user}

    async def fake_stream(*_args, **_kwargs):
        yield "history save check"

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    calls = []

    async def fake_save_to_history(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        calls.append(True)

    main_app.app.dependency_overrides[query_module.verify_token_optional] = fake_auth
    monkeypatch.setattr(query_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(query_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(query_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(query_module, "save_to_history", fake_save_to_history)

    try:
        start = time.perf_counter()
        resp = await app_client.post(
            "/api/query/stream",
            json={"topic": "Persist stream", "levels": ["eli5"], "mode": "learning"},
        )
        elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        assert "history save check" in resp.text
        assert calls == [True]
        assert elapsed >= 0.08
    finally:
        main_app.app.dependency_overrides.pop(query_module.verify_token_optional, None)


@pytest.mark.asyncio
async def test_messages_partial_failure_returns_done_without_error(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-stream-partial", email="user@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return True

    async def partial_then_fail(*_args, **_kwargs):
        yield "partial technical chunk"
        raise RuntimeError("stream interrupted")

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-partial", "user_id": user.id, "mode": "technical", "settings": {}},
            "messages": [{"id": "assistant-partial"}],
            "users": {"is_pro": True},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", partial_then_fail)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-partial",
            "content": "hello",
            "client_generated_id": "1eb91e58-e2b6-4f47-bece-f8dca3854e95",
            "assistant_client_id": "6d46d539-5f21-47bc-9e46-c21810108ba8",
            "mode": "technical",
            "prompt_mode": "eli15",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
        assert "partial technical chunk" in resp.text
        assert "event: done" in resp.text
        assert "event: error" not in resp.text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_query_stream_emits_single_done_when_stream_and_fallback_fail(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream hard failure")
        yield "unreachable"

    async def crashing_fallback(*_args, **_kwargs):
        raise RuntimeError("fallback hard failure")

    monkeypatch.setattr(query_module, "generate_stream_explanation", crashing_stream)
    monkeypatch.setattr(query_module, "generate_explanation", crashing_fallback)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/query/stream",
        json={"topic": "test", "levels": ["eli5"], "mode": "learning"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: error" in text
    assert "event: done" in text
    assert text.count("event: done") == 1


@pytest.mark.asyncio
async def test_query_stream_does_not_hang_when_stream_close_blocks(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    class BlockingCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(5)
            raise StopAsyncIteration

        async def aclose(self):
            await asyncio.sleep(1.5)

    def hanging_stream(*_args, **_kwargs):
        return BlockingCloseStream()

    async def fallback_generate(*_args, **_kwargs):
        return "fallback after close timeout"

    monkeypatch.setattr(query_module, "generate_stream_explanation", hanging_stream)
    monkeypatch.setattr(query_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(query_module, "get_settings", lambda: test_settings)

    started = time.perf_counter()
    resp = await app_client.post(
        "/api/query/stream",
        json={
            "topic": "close timeout",
            "levels": ["eli5"],
            "mode": "learning",
            "bypass_cache": True,
        },
    )
    text = resp.text
    elapsed = time.perf_counter() - started
    assert resp.status_code == 200
    assert "event: chunk" in text
    assert "fallback after close timeout" in text
    assert "event: done" in text
    assert elapsed < 1.2


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
            "conversations": {"id": "conv-2", "user_id": user.id, "mode": "learning", "settings": {}},
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
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        abort_logs = [entry for entry in calls if entry[0] == "messages_abort_confirmed"]
        assert abort_logs
        assert abort_logs[0][1].get("tokens_after_abort") == 0
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_stream_does_not_hang_when_stream_close_blocks(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-close-timeout", email="close@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    class BlockingCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(5)
            raise StopAsyncIteration

        async def aclose(self):
            await asyncio.sleep(1.5)

    def hanging_stream(*_args, **_kwargs):
        return BlockingCloseStream()

    async def fallback_generate(*_args, **_kwargs):
        return "messages fallback after close timeout"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-close-timeout", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-close-timeout"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", hanging_stream)
    monkeypatch.setattr(messages_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-close-timeout",
            "content": "hang-close-timeout-check",
            "client_generated_id": "f4e667f4-bde8-4ac2-9f0f-b9d213444745",
            "assistant_client_id": "89189fc9-82d6-48f7-96dd-f9cbd585f3f8",
            "mode": "learning",
            "prompt_mode": "eli5",
            "regenerate": True,
        }

        started = time.perf_counter()
        resp = await app_client.post("/api/messages", json=payload)
        text = resp.text
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200
        assert "event: delta" in text
        assert "messages fallback after close timeout" in text
        assert "event: done" in text
        assert elapsed < 1.2
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_fallback_when_stream_completes_without_chunks(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 0.2
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.05

    user = SimpleNamespace(id="user-empty-stream", email="empty@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def empty_stream(*_args, **_kwargs):
        if False:
            yield "unreachable"

    async def fallback_generate(*_args, **_kwargs):
        return "fallback for empty stream"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-empty-stream", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-empty-stream"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", empty_stream)
    monkeypatch.setattr(messages_module, "generate_explanation", fallback_generate)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-empty-stream",
            "content": "empty-stream-check",
            "client_generated_id": "b6ff4c4b-4ce9-45ff-bf88-99160fe8d45e",
            "assistant_client_id": "9ea7ea2f-80d0-43b7-b227-6279400df4c6",
            "mode": "learning",
            "prompt_mode": "eli5",
            "regenerate": True,
        }

        resp = await app_client.post("/api/messages", json=payload)
        text = resp.text

        assert resp.status_code == 200
        assert "event: delta" in text
        assert "fallback for empty stream" in text
        assert "event: done" in text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_technical_mode_blocks_free_user(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-free", email="free@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-tech", "user_id": user.id, "mode": "learning", "settings": {}},
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-tech",
            "content": "debug this",
            "client_generated_id": "f03b3af7-30d6-490f-9a9f-2f683f8ef713",
            "assistant_client_id": "03de0b9c-8514-429f-b97d-45f327cd5f57",
            "mode": "technical",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 403
        assert "Pro feature" in resp.json()["detail"]
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_technical_mode_allows_pro_user(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-pro", email="pro@example.com", user_metadata={})

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return True

    async def fast_stream(*_args, **_kwargs):
        yield "ok"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-tech-pro", "user_id": user.id, "mode": "technical", "settings": {}},
            "messages": [{"id": "assistant-tech"}],
            "users": {"is_pro": True},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-tech-pro",
            "content": "debug this",
            "client_generated_id": "3f76fe87-bd73-4709-9fa2-703af1eedf04",
            "assistant_client_id": "5f2f8e82-b394-4f07-956d-c60f9584381a",
            "mode": "technical",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "event: delta" in resp.text
        assert "ok" in resp.text
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_regeneration_forwards_temperature(app_client, monkeypatch, test_settings):
    user = SimpleNamespace(id="user-regen", email="regen@example.com", user_metadata={})
    captured = {}

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fake_stream(*_args, **kwargs):
        captured["temperature"] = kwargs.get("temperature")
        captured["regenerate"] = kwargs.get("regenerate")
        yield "regen"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-regen", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-regen"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fake_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-regen",
            "content": "hello",
            "client_generated_id": "889e2f13-b55e-46cd-b8de-c640f81b4cab",
            "assistant_client_id": "d5e814fd-5ff7-4c8f-88e2-4e09e2e25028",
            "mode": "learning",
            "prompt_mode": "eli5",
            "regenerate": True,
            "temperature": 0.9,
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        assert "regen" in resp.text
        assert captured["regenerate"] is True
        assert captured["temperature"] == 0.9
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_untrusted_peer_ignores_forwarded_headers(app_client, monkeypatch, test_settings):
    test_settings.trusted_proxies = "10.10.10.10"
    user = SimpleNamespace(id="user-ip-untrusted", email="ip@example.com", user_metadata={})
    captured: dict[str, str] = {}

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fake_enforce_request_controls(*_args, **kwargs):
        captured["client_ip"] = str(kwargs.get("client_ip", ""))
        return TokenReservation(
            identifier="user:test",
            mode="learning",
            reserved_tokens=100,
            daily_key="knowbear:quota:user:test:learning",
            hourly_key="knowbear:quota_hour:user:test:learning",
            hourly_bucket=0,
            is_anonymous=False,
        )

    async def fast_stream(*_args, **_kwargs):
        yield "ok"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-ip-untrusted", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-ip-untrusted"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "enforce_request_controls", fake_enforce_request_controls)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-ip-untrusted",
            "content": "hello",
            "client_generated_id": "2c801f77-0ab9-4e7d-a7b6-b95b69f8627e",
            "assistant_client_id": "d6353ce2-47c3-4c7f-9c1d-6d7f8df6f4ed",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post(
            "/api/messages",
            json=payload,
            headers={
                "x-forwarded-for": "203.0.113.10, 198.51.100.8",
                "x-real-ip": "203.0.113.5",
            },
        )
        assert resp.status_code == 200
        assert captured.get("client_ip") not in {"203.0.113.10", "198.51.100.8", "203.0.113.5"}
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_trusted_peer_uses_leftmost_forwarded_ip(app_client, monkeypatch, test_settings):
    test_settings.trusted_proxies = "127.0.0.1"
    user = SimpleNamespace(id="user-ip-trusted", email="ip@example.com", user_metadata={})
    captured: dict[str, str] = {}

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fake_enforce_request_controls(*_args, **kwargs):
        captured["client_ip"] = str(kwargs.get("client_ip", ""))
        return TokenReservation(
            identifier="user:test",
            mode="learning",
            reserved_tokens=100,
            daily_key="knowbear:quota:user:test:learning",
            hourly_key="knowbear:quota_hour:user:test:learning",
            hourly_bucket=0,
            is_anonymous=False,
        )

    async def fast_stream(*_args, **_kwargs):
        yield "ok"

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-ip-trusted", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-ip-trusted"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "enforce_request_controls", fake_enforce_request_controls)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    try:
        payload = {
            "conversation_id": "conv-ip-trusted",
            "content": "hello",
            "client_generated_id": "a01174ef-82f6-4e83-bcbf-b98f8f95fe0e",
            "assistant_client_id": "77a3d538-f47e-4ab2-befd-d92465274e20",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post(
            "/api/messages",
            json=payload,
            headers={
                "x-forwarded-for": "203.0.113.10, 198.51.100.8",
                "x-real-ip": "203.0.113.5",
            },
        )
        assert resp.status_code == 200
        assert captured.get("client_ip") == "203.0.113.10"
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)


@pytest.mark.asyncio
async def test_messages_stream_performance_guardrails(app_client, monkeypatch, test_settings):
    test_settings.stream_start_timeout_seconds = 1
    test_settings.stream_max_seconds = 2
    test_settings.stream_heartbeat_seconds = 0.1

    user = SimpleNamespace(id="user-perf", email="perf@example.com", user_metadata={})
    captured: dict[str, float | None] = {}

    async def fake_verify_token():
        return {"user": user}

    async def fake_is_pro(*_args, **_kwargs):
        return False

    async def fast_stream(*_args, **_kwargs):
        yield "ok"

    def fake_log_sampled_success(event, **kwargs):
        if event == "messages_stream_observed":
            captured["first_event_ms"] = kwargs.get("first_event_ms")
            captured["latency_ms"] = kwargs.get("latency_ms")

    fake_supabase = FakeSupabase(
        responses={
            "conversations": {"id": "conv-perf", "user_id": user.id, "mode": "learning", "settings": {}},
            "messages": [{"id": "assistant-perf"}],
            "users": {"is_pro": False},
        }
    )

    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "check_is_pro", fake_is_pro)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(messages_module, "log_sampled_success", fake_log_sampled_success)

    try:
        payload = {
            "conversation_id": "conv-perf",
            "content": "hello",
            "client_generated_id": "03d91f7c-69f9-4c2c-9f6a-3773aa6cd03b",
            "assistant_client_id": "2e3c2a2d-2a7f-4932-8a5c-2d2878aa2c90",
            "mode": "learning",
            "prompt_mode": "eli5",
        }

        resp = await app_client.post("/api/messages", json=payload)
        assert resp.status_code == 200
        first_event_ms = captured.get("first_event_ms")
        latency_ms = captured.get("latency_ms")
        assert first_event_ms is not None
        assert first_event_ms <= 2000
        assert latency_ms is not None
        assert latency_ms <= 30000
    finally:
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)
