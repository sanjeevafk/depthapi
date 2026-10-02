"""Unit tests for local LLM fallback and CPU context throttling."""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import SecretStr

from api.services.inference import inference as inference_module


class _StubSettingsWithLocal:
    openai_api_key = SecretStr("")
    llm_model = "gpt-4o-mini"
    llm_timeout_seconds = 60
    local_llm_base_url = "http://localhost:11434/v1"
    local_llm_model = "qwen2.5:1.5b"
    local_llm_api_key = SecretStr("local")
    local_llm_timeout_seconds = 120
    local_llm_max_context_chunks = 2


class _StubSettingsWithBoth:
    openai_api_key = SecretStr("sk-openai-key")
    llm_model = "gpt-4o-mini"
    llm_timeout_seconds = 60
    local_llm_base_url = "http://localhost:11434/v1"
    local_llm_model = "qwen2.5:1.5b"
    local_llm_api_key = SecretStr("local")
    local_llm_timeout_seconds = 120
    local_llm_max_context_chunks = 2


def _install_configurable_stub(monkeypatch, client_behavior_fn):
    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)
            self.delta = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)] if content is not None else []

    class _StreamResponse:
        def __init__(self, tokens: list[str]):
            self.tokens = tokens

        def __aiter__(self):
            self._iter = iter(self.tokens)
            return self

        async def __anext__(self):
            try:
                token = next(self._iter)
                return _Response(token)
            except StopIteration:
                raise StopAsyncIteration

    class _Completions:
        def __init__(self, client_instance):
            self._client = client_instance

        async def create(self, **kwargs):
            return await client_behavior_fn(self._client, kwargs)

    class _Chat:
        def __init__(self, client_instance):
            self.completions = _Completions(client_instance)

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.chat = _Chat(self)

    stub = types.ModuleType("openai")
    stub.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", stub)


@pytest.mark.asyncio
async def test_local_fallback_when_openai_key_empty(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def behavior(client, kwargs):
        calls.append({"client_kwargs": client.kwargs, "call_kwargs": kwargs})
        class _Choice:
            message = types.SimpleNamespace(content="Local answer [1].")
        return types.SimpleNamespace(choices=[_Choice()])

    _install_configurable_stub(monkeypatch, behavior)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettingsWithLocal())

    contexts = [{"content": "Local knowledge base."}]
    answer = await inference_module.generate_response("Question?", contexts)

    assert answer == "Local answer [1]."
    assert len(calls) == 1
    assert calls[0]["client_kwargs"].get("base_url") == "http://localhost:11434/v1"
    assert calls[0]["call_kwargs"].get("model") == "qwen2.5:1.5b"


@pytest.mark.asyncio
async def test_cascade_from_openai_failure_to_local(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def behavior(client, kwargs):
        calls.append({"client_kwargs": client.kwargs, "call_kwargs": kwargs})
        # Fail the primary OpenAI call, succeed on the local one
        if "base_url" not in client.kwargs:
            raise ConnectionError("OpenAI API unreachable")
        class _Choice:
            message = types.SimpleNamespace(content="Fallback answer [1].")
        return types.SimpleNamespace(choices=[_Choice()])

    _install_configurable_stub(monkeypatch, behavior)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettingsWithBoth())

    contexts = [{"content": "Important context."}]
    answer = await inference_module.generate_response("Question?", contexts)

    assert answer == "Fallback answer [1]."
    assert len(calls) == 2
    # First call attempted primary OpenAI
    assert "base_url" not in calls[0]["client_kwargs"]
    # Second call cascaded to local LLM base URL
    assert calls[1]["client_kwargs"].get("base_url") == "http://localhost:11434/v1"


@pytest.mark.asyncio
async def test_local_llm_context_chunk_capping(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def behavior(client, kwargs):
        calls.append({"call_kwargs": kwargs})
        class _Choice:
            message = types.SimpleNamespace(content="Capped answer [1].")
        return types.SimpleNamespace(choices=[_Choice()])

    _install_configurable_stub(monkeypatch, behavior)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettingsWithLocal())

    contexts = [
        {"content": "Chunk one."},
        {"content": "Chunk two."},
        {"content": "Chunk three."},
        {"content": "Chunk four."},
    ]
    await inference_module.generate_response("Question?", contexts)

    assert len(calls) == 1
    user_msg = calls[0]["call_kwargs"]["messages"][1]["content"]
    assert "[1] Chunk one." in user_msg
    assert "[2] Chunk two." in user_msg
    # Chunks 3 and 4 should be omitted due to local_llm_max_context_chunks = 2
    assert "[3] Chunk three." not in user_msg
    assert "[4] Chunk four." not in user_msg


@pytest.mark.asyncio
async def test_local_streaming_fallback(monkeypatch):
    class _Delta:
        def __init__(self, content):
            self.content = content

    class _StreamChoice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _StreamChunk:
        def __init__(self, content):
            self.choices = [_StreamChoice(content)]

    class _AsyncStream:
        def __init__(self, tokens):
            self.tokens = tokens

        def __aiter__(self):
            self._iter = iter(self.tokens)
            return self

        async def __anext__(self):
            try:
                return _StreamChunk(next(self._iter))
            except StopIteration:
                raise StopAsyncIteration

    async def behavior(client, kwargs):
        if "base_url" not in client.kwargs:
            raise RuntimeError("Primary OpenAI stream error")
        return _AsyncStream(["Local ", "streamed ", "tokens [1]."])

    _install_configurable_stub(monkeypatch, behavior)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettingsWithBoth())

    tokens = []
    async for token in inference_module.generate_stream_response("Question?", [{"content": "Context."}]):
        tokens.append(token)

    assert "".join(tokens) == "Local streamed tokens [1]."


@pytest.mark.asyncio
async def test_all_providers_fail_to_raw_fallback(monkeypatch):
    async def behavior(client, kwargs):
        raise RuntimeError("Service down")

    _install_configurable_stub(monkeypatch, behavior)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettingsWithBoth())

    contexts = [{"content": "Direct fallback raw context."}]
    answer = await inference_module.generate_response("Question?", contexts)

    assert answer == "Direct fallback raw context."
