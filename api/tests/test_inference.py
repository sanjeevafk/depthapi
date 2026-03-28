import httpx
import pytest
from openai import APIStatusError
from types import SimpleNamespace

import services.inference as inference_module
import services.llm_client as llm_client


@pytest.mark.asyncio
async def test_generate_explanation_unknown_level():
    with pytest.raises(ValueError):
        await inference_module.generate_explanation("topic", "nope", model="m1")


@pytest.mark.asyncio
async def test_generate_explanation_learning_injects_search_context(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_search_context(_topic: str):
        return "search context for learning"

    async def fake_call_model(_model, prompt, **_kwargs):
        captured["prompt"] = prompt
        return "This is a detailed explanation.\nIt includes context and examples.\nIt remains clear for learners."

    monkeypatch.setattr(inference_module.search_service, "get_search_context", fake_search_context)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    result = await inference_module.generate_explanation("dns caching", "eli5", mode="learning")
    assert "detailed explanation" in result
    assert "External web context" in captured["prompt"]
    assert "search context for learning" in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_explanation_socratic_injects_search_context(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_search_context(_topic: str):
        return "search context for socratic"

    async def fake_call_model(_model, prompt, **_kwargs):
        captured["prompt"] = prompt
        return "What is DNS?"

    monkeypatch.setattr(inference_module.search_service, "get_search_context", fake_search_context)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    result = await inference_module.generate_explanation("dns", "eli15", mode="socratic")
    assert "What is DNS?" in result
    assert "External web context" in captured["prompt"]
    assert "search context for socratic" in captured["prompt"]


@pytest.mark.asyncio
async def test_technical_mode_handler_injects_search_context(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_search_context(_topic: str):
        return "search context for technical"

    async def fake_call_model(_model, prompt, **_kwargs):
        captured["prompt"] = prompt
        return "valid technical response"

    monkeypatch.setattr(inference_module.search_service, "get_search_context", fake_search_context)
    monkeypatch.setattr(
        inference_module,
        "detect_intent_and_depth",
        lambda _topic: {"intent": "explain", "depth": "medium"},
    )
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "technical base prompt")
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)
    monkeypatch.setattr(inference_module, "validate_technical_response", lambda *_args, **_kwargs: (True, None))

    result = await inference_module.technical_mode_handler("raft consensus")
    assert result == "valid technical response"
    assert "External web context" in captured["prompt"]
    assert "search context for technical" in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_explanation_search_failure_is_fail_soft(monkeypatch):
    calls = {"count": 0}

    async def broken_search(_topic: str):
        raise RuntimeError("search backend down")

    async def fake_call_model(_model, _prompt, **_kwargs):
        calls["count"] += 1
        return (
            "This answer remains useful without external search and still teaches core concepts clearly.\n"
            "It explains what changed in the request path, why the fallback remains safe, and how learners should interpret results.\n"
            "It also provides practical framing and enough detail to be considered complete for an instructional response."
        )

    monkeypatch.setattr(inference_module.search_service, "get_search_context", broken_search)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    result = await inference_module.generate_explanation("tcp", "eli10", mode="learning")
    assert "without external search" in result
    assert calls["count"] == 1


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
    assert combined.count("How does it change in this process?") == 1
    assert "Share your answer, and I will guide the next step." in combined


@pytest.mark.asyncio
async def test_generate_stream_explanation_socratic_dedupes_and_caps_questions(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        chunks = [
            "What is entropy? ",
            "How does entropy change here? ",
            "How does entropy change here? ",
            "Why does entropy matter? ",
            "How would you measure entropy in practice?",
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
    assert combined.count("?") == 3
    assert combined.count("How does entropy change here?") == 1
    assert "How would you measure entropy in practice?" not in combined
    assert combined.endswith("Share your answer, and I will guide the next step.")


@pytest.mark.asyncio
async def test_generate_stream_explanation_socratic_streams_questions_progressively(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield "What is entropy? "
        yield "How does entropy change in this process? "
        yield "How does entropy change in this process? "

    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream)

    streamed = []
    async for chunk in inference_module.generate_stream_explanation(
        "entropy",
        "eli15",
        mode="socratic",
    ):
        streamed.append(chunk)

    assert streamed[0] == "What is entropy?"
    assert any("How does entropy change in this process?" in chunk for chunk in streamed)
    assert sum("How does entropy change in this process?" in chunk for chunk in streamed) == 1
    assert streamed[-1] == "\n\nShare your answer, and I will guide the next step."


@pytest.mark.asyncio
async def test_generate_stream_explanation_socratic_stream_failure_falls_back(monkeypatch):
    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream broke")
        yield ""  # pragma: no cover

    monkeypatch.setattr(inference_module, "stream_chat_completion", crashing_stream)

    telemetry_sink: dict[str, object] = {}
    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "entropy",
        "eli15",
        mode="socratic",
        telemetry_sink=telemetry_sink,
        request_id="req-socratic-fail",
    ):
        chunks.append(chunk)

    combined = "".join(chunks)
    assert "temporary issue while streaming" in combined
    assert telemetry_sink.get("stream_error_type") == "RuntimeError"
    assert telemetry_sink.get("request_id") == "req-socratic-fail"


@pytest.mark.asyncio
async def test_generate_stream_explanation_learning_stream_failure_is_graceful(monkeypatch):
    async def crashing_stream(*_args, **_kwargs):
        raise RuntimeError("stream broke")
        yield ""  # pragma: no cover

    monkeypatch.setattr(inference_module, "stream_chat_completion", crashing_stream)

    telemetry_sink: dict[str, object] = {}
    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "dns",
        "eli5",
        mode="learning",
        telemetry_sink=telemetry_sink,
        request_id="req-learning-fail",
    ):
        chunks.append(chunk)

    combined = "".join(chunks)
    assert "Unable to stream a response right now" in combined
    assert telemetry_sink.get("stream_error_type") == "RuntimeError"
    assert telemetry_sink.get("request_id") == "req-learning-fail"


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


@pytest.mark.asyncio
async def test_generate_stream_explanation_technical_does_not_duplicate_request_id(monkeypatch):
    async def fake_stream_chat_completion(*_args, request_id=None, **kwargs):
        assert request_id == "req-123"
        assert "request_id" not in kwargs
        assert kwargs.get("temperature") == inference_module.TECHNICAL_TEMPERATURE
        yield "ok"

    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(
        inference_module,
        "detect_intent_and_depth",
        lambda _topic: {"intent": "explain", "depth": "shallow"},
    )
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "prompt")
    async def fake_search_context(*_args, **_kwargs):
        return ""
    monkeypatch.setattr(inference_module, "_load_search_context", fake_search_context)
    monkeypatch.setattr(
        inference_module,
        "_technical_route",
        lambda *_args, **_kwargs: ("technical-gemini-pro", "technical-groq-llama8b"),
    )

    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "topic",
        "eli15",
        mode="technical",
        request_id="req-123",
        temperature=0.7,
    ):
        chunks.append(chunk)

    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_technical_mode_handler_returns_best_effort_when_validation_fails(monkeypatch):
    async def fake_call_model(*_args, **_kwargs):
        return "This is useful but does not match strict markdown sections."

    monkeypatch.setattr(inference_module, "call_model", fake_call_model)
    monkeypatch.setattr(
        inference_module,
        "validate_technical_response",
        lambda *_args, **_kwargs: (False, "missing_structure"),
    )
    monkeypatch.setattr(inference_module, "detect_intent_and_depth", lambda _topic: {"intent": "explain", "depth": "medium"})
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "prompt")

    result = await inference_module.technical_mode_handler("topic")

    assert "Unable to generate a response at this time" not in result
    assert result.endswith(".")


@pytest.mark.asyncio
async def test_technical_mode_handler_accepts_structured_response_without_terminal_punctuation(monkeypatch):
    response = (
        "## Core Idea\nA reliable overview of retrieval behavior across providers\n\n"
        "## First Principles Breakdown\nRequests are routed by intent and depth with bounded fallback strategy\n\n"
        "## Intuition\nTreat the system as layered safety checks around model selection and output quality\n\n"
        "## Edge Cases / Limitations\nProvider key drift, timeout windows, and partial stream failures require guarded fallback\n\n"
        "## Connections\nThis links request controls, routing state, and response validation across the stack"
    )

    async def fake_call_model(*_args, **_kwargs):
        return response

    monkeypatch.setattr(inference_module, "call_model", fake_call_model)
    monkeypatch.setattr(
        inference_module,
        "detect_intent_and_depth",
        lambda _topic: {"intent": "explain", "depth": "medium"},
    )
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "prompt")

    result = await inference_module.technical_mode_handler("topic")
    assert result == response
    assert not result.endswith(".")


@pytest.mark.asyncio
async def test_generate_stream_explanation_technical_partial_stream_failure_is_graceful(monkeypatch):
    async def partial_then_fail(*_args, **_kwargs):
        yield "partial"
        raise RuntimeError("stream broke")

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("technical_mode_handler should not run when partial stream already exists")

    monkeypatch.setattr(inference_module, "stream_chat_completion", partial_then_fail)
    monkeypatch.setattr(inference_module, "technical_mode_handler", fail_if_called)
    monkeypatch.setattr(
        inference_module,
        "detect_intent_and_depth",
        lambda _topic: {"intent": "explain", "depth": "medium"},
    )
    monkeypatch.setattr(inference_module, "detect_diagram_type", lambda _topic: None)
    monkeypatch.setattr(inference_module, "build_technical_prompt", lambda *_args, **_kwargs: "prompt")

    telemetry_sink: dict[str, object] = {}
    chunks = []
    async for chunk in inference_module.generate_stream_explanation(
        "topic",
        "eli15",
        mode="technical",
        telemetry_sink=telemetry_sink,
    ):
        chunks.append(chunk)

    assert chunks[0] == "partial"
    assert any("service interruption" in chunk for chunk in chunks[1:])
    assert telemetry_sink.get("stream_completed") is False
    assert telemetry_sink.get("partial_failure") is True


def test_weighted_routing_prefers_technical_model_for_complex_queries():
    ranked = inference_module.route_model_aliases(
        "Compare distributed consensus tradeoffs and derive correctness guarantees.",
        mode="technical",
        level="eli15",
    )
    assert ranked[0] == "technical-gemini-pro"


def test_weighted_routing_prefers_fast_model_for_latency_queries():
    ranked = inference_module.route_model_aliases(
        "Give me a quick brief summary of DNS.",
        mode="learning",
        level="eli5",
    )
    assert ranked[0] in {"learn-groq-llama8b", "learn-gemini-flash"}


def test_is_transient_http_error_retries_on_connect_and_timeout():
    connect_exc = httpx.ConnectError("connect failed")
    timeout_exc = httpx.TimeoutException("timed out")

    assert inference_module.is_transient_http_error(connect_exc) is True
    assert inference_module.is_transient_http_error(timeout_exc) is True


def test_is_transient_http_error_retries_on_5xx_only():
    request = httpx.Request("GET", "https://example.com")
    response_503 = httpx.Response(503, request=request)
    response_400 = httpx.Response(400, request=request)
    exc_503 = httpx.HTTPStatusError("server error", request=request, response=response_503)
    exc_400 = httpx.HTTPStatusError("client error", request=request, response=response_400)

    assert inference_module.is_transient_http_error(exc_503) is True
    assert inference_module.is_transient_http_error(exc_400) is False


def test_is_transient_http_error_retries_on_openai_retryable_statuses():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response_429 = httpx.Response(429, request=request)
    response_400 = httpx.Response(400, request=request)
    exc_429 = APIStatusError("rate limited", response=response_429, body={"error": "rate_limited"})
    exc_400 = APIStatusError("bad request", response=response_400, body={"error": "bad_request"})

    assert inference_module.is_transient_http_error(exc_429) is True
    assert inference_module.is_transient_http_error(exc_400) is False


@pytest.mark.asyncio
async def test_call_model_retries_on_openai_retryable_status(monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    retryable_exc = APIStatusError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body={"error": "rate_limited"},
    )
    attempts = {"count": 0}

    async def flaky_completion(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise retryable_exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
            model="default-fast",
        )

    monkeypatch.setattr(inference_module, "create_chat_completion", flaky_completion)

    result = await inference_module.call_model("default-fast", "hello", max_tokens=16)
    assert result == "ok"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_learning_quality_retry_uses_next_alias_once(monkeypatch):
    calls: list[str] = []

    async def fake_search_context(_topic: str):
        return ""

    async def fake_call_model(model_alias, _prompt, **_kwargs):
        calls.append(model_alias)
        if len(calls) == 1:
            return "not sure"
        return "Detailed response.\nWith useful structure.\nWith actionable clarity."

    monkeypatch.setattr(inference_module.search_service, "get_search_context", fake_search_context)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    result = await inference_module.generate_explanation(
        "explain dns",
        "eli10",
        mode="learning",
    )

    assert "Detailed response." in result
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_call_model_does_not_retry_on_openai_non_retryable_4xx(monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    non_retryable_exc = APIStatusError(
        "bad request",
        response=httpx.Response(400, request=request),
        body={"error": "bad_request"},
    )
    attempts = {"count": 0}

    async def always_bad_request(*_args, **_kwargs):
        attempts["count"] += 1
        raise non_retryable_exc

    monkeypatch.setattr(inference_module, "create_chat_completion", always_bad_request)

    with pytest.raises(APIStatusError):
        await inference_module.call_model("default-fast", "hello", max_tokens=16)

    assert attempts["count"] == 1
