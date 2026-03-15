import time
from types import SimpleNamespace

import httpx
import pytest

from services.model_provider import GEMINI_PROVIDER, GROQ_PROVIDER, OPENROUTER_PROVIDER, ModelProvider, ModelUnavailable


@pytest.mark.asyncio
async def test_route_inference_fallback(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = None
    provider.gemini_configured = True

    async def fake_fallback(_prompt, **_kwargs):
        return {"provider": "fallback", "model": "x", "content": "fallback"}

    monkeypatch.setattr(provider, "_fallback_chain", fake_fallback)
    result = await provider.route_inference("prompt")
    assert result["content"] == "fallback"
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_route_inference_stream_fallback(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = None
    provider.gemini_configured = True

    async def fake_fallback(_prompt, **_kwargs):
        return {"provider": "fallback", "model": "x", "content": "fallback"}

    monkeypatch.setattr(provider, "_fallback_chain", fake_fallback)
    chunks = []
    async for chunk in provider.route_inference_stream("prompt"):
        chunks.append(chunk)

    assert "fallback" in "".join(chunks)
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_route_inference_stream_filters_split_thinking_tags():
    provider = ModelProvider()
    provider.groq_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))

    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Visible <thi"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="nk>hidden"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" still hidden</th"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="ink> shown T"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hought:hidden"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" again\n"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="\n done"), finish_reason="stop")]
        ),
    ]

    async def fake_create(**_kwargs):
        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()

    provider.groq_client.chat.completions.create = fake_create

    output = []
    async for chunk in provider.route_inference_stream("prompt"):
        output.append(chunk)

    assert "".join(output) == "Visible  shown  done"
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_fallback_to_gemini_raises_when_unconfigured():
    provider = ModelProvider()
    provider.gemini_configured = False
    with pytest.raises(ModelUnavailable):
        await provider._fallback_to_gemini("prompt")
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_learning_mode_prefers_groq(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = object()
    provider.gemini_configured = True
    provider.openrouter_api_key = "or-key"
    provider.hf_token = "hf-key"
    calls = []

    async def fake_groq(_prompt, **_kwargs):
        calls.append(GROQ_PROVIDER)
        return {"provider": GROQ_PROVIDER, "model": "groq-model", "content": "groq"}

    async def fail_other(*_args, **_kwargs):
        raise AssertionError("Fallback provider should not be called")

    monkeypatch.setattr(provider, "_call_groq", fake_groq)
    monkeypatch.setattr(provider, "_call_gemini_direct", fail_other)
    monkeypatch.setattr(provider, "_call_openrouter", fail_other)
    monkeypatch.setattr(provider, "_call_huggingface", fail_other)

    result = await provider.route_inference("prompt", mode="eli5")

    assert result["provider"] == GROQ_PROVIDER
    assert calls == [GROQ_PROVIDER]
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_technical_mode_prefers_gemini(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = object()
    provider.gemini_configured = True
    provider.openrouter_api_key = "or-key"
    provider.hf_token = "hf-key"
    calls = []

    async def fake_gemini(_prompt, **_kwargs):
        calls.append(GEMINI_PROVIDER)
        return {"provider": GEMINI_PROVIDER, "model": "gemini-model", "content": "gemini"}

    async def fail_other(*_args, **_kwargs):
        raise AssertionError("Fallback provider should not be called")

    monkeypatch.setattr(provider, "_call_gemini_direct", fake_gemini)
    monkeypatch.setattr(provider, "_call_groq", fail_other)
    monkeypatch.setattr(provider, "_call_openrouter", fail_other)
    monkeypatch.setattr(provider, "_call_huggingface", fail_other)

    result = await provider.route_inference("prompt", mode="technical")

    assert result["provider"] == GEMINI_PROVIDER
    assert calls == [GEMINI_PROVIDER]
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_requested_openrouter_model_routes_to_openrouter(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = object()
    provider.gemini_configured = True
    provider.openrouter_api_key = "or-key"
    calls = []

    async def fake_openrouter(_prompt, **_kwargs):
        calls.append(OPENROUTER_PROVIDER)
        return {"provider": OPENROUTER_PROVIDER, "model": "qwen/qwen3.5-9b", "content": "openrouter"}

    async def fail_other(*_args, **_kwargs):
        raise AssertionError("Only OpenRouter should be called for an OpenRouter model request")

    monkeypatch.setattr(provider, "_call_openrouter", fake_openrouter)
    monkeypatch.setattr(provider, "_call_groq", fail_other)
    monkeypatch.setattr(provider, "_call_gemini_direct", fail_other)
    monkeypatch.setattr(provider, "_call_huggingface", fail_other)

    result = await provider.route_inference("prompt", model="qwen/qwen3.5-9b")

    assert result["provider"] == OPENROUTER_PROVIDER
    assert calls == [OPENROUTER_PROVIDER]
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_rate_limited_provider_is_blocked_and_skipped(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = object()
    provider.gemini_configured = True
    provider.openrouter_api_key = "or-key"
    provider.hf_token = "hf-key"
    calls = []

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)

    async def fake_groq(_prompt, **_kwargs):
        calls.append(GROQ_PROVIDER)
        raise httpx.HTTPStatusError("rate limit", request=request, response=response)

    async def fake_gemini(_prompt, **_kwargs):
        calls.append(GEMINI_PROVIDER)
        return {"provider": GEMINI_PROVIDER, "model": "gemini-model", "content": "gemini"}

    monkeypatch.setattr(provider, "_call_groq", fake_groq)
    monkeypatch.setattr(provider, "_call_gemini_direct", fake_gemini)
    monkeypatch.setattr(provider, "_call_openrouter", fake_gemini)
    monkeypatch.setattr(provider, "_call_huggingface", fake_gemini)

    first = await provider.route_inference("prompt", mode="eli5")
    second = await provider.route_inference("prompt", mode="eli5")

    assert first["provider"] == GEMINI_PROVIDER
    assert second["provider"] == GEMINI_PROVIDER
    assert provider.provider_status[GROQ_PROVIDER]["blockedUntil"] is not None
    assert provider.provider_status[GROQ_PROVIDER]["blockedUntil"] > time.time()
    assert calls == [GROQ_PROVIDER, GEMINI_PROVIDER, GEMINI_PROVIDER]
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = object()
    provider.gemini_configured = True
    provider.provider_status[GROQ_PROVIDER]["blockedUntil"] = time.time() - 1
    calls = []

    async def fake_groq(_prompt, **_kwargs):
        calls.append(GROQ_PROVIDER)
        return {"provider": GROQ_PROVIDER, "model": "groq-model", "content": "groq"}

    async def fail_other(*_args, **_kwargs):
        raise AssertionError("Fallback provider should not be called after recovery")

    monkeypatch.setattr(provider, "_call_groq", fake_groq)
    monkeypatch.setattr(provider, "_call_gemini_direct", fail_other)
    monkeypatch.setattr(provider, "_call_openrouter", fail_other)
    monkeypatch.setattr(provider, "_call_huggingface", fail_other)

    result = await provider.route_inference("prompt", mode="eli5")

    assert result["provider"] == GROQ_PROVIDER
    assert provider.provider_status[GROQ_PROVIDER]["blockedUntil"] is None
    assert calls == [GROQ_PROVIDER]
    await provider.http_client.aclose()
