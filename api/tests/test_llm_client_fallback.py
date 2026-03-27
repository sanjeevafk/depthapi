import importlib
import asyncio
from types import SimpleNamespace

import pytest

import services.llm_client as llm_client_module


class DummyProviderState:
    async def should_attempt(self, _provider):
        return True

    async def mark_success(self, _provider):
        return None

    async def mark_failure(self, _provider):
        return None


@pytest.mark.asyncio
async def test_create_chat_completion_cascades_through_primary_providers(monkeypatch):
    # Restore real module functions because conftest autouse fixtures patch them.
    importlib.reload(llm_client_module)

    attempts: list[str] = []

    class FakeCompletions:
        def __init__(self, provider: str):
            self.provider = provider

        async def create(self, **kwargs):
            attempts.append(self.provider)
            if self.provider in {"groq", "cerebras"}:
                raise RuntimeError(f"{self.provider} unavailable")
            return SimpleNamespace(
                model=kwargs.get("model", "gemini-2.5-flash"),
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    async def fake_get_provider_client(provider: str):
        return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(provider)))

    monkeypatch.setattr(llm_client_module, "_provider_state_manager", DummyProviderState())
    monkeypatch.setattr(llm_client_module, "_get_provider_client", fake_get_provider_client)
    monkeypatch.setattr(llm_client_module, "_is_retryable_error", lambda exc: isinstance(exc, RuntimeError))
    async def fake_provider_within_runtime_limits(*_args, **_kwargs):
        return True

    monkeypatch.setattr(llm_client_module, "_provider_within_runtime_limits", fake_provider_within_runtime_limits)
    async def fake_increment_provider_usage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(llm_client_module, "_increment_provider_usage", fake_increment_provider_usage)

    response = await llm_client_module.create_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert response.choices[0].message.content == "ok"
    assert attempts == ["groq", "cerebras", "gemini"]


@pytest.mark.asyncio
async def test_provider_state_mark_failure_is_atomic(monkeypatch):
    importlib.reload(llm_client_module)

    class FakeRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            await asyncio.sleep(0.001)
            return self.store.get(key)

        async def setex(self, key, _ttl, value):
            await asyncio.sleep(0.001)
            self.store[key] = value
            return True

    fake_redis = FakeRedis()

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(llm_client_module, "get_redis", fake_get_redis)

    manager = llm_client_module.ProviderStateManager(
        failure_threshold=999,
        cooldown_seconds=30,
        state_ttl_seconds=300,
    )

    await asyncio.gather(*(manager.mark_failure("groq") for _ in range(25)))
    state = await manager._read_state("groq")

    assert state["failure_count"] == 25
    assert state["blocked_until"] == 0
