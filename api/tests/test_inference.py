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


@pytest.mark.asyncio
async def test_technical_mode_handler_uses_safe_defaults_when_classification_fails(monkeypatch):
    captured = {}

    def fake_detect_intent_and_depth(_topic: str):
        raise RuntimeError("classification failed")

    def fake_build_technical_prompt(topic: str, intent: str, depth: str, diagram_type: str | None) -> str:
        captured["build_args"] = (topic, intent, depth, diagram_type)
        return "safe prompt"

    async def fake_call_model(*_args, **_kwargs):
        return "valid technical response"

    monkeypatch.setattr(inference_module, "detect_intent_and_depth", fake_detect_intent_and_depth)
    monkeypatch.setattr(inference_module, "build_technical_prompt", fake_build_technical_prompt)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)
    monkeypatch.setattr(inference_module, "validate_technical_response", lambda *_args, **_kwargs: (True, None))

    result = await inference_module.technical_mode_handler("topic")

    assert result == "valid technical response"
    assert captured["build_args"] == ("topic", "unknown", "shallow", "generic")


@pytest.mark.asyncio
async def test_technical_mode_handler_uses_minimal_prompt_when_prompt_builder_empty(monkeypatch):
    captured = {}

    monkeypatch.setattr(inference_module, "detect_intent_and_depth", lambda _topic: {"intent": "explain", "depth": "deep"})
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "   ")

    async def fake_call_model(_model_alias: str, prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return "valid technical response"

    monkeypatch.setattr(inference_module, "call_model", fake_call_model)
    monkeypatch.setattr(inference_module, "validate_technical_response", lambda *_args, **_kwargs: (True, None))

    result = await inference_module.technical_mode_handler("topic")

    assert result == "valid technical response"
    assert captured["prompt"] == inference_module.TECHNICAL_MINIMAL_PROMPT


@pytest.mark.asyncio
async def test_generate_stream_explanation_technical_streams_via_llm_stream(monkeypatch):
    async def fake_stream_chat_completion(*_args, **_kwargs):
        yield "chunk-a"
        yield "chunk-b"

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("technical_mode_handler should not be used for primary stream path")

    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(inference_module, "technical_mode_handler", fail_if_called)
    monkeypatch.setattr(
        inference_module,
        "detect_intent_and_depth",
        lambda _topic: {"intent": "explain", "depth": "shallow"},
    )
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "prompt")

    streamed = []
    async for chunk in inference_module.generate_stream_explanation(
        "topic",
        "eli15",
        mode="technical",
    ):
        streamed.append(chunk)

    assert streamed == ["chunk-a", "chunk-b"]
