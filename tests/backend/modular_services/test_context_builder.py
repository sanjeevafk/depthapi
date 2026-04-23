from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.context_builder as context_builder_module
from services.context_builder import ContextBuilder


class DummyRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str):
        self.deleted.append(key)
        return 1


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, _value):
        return self

    def eq(self, _field, _value):
        return self

    def single(self):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        return SimpleNamespace(data=self._data)


class _Supabase:
    def __init__(self, conversation_data, messages_data):
        self._conversation_data = conversation_data
        self._messages_data = messages_data

    def table(self, name: str):
        if name == "conversations":
            return _Query(self._conversation_data)
        if name == "messages":
            return _Query(self._messages_data)
        return _Query(None)


@pytest.mark.asyncio
async def test_parse_snapshot_meta_handles_valid_json() -> None:
    builder = ContextBuilder()
    parsed = await builder.parse_snapshot_meta('{"mode":"learn"}', "conv-1")
    assert parsed == {"mode": "learn"}


@pytest.mark.asyncio
async def test_parse_snapshot_meta_cleans_invalid_cache(monkeypatch) -> None:
    redis = DummyRedis()

    async def fake_safe_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(context_builder_module, "safe_redis_call", fake_safe_call)
    monkeypatch.setattr(context_builder_module, "get_redis", fake_get_redis)

    builder = ContextBuilder()
    parsed = await builder.parse_snapshot_meta("not-json", "conv-2")
    assert parsed == {}
    assert redis.deleted and redis.deleted[0].endswith("conv-2:meta")


@pytest.mark.asyncio
async def test_parse_snapshot_messages_filters_and_cleans_corruption(monkeypatch) -> None:
    redis = DummyRedis()

    async def fake_safe_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(context_builder_module, "safe_redis_call", fake_safe_call)
    monkeypatch.setattr(context_builder_module, "get_redis", fake_get_redis)

    builder = ContextBuilder()
    parsed = await builder.parse_snapshot_messages(
        ['{"role":"user","content":"hello"}', "bad-json"],
        "conv-3",
    )
    assert parsed == [{"role": "user", "content": "hello"}]
    assert any(key.endswith("conv-3:messages") for key in redis.deleted)


@pytest.mark.asyncio
async def test_load_snapshot_warms_cache_on_miss() -> None:
    builder = ContextBuilder()
    calls = {"fetch": 0, "warm": 0}

    async def fake_fetch_snapshot(*, conversation_id: str, max_messages: int, timeout_seconds: float):
        _ = conversation_id, max_messages, timeout_seconds
        calls["fetch"] += 1
        if calls["fetch"] == 1:
            return None, []
        return '{"mode":"learn"}', []

    async def fake_warm_snapshot(_conversation_id: str, _user_id: str) -> None:
        calls["warm"] += 1

    result = await builder.load_snapshot(
        conversation_id="conv-4",
        user_id="user-4",
        history_limit=20,
        request_id="req-4",
        fetch_snapshot=fake_fetch_snapshot,
        warm_snapshot=fake_warm_snapshot,
    )

    assert calls == {"fetch": 2, "warm": 1}
    assert result.meta.get("mode") == "learn"
    assert result.snapshot_degraded is False


@pytest.mark.asyncio
async def test_load_conversation_from_db_returns_messages_for_owner(monkeypatch) -> None:
    builder = ContextBuilder()
    supabase = _Supabase(
        conversation_data={"id": "c1", "user_id": "u1", "mode": "learn", "settings": {}},
        messages_data=[
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u1"},
        ],
    )
    async def fake_to_thread(fn, *args, **kwargs):
        _ = args, kwargs
        return fn()
    monkeypatch.setattr(context_builder_module.asyncio, "to_thread", fake_to_thread)

    conversation, messages = await builder.load_conversation_from_db(
        "c1",
        "u1",
        10,
        get_supabase_admin_fn=lambda: supabase,
    )
    assert conversation.get("id") == "c1"
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_extract_turns_and_socratic_context() -> None:
    builder = ContextBuilder()
    messages = [
        {"role": "user", "content": "How does DNS work?"},
        {"role": "assistant", "content": "It resolves names."},
    ]
    last_user, last_assistant = builder.extract_turns(messages)
    assert last_user == "How does DNS work?"
    assert last_assistant == "It resolves names."
    context = builder.build_socratic_context(messages)
    assert "User last answered" in context


@pytest.mark.asyncio
async def test_build_context_returns_signature_and_messages() -> None:
    builder = ContextBuilder()
    history = [
        {"role": "user", "content": "Explain TCP."},
        {"role": "assistant", "content": "TCP is connection-oriented."},
    ]
    messages, signature, build_ms = await builder.build_context(
        history,
        request_id="req-ctx",
        conversation_id="conv-ctx",
        context_max_tokens=200,
        summary_max_tokens=50,
        max_turns=4,
    )
    assert messages
    assert isinstance(signature, str) and len(signature) == 64
    assert build_ms >= 0
