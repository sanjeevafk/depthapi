import pytest

import services.inference as inference_module
import services.llm_client as llm_client


@pytest.mark.asyncio
async def test_generate_explanation_unknown_level():
    with pytest.raises(ValueError):
        await inference_module.generate_explanation("topic", "nope", model="m1")


@pytest.mark.asyncio
async def test_generate_stream_explanation_passes_temperature(monkeypatch):
    captured = {}

    async def fake_stream(*_args, **kwargs):
        captured["temperature"] = kwargs.get("temperature")
        yield "hello"

    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream)

    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "topic",
        "eli5",
        mode="learning",
        regenerate=True,
        temperature=0.8,
    ):
        chunks.append(chunk)

    assert "hello" in "".join(chunks)
    assert captured["temperature"] == 0.8
