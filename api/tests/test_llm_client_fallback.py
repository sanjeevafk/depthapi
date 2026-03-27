import importlib
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

    response = await llm_client_module.create_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert response.choices[0].message.content == "ok"
    assert attempts == ["groq", "cerebras", "gemini"]
