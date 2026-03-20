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


@pytest.mark.asyncio
async def test_generate_explanation_socratic_limits_questions(monkeypatch):
    async def fake_call_model(*_args, **_kwargs):
        return (
            "What is energy?\n"
            "How does energy move in this system?\n"
            "How does energy move in this system?\n"
            "Why does that transfer matter?\n"
            "How would you measure it?"
        )

    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    result = await inference_module.generate_explanation(
        "energy",
        "eli15",
        mode="socratic",
    )

    assert result.count("?") <= 3
    assert "How would you measure it?" not in result
    assert "Share your answer, and I will guide the next step." in result


@pytest.mark.asyncio
async def test_generate_stream_explanation_socratic_limits_questions(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        chunks = [
            "What is entropy? ",
            "How does it change in this process? ",
            "How does it change in this process? ",
            "Why is that useful in engineering?",
        ]
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream)

    streamed = []
    async for chunk in inference_module.generate_stream_explanation(
        "entropy",
        "eli15",
        mode="socratic",
    ):
        streamed.append(chunk)

    combined = "".join(streamed)
    assert combined.count("?") <= 3
    assert "Share your answer, and I will guide the next step." in combined
