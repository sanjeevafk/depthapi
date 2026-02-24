import pytest

import services.inference as inference_module
import services.model_provider as model_provider


@pytest.mark.asyncio
async def test_generate_explanation_unknown_level():
    with pytest.raises(ValueError):
        await inference_module.generate_explanation("topic", "nope", model="m1")


@pytest.mark.asyncio
async def test_generate_stream_explanation_regenerate_appends_quote(monkeypatch):
    class DummyProvider:
        async def route_inference_stream(self, _prompt, **_kwargs):
            yield "hello"

    async def fake_quote():
        return "---\n*\"Quote\"*"

    monkeypatch.setattr(model_provider.ModelProvider, "get_instance", classmethod(lambda cls: DummyProvider()))
    monkeypatch.setattr(inference_module.search_service, "get_regeneration_quote", fake_quote)

    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "topic",
        "eli5",
        mode="fast",
        regenerate=True
    ):
        chunks.append(chunk)

    assert "hello" in "".join(chunks)
    assert "Quote" in "".join(chunks)
