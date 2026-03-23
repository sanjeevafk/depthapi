import importlib
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from pydantic import SecretStr

import services.llm_client as llm_client_module


def _invalid_model_error(model_alias: str) -> APIStatusError:
    request = httpx.Request("POST", "https://proxy.example/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    message = f"Invalid model name passed in model={model_alias}"
    return APIStatusError(
        message=message,
        response=response,
        body={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": "model",
                "code": "model_not_found",
            }
        },
    )


class _StreamChunk:
    def __init__(self, content: str | None):
        self.model = "llama-3.1-8b-instant"
        self.usage = None
        self._hidden_params = {}
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


@pytest.mark.asyncio
async def test_create_chat_completion_recovers_from_invalid_alias(monkeypatch):
    importlib.reload(llm_client_module)

    class LiteLLMCompletions:
        def __init__(self):
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise _invalid_model_error("default-fast")

    class GroqCompletions:
        def __init__(self):
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="fallback-ok"))],
                usage=None,
                model="llama-3.1-8b-instant",
            )

    litellm_completions = LiteLLMCompletions()
    groq_completions = GroqCompletions()
    litellm_client = SimpleNamespace(chat=SimpleNamespace(completions=litellm_completions))
    groq_client = SimpleNamespace(chat=SimpleNamespace(completions=groq_completions))

    async def fake_get_llm_client():
        return litellm_client

    async def fake_get_groq_client():
        return groq_client

    monkeypatch.setattr(llm_client_module, "get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(llm_client_module, "get_groq_client", fake_get_groq_client)
    monkeypatch.setattr(llm_client_module.sentry_sdk, "capture_exception", lambda *_args, **_kwargs: None)

    result = await llm_client_module.create_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.choices[0].message.content == "fallback-ok"
    assert litellm_completions.calls[0]["model"] == "default-fast"
    assert groq_completions.calls[0]["model"] == "llama-3.1-8b-instant"


@pytest.mark.asyncio
async def test_create_chat_completion_recovers_via_litellm_model_mapping(monkeypatch):
    importlib.reload(llm_client_module)

    class LiteLLMCompletions:
        def __init__(self):
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"] == "default-fast":
                raise _invalid_model_error("default-fast")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="proxy-fallback-ok"))],
                usage=None,
                model="groq/llama-3.1-8b-instant",
            )

    litellm_completions = LiteLLMCompletions()
    litellm_client = SimpleNamespace(chat=SimpleNamespace(completions=litellm_completions))

    async def fake_get_llm_client():
        return litellm_client

    async def fake_get_groq_client():
        raise AssertionError("Direct Groq fallback should not run when proxy fallback succeeds.")

    monkeypatch.setattr(llm_client_module, "get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(llm_client_module, "get_groq_client", fake_get_groq_client)
    monkeypatch.setattr(llm_client_module.sentry_sdk, "capture_exception", lambda *_args, **_kwargs: None)

    result = await llm_client_module.create_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.choices[0].message.content == "proxy-fallback-ok"
    assert litellm_completions.calls[0]["model"] == "default-fast"
    assert litellm_completions.calls[1]["model"] == "groq/llama-3.1-8b-instant"


@pytest.mark.asyncio
async def test_stream_chat_completion_recovers_from_invalid_alias(monkeypatch):
    importlib.reload(llm_client_module)

    class LiteLLMCompletions:
        def __init__(self):
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise _invalid_model_error("default-fast")

    class GroqCompletions:
        def __init__(self):
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)

            async def _stream():
                yield _StreamChunk("hello")
                yield _StreamChunk(None)

            return _stream()

    litellm_completions = LiteLLMCompletions()
    groq_completions = GroqCompletions()
    litellm_client = SimpleNamespace(chat=SimpleNamespace(completions=litellm_completions))
    groq_client = SimpleNamespace(chat=SimpleNamespace(completions=groq_completions))

    async def fake_get_llm_client():
        return litellm_client

    async def fake_get_groq_client():
        return groq_client

    monkeypatch.setattr(llm_client_module, "get_llm_client", fake_get_llm_client)
    monkeypatch.setattr(llm_client_module, "get_groq_client", fake_get_groq_client)
    monkeypatch.setattr(llm_client_module.sentry_sdk, "capture_exception", lambda *_args, **_kwargs: None)

    chunks: list[str] = []
    async for chunk in llm_client_module.stream_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hi"}],
    ):
        chunks.append(chunk)

    assert chunks == ["hello"]
    assert litellm_completions.calls[0]["model"] == "default-fast"
    assert litellm_completions.calls[0]["stream"] is True
    assert groq_completions.calls[0]["model"] == "llama-3.1-8b-instant"
    assert groq_completions.calls[0]["stream"] is True


def test_resolve_groq_api_key_uses_secret_value(monkeypatch):
    importlib.reload(llm_client_module)

    settings = SimpleNamespace(groq_api_key=SecretStr("groq-test-key"))
    monkeypatch.setattr(llm_client_module, "get_settings", lambda: settings)

    assert llm_client_module._resolve_groq_api_key() == "groq-test-key"
