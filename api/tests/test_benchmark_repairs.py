from types import SimpleNamespace

import pytest

import api.services.rag.rag_backend_router as rag_backend_router
import api.services.inference.llm_intent_classifier as classifier_module
from api.services.inference.llm_errors import LLMBadRequest
from api.services.inference.inference_technical import (
    call_with_quality_escalation,
    technical_mode_handler,
)


@pytest.mark.asyncio
async def test_rag_backend_auto_prefers_pgvector_when_supabase_is_configured(monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    monkeypatch.setattr(
        rag_backend_router,
        "get_settings",
        lambda: SimpleNamespace(supabase_url="https://example.supabase.co", supabase_secret_key="secret"),
    )
    sentinel = object()
    monkeypatch.setattr(rag_backend_router, "get_retrieval_service", lambda: sentinel)
    rag_backend_router._fs_store = None

    backend = rag_backend_router.get_rag_backend()

    assert backend is sentinel


@pytest.mark.asyncio
async def test_llm_intent_classifier_uses_live_client_path_for_ambiguous_query(monkeypatch):
    monkeypatch.setattr(
        classifier_module,
        "_regex_classify",
        lambda _query: classifier_module.IntentResult(
            task="explain",
            depth="accessible",
            reasoning="direct",
            style="normal",
            capabilities=[],
            source="regex",
            confidence=0.0,
        ),
    )

    async def fake_cache_get(_key):
        return None

    writes = []

    async def fake_cache_set(key, value, ttl=None):
        writes.append((key, value, ttl))
        return True

    async def fake_create_chat_completion(*, model, messages, **kwargs):
        assert model == "learn-groq-llama8b"
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"task":"brainstorm","depth":"technical","reasoning":"guided","style":"academic","capabilities":["requires_search"]}'
                    )
                )
            ]
        )

    monkeypatch.setattr(classifier_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(classifier_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(classifier_module, "create_chat_completion", fake_create_chat_completion)

    result = await classifier_module.classify_intent("What architecture ideas should we consider here?")

    assert result.source == "llm"
    assert result.task == "brainstorm"
    assert result.depth == "technical"
    assert result.reasoning == "guided"
    assert result.style == "academic"
    assert result.capabilities == ["requires_search"]
    assert writes


@pytest.mark.asyncio
async def test_llm_intent_classifier_retries_without_response_format_on_bad_request(monkeypatch):
    monkeypatch.setattr(
        classifier_module,
        "_regex_classify",
        lambda _query: classifier_module.IntentResult(
            task="explain",
            depth="accessible",
            reasoning="direct",
            style="normal",
            capabilities=[],
            source="regex",
            confidence=0.0,
        ),
    )

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    calls = []

    async def fake_create_chat_completion(*, model, messages, **kwargs):
        calls.append(kwargs.copy())
        assert model == "learn-groq-llama8b"
        if kwargs.get("response_format") == {"type": "json_object"}:
            raise LLMBadRequest("Provider groq rejected the request payload.")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"task":"brainstorm","depth":"technical","reasoning":"guided","style":"academic","capabilities":["requires_search"]}'
                    )
                )
            ]
        )

    monkeypatch.setattr(classifier_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(classifier_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(classifier_module, "create_chat_completion", fake_create_chat_completion)

    result = await classifier_module.classify_intent("unclear")

    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert result.source == "llm"
    assert result.task == "brainstorm"


@pytest.mark.asyncio
async def test_llm_intent_classifier_ignores_cached_degraded_llm_result(monkeypatch):
    degraded = classifier_module.IntentResult(
        task="explain",
        depth="accessible",
        reasoning="direct",
        style="normal",
        capabilities=[],
        source="llm",
        confidence=0.5,
    ).to_dict()

    async def fake_cache_get(_key):
        return degraded

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    async def fake_create_chat_completion(*, model, messages, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"task":"analyze","depth":"technical","reasoning":"guided","style":"academic","capabilities":["requires_context"]}'
                    )
                )
            ]
        )

    monkeypatch.setattr(
        classifier_module,
        "_regex_classify",
        lambda _query: classifier_module.IntentResult(
            task="explain",
            depth="accessible",
            reasoning="direct",
            style="normal",
            capabilities=[],
            source="regex",
            confidence=0.0,
        ),
    )
    monkeypatch.setattr(classifier_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(classifier_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(classifier_module, "create_chat_completion", fake_create_chat_completion)

    result = await classifier_module.classify_intent("unclear again")

    assert result.source == "llm"
    assert result.confidence == 0.9
    assert result.task == "analyze"


@pytest.mark.asyncio
async def test_call_with_quality_escalation_respects_single_explicit_alias():
    calls = []

    async def fake_call_model(alias, prompt, max_tokens, **kwargs):
        calls.append((alias, prompt, max_tokens, kwargs))
        return "ok"

    def fake_effective_alias_chain(_aliases, *, complexity):
        assert complexity < 0.8
        return []

    result = await call_with_quality_escalation(
        ["cerebras/zai-glm-4.7"],
        "prompt",
        complexity=0.2,
        max_tokens=64,
        call_model_fn=fake_call_model,
        effective_alias_chain_fn=fake_effective_alias_chain,
    )

    assert result == "ok"
    assert calls[0][0] == "cerebras/zai-glm-4.7"


@pytest.mark.asyncio
async def test_technical_mode_handler_respects_explicit_model(monkeypatch):
    calls = []

    async def fake_load_search_context(_topic, **_kwargs):
        return ""

    async def fake_call_model(model_alias, _prompt, **_kwargs):
        calls.append(model_alias)
        return (
            "## Core Idea\nSomething.\n\n"
            "## First Principles Breakdown\nDetail.\n\n"
            "## Intuition\nAnalogy.\n\n"
            "## Edge Cases / Limitations\nLimits.\n\n"
            "## Connections\nMore."
        )

    def fake_validate(response, _intent):
        return True, ""

    result = await technical_mode_handler(
        "Explain consistency models",
        model="cerebras/zai-glm-4.7",
        detect_intent_and_depth_fn=lambda _query: {"task": "explain", "depth": "technical"},
        detect_diagram_type_fn=lambda _query: None,
        validate_technical_response_fn=fake_validate,
        load_search_context_fn=fake_load_search_context,
        route_aliases_fn=lambda *_args, **_kwargs: ["technical-groq-llama8b", "technical-gemini-pro"],
        call_model_fn=fake_call_model,
    )

    assert calls == ["cerebras/zai-glm-4.7"]
    assert "## Core Idea" in result
