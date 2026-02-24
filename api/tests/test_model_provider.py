import pytest

from services.model_provider import ModelProvider, ModelUnavailable


@pytest.mark.asyncio
async def test_route_inference_fallback(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = None

    async def fake_fallback(_prompt):
        return {"provider": "fallback", "model": "x", "content": "fallback"}

    monkeypatch.setattr(provider, "_fallback_chain", fake_fallback)
    result = await provider.route_inference("prompt")
    assert result["content"] == "fallback"
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_route_inference_stream_fallback(monkeypatch):
    provider = ModelProvider()
    provider.groq_client = None

    async def fake_fallback(_prompt):
        return {"provider": "fallback", "model": "x", "content": "fallback"}

    monkeypatch.setattr(provider, "_fallback_chain", fake_fallback)
    chunks = []
    async for chunk in provider.route_inference_stream("prompt"):
        chunks.append(chunk)

    assert "fallback" in "".join(chunks)
    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_fallback_to_gemini_raises_when_unconfigured():
    provider = ModelProvider()
    provider.gemini_configured = False
    with pytest.raises(ModelUnavailable):
        await provider._fallback_to_gemini("prompt")
    await provider.http_client.aclose()
