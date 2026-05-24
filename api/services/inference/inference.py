"""Core inference orchestration for chat and query responses."""

from __future__ import annotations

import inspect
import os
import time
from typing import Any, cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletionMessageParam
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from api.config import get_settings
from api.logging_config import anonymize_user_id, logger, log_sampled_success
from api.prompts import build_prompt, build_prompt_with_trace
from api.prompt_engine import PromptSpec
from api.services.inference.inference_classifier import IntentClassifier
from api.services.inference.inference_message_builder import (
    build_messages,
    is_comparison_query,
    trim_history_for_cost,
)
from api.services.inference.inference_routing import (
    _effective_alias_chain,
    _technical_route,
    extract_features,
    route_model_aliases,
)
from api.services.inference.inference_search import (
    _append_rag_context,
    _append_search_context,
    _load_search_context,
    _truncate_search_context,
    format_rag_context,
)
from api.services.rag.rag_backend_router import retrieve_context as retrieve_rag_context
from api.services.inference.inference_socratic import (
    _enforce_socratic_response_constraints,
    _normalize_question_signature,
    _wants_direct_answer,
)
from api.services.inference.inference_prompting import (
    _append_cue_if_fits,
    _compress_sentence,
    _normalize_whitespace,
    _word_count,
)
from api.services.inference.inference_streaming import generate_stream_explanation as generate_stream_explanation_impl
from api.services.inference.inference_technical import (
    build_technical_prompt as build_technical_prompt_impl,
    call_with_quality_escalation,
    is_low_quality as is_low_quality_impl,
    technical_mode_handler as technical_mode_handler_impl,
)
from api.services.inference.inference_constants import TECHNICAL_MINIMAL_PROMPT, TECHNICAL_TEMPERATURE
from api.services.conversation.intent import (
    detect_diagram_type as detect_diagram_type_base,
    detect_intent_and_depth as detect_intent_and_depth_base,
    validate_technical_response,
)
from api.services.inference.llm_client import close_llm_client, create_chat_completion, stream_chat_completion
from api.services.inference.model_router import ModelRouter
from api.services.inference.prompt_orchestrator import PromptOrchestrator
from api.services.messaging.response_builder import ResponseBuilder
from api.services.infra.utils_shared import (
    extract_estimated_cost as extract_shared_estimated_cost,
    extract_usage_dict as extract_shared_usage_dict,
)
from api.utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode

_intent_classifier = IntentClassifier()
_model_router = ModelRouter()
_prompt_orchestrator = PromptOrchestrator()
_response_builder = ResponseBuilder()
VALID_PROMPT_DEPTHS = {"simple", "accessible", "technical", "expert"}


class _SearchServiceShim:
    async def get_search_context(self, topic: str, mode: str) -> str:
        return await _load_search_context(topic, mode=mode)

    async def load_search_context(self, topic: str, *, mode: str) -> str:
        default_impl = getattr(self.get_search_context, "__func__", None) is _SearchServiceShim.get_search_context
        if default_impl:
            return await _load_search_context(topic, mode=mode)

        get_context = cast(Any, self.get_search_context)
        supports_mode = False
        try:
            signature = inspect.signature(get_context)
            supports_mode = any(
                param.kind is inspect.Parameter.VAR_KEYWORD or param.name == "mode"
                for param in signature.parameters.values()
            )
        except (TypeError, ValueError):
            supports_mode = True

        try:
            if supports_mode:
                context = await get_context(topic, mode=mode)
            else:
                context = await get_context(topic)
        except Exception as exc:
            logger.debug("search_context_load_failed", mode=mode, error=str(exc))
            return ""
        return _truncate_search_context(str(context or ""))


search_service = _SearchServiceShim()



async def _classify_intent(query: str) -> dict[str, Any]:
    """Async-first prompt-axis classification. Falls back to regex on error."""
    try:
        return await _intent_classifier.classify_async(query)
    except Exception:
        return detect_intent_and_depth_base(query)


async def _classify_prompt_spec(query: str) -> PromptSpec:
    try:
        return await _intent_classifier.classify_prompt_spec(query)
    except Exception:
        fallback = detect_intent_and_depth_base(query)
        return PromptSpec(
            topic=query,
            depth=_canonical_depth(str(fallback.get("depth", "accessible"))),
            task=str(fallback.get("intent", "explain")),
            reasoning="direct",
            style="normal",
        )


def detect_intent_and_depth(query: str) -> dict[str, str]:
    """Sync shim kept for call sites that cannot be made async yet."""
    try:
        return _intent_classifier.detect_intent_and_depth(query)
    except Exception:
        return detect_intent_and_depth_base(query)


def _canonical_depth(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    mapping = {
        "shallow": "simple",
        "medium": "accessible",
        "deep": "technical",
        "meme": "accessible",
    }
    normalized = mapping.get(normalized, normalized)
    return normalized if normalized in {"simple", "accessible", "technical", "expert"} else "accessible"


def _spec_for_request(
    topic: str,
    classified_spec: PromptSpec,
    *,
    level: str | None,
    mode: str,
    search_context: str = "",
    diagram_type: str | None = None,
) -> tuple[PromptSpec, str | None]:
    depth = _canonical_depth(level) if level else classified_spec.depth
    style = classified_spec.style
    if (level or "").strip().lower() == "meme":
        style = "meme"
    reasoning = "socratic" if mode == SOCRATIC_MODE else classified_spec.reasoning
    capabilities = set(classified_spec.capabilities)
    if search_context:
        capabilities.discard("requires_search")
        capabilities.discard("requires_citations")
    if diagram_type is None:
        capabilities.discard("requires_diagram")
    return (
        PromptSpec(
            topic=topic,
            depth=depth,
            task=classified_spec.task,
            reasoning=reasoning,
            style=style,
            capabilities=frozenset(capabilities),
        ),
        diagram_type,
    )


def detect_diagram_type(query: str) -> str | None:
    try:
        return _intent_classifier.detect_diagram_type(query)
    except Exception:
        return detect_diagram_type_base(query)


def is_low_quality(response: str) -> bool:
    return is_low_quality_impl(response)


async def _call_with_quality_escalation(
    aliases: list[str],
    prompt: str,
    *,
    complexity: float,
    max_tokens: int = 300,
    **kwargs: Any,
) -> str:
    return await call_with_quality_escalation(
        aliases,
        prompt,
        complexity=complexity,
        max_tokens=max_tokens,
        call_model_fn=call_model,
        effective_alias_chain_fn=_effective_alias_chain,
        **kwargs,
    )


def build_technical_prompt(topic: str, intent: str, depth: str, diagram_type: str | None) -> str:
    return build_technical_prompt_impl(topic, intent, depth, diagram_type)


async def technical_mode_handler(topic: str, **kwargs: Any) -> str:
    # 1. RAG Retrieval
    rag_context = ""
    try:
        rag_results = await retrieve_rag_context(
            query=topic,
            api_key_id=str(kwargs.get("user_id") or "anonymous"),
            limit=int(os.getenv("RAG_TOP_K", "5")),
            collection_id=kwargs.get("collection_id"),
            use_trusted_corpus=kwargs.get("use_trusted_corpus", True),
            query_mode="technical",
        )
        rag_context = format_rag_context(rag_results)
        if isinstance(kwargs.get("telemetry_sink"), dict):
            kwargs["telemetry_sink"]["retrieved_contexts"] = rag_results
    except Exception as exc:
        logger.error(f"technical_mode_rag_failed: {str(exc)}", request_id=kwargs.get("request_id"))

    return await technical_mode_handler_impl(
        topic,
        build_technical_prompt_fn=build_technical_prompt,
        detect_intent_and_depth_fn=detect_intent_and_depth,
        detect_diagram_type_fn=detect_diagram_type,
        validate_technical_response_fn=validate_technical_response,
        load_search_context_fn=search_service.load_search_context,
        route_aliases_fn=_model_router.route_aliases,
        call_model_fn=call_model,
        _rag_context=rag_context,
        **kwargs,
    )


async def close_client() -> None:
    await close_llm_client()


def _extract_usage_dict(usage_obj: object) -> dict[str, int] | None:
    return extract_shared_usage_dict(usage_obj)


def _extract_estimated_cost(result: object, usage: dict[str, int] | None) -> float | None:
    return extract_shared_estimated_cost(result, usage)


def _is_comparison_query(text: str) -> bool:
    return is_comparison_query(text)


def trimHistoryForCost(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    return trim_history_for_cost(history)


def _build_messages(
    prompt: str,
    *,
    conversation_messages: list[dict[str, str]] | None = None,
    intent_system_prompt: str | None = None,
    mode: str | None = None,
) -> list[dict[str, str]]:
    return build_messages(
        prompt,
        conversation_messages=conversation_messages,
        intent_system_prompt=intent_system_prompt,
        mode=mode,
    )


def is_transient_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(is_transient_http_error),
    reraise=True,
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 300, **kwargs: Any) -> str:
    try:
        alias = model or "default-fast"
        request_id = kwargs.get("request_id")
        retry_flag = bool(kwargs.get("regenerate", False))
        anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
        telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
        model_start = time.perf_counter()
        messages = _build_messages(
            prompt,
            conversation_messages=kwargs.get("conversation_messages"),
            intent_system_prompt=kwargs.get("intent_system_prompt"),
            mode=kwargs.get("mode"),
        )
        result = await create_chat_completion(
            model=alias,
            messages=cast(list[ChatCompletionMessageParam], messages),
            max_tokens=max_tokens,
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
            telemetry_sink=telemetry_sink,
        )
        model_inference_ms = round((time.perf_counter() - model_start) * 1000, 2)
        usage = _extract_usage_dict(getattr(result, "usage", None))
        estimated_cost_usd = _extract_estimated_cost(result, usage)
        model_name = getattr(result, "model", None)
        if telemetry_sink is not None:
            telemetry_sink["token_usage"] = usage
            telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
            telemetry_sink["model_inference_ms"] = model_inference_ms
            telemetry_sink["model_alias"] = alias

        log_sampled_success(
            "llm_completion_observed",
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            model_alias=alias,
            model=model_name,
            latency_ms=model_inference_ms,
            token_usage=usage,
            estimated_cost_usd=estimated_cost_usd,
            retry=retry_flag,
            sampled=True,
        )
        if not result.choices:
            raise RuntimeError("LLM response missing choices.")
        return result.choices[0].message.content or ""
    except Exception as e:
        logger.error(
            "inference_failed",
            error=str(e),
            model_alias=model or "default-fast",
            request_id=kwargs.get("request_id"),
            user_id_hash=anonymize_user_id(str(kwargs.get("user_id") or "") or None),
            retry=bool(kwargs.get("regenerate", False)),
            sampled=False,
        )
        raise


async def generate_explanation(topic: str, level: str, model: str | None = None, **kwargs: Any) -> str:
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    explicit_prompt_spec = kwargs.get("prompt_spec")
    if explicit_prompt_spec is not None and not isinstance(explicit_prompt_spec, PromptSpec):
        explicit_prompt_spec = None
    if (level or "").strip().lower() not in VALID_PROMPT_DEPTHS:
        raise ValueError(f"Unknown prompt depth '{level}'. Valid depths: {sorted(VALID_PROMPT_DEPTHS)}")

    if mode == TECHNICAL_MODE:
        return await technical_mode_handler(topic, **kwargs)

    # --- Classify once into canonical prompt axes; thread scalar task/depth
    # fields where routing still expects them.
    classified_spec = explicit_prompt_spec or await _classify_prompt_spec(topic)
    intent = classified_spec.task
    depth = classified_spec.depth

    if mode == SOCRATIC_MODE:
        search_context = await search_service.load_search_context(topic, mode=SOCRATIC_MODE)
        prompt_spec, _ = _spec_for_request(
            topic,
            classified_spec,
            level=level,
            mode=SOCRATIC_MODE,
            search_context=search_context,
        )
        prompt_build = build_prompt_with_trace(
            prompt_spec,
            conversation_context=kwargs.get("conversation_context", ""),
            search_context=search_context,
        )
        prompt = prompt_build.prompt
        if isinstance(kwargs.get("telemetry_sink"), dict):
            kwargs["telemetry_sink"]["prompt_trace"] = prompt_build.trace.to_dict()
        routed_aliases = _model_router.route_aliases(
            topic,
            intent=intent,
            mode=mode,
            level=level,
            depth=depth,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        socratic_complexity = float(
            extract_features(topic, mode=mode, level=level, intent=intent, depth=depth).get("complexity", 0.0) or 0.0
        )
        max_tokens = int(getattr(get_settings(), "max_output_tokens_socratic", 1024))
        response = await _call_with_quality_escalation(
            [model] if model else routed_aliases,
            prompt,
            complexity=socratic_complexity,
            max_tokens=max_tokens,
            **kwargs,
        )
        if _wants_direct_answer(topic):
            return _enforce_socratic_response_constraints(response, topic=topic, wants_direct_answer=True)
        return _response_builder.apply_socratic_fallback(topic, response)

    # --- Learn mode ---
    # 1. RAG Retrieval
    rag_context = ""
    try:
        rag_results = await retrieve_rag_context(
            query=topic,
            api_key_id=str(kwargs.get("user_id") or "anonymous"),
            limit=int(os.getenv("RAG_TOP_K", "5")),
            collection_id=kwargs.get("collection_id"),
            use_trusted_corpus=kwargs.get("use_trusted_corpus", True),
            query_mode="conceptual",
        )
        rag_context = format_rag_context(rag_results)
        if isinstance(kwargs.get("telemetry_sink"), dict):
            kwargs["telemetry_sink"]["retrieved_contexts"] = rag_results
    except Exception as exc:
        logger.error(f"learn_mode_rag_failed: {str(exc)}", request_id=kwargs.get("request_id"))
    
    # 2. Web Search
    search_context = await search_service.load_search_context(topic, mode=LEARNING_MODE)
    
    # 3. Assemble Prompt
    combined_context = "\n\n".join(part for part in (rag_context, search_context) if part)
    prompt_spec, _ = _spec_for_request(
        topic,
        classified_spec,
        level=level,
        mode=LEARNING_MODE,
        search_context=combined_context,
    )
    prompt_build = build_prompt_with_trace(prompt_spec, search_context=combined_context)
    prompt = prompt_build.prompt
    if isinstance(kwargs.get("telemetry_sink"), dict):
        kwargs["telemetry_sink"]["prompt_trace"] = prompt_build.trace.to_dict()
    length_constraint = _prompt_orchestrator.extract_length_constraint(topic)
    prompt = _prompt_orchestrator.apply_length_constraints(prompt, length_constraint)
    is_large_input = _prompt_orchestrator.is_large_input(topic)
    learn_cap, learn_cue = _prompt_orchestrator.learning_length_policy(topic)

    routed_aliases = _model_router.route_aliases(
        topic,
        intent=intent,
        depth=depth,
        mode=mode,
        level=level,
        is_pro=bool(kwargs.get("is_pro", False)),
        search_api_used=bool(search_context),
    )
    learning_complexity = float(
        extract_features(topic, mode=mode, level=level, intent=intent, depth=depth).get("complexity", 0.0) or 0.0
    )
    max_tokens = int(getattr(get_settings(), "max_output_tokens_learning", 1024))
    response = await _call_with_quality_escalation(
        [model] if model else routed_aliases,
        prompt,
        complexity=learning_complexity,
        max_tokens=max_tokens,
        **kwargs,
    )
    if length_constraint:
        return _prompt_orchestrator.enforce_response_length(response, length_constraint)
    if is_large_input:
        return response
    return _prompt_orchestrator.enforce_word_limit(response, learn_cap, cue=learn_cue)


async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs: Any):
    explicit_prompt_spec = kwargs.get("prompt_spec")
    if isinstance(explicit_prompt_spec, PromptSpec):
        kwargs.setdefault("_pre_classified_intent", explicit_prompt_spec.task)
        kwargs.setdefault("_pre_classified_depth", explicit_prompt_spec.depth)
        kwargs.setdefault("_pre_classified_reasoning", explicit_prompt_spec.reasoning)
        kwargs.setdefault("_pre_classified_style", explicit_prompt_spec.style)
        kwargs.setdefault("_pre_classified_capabilities", list(explicit_prompt_spec.capabilities))
    if (level or "").strip().lower() not in VALID_PROMPT_DEPTHS:
        raise ValueError(f"Unknown prompt depth '{level}'. Valid depths: {sorted(VALID_PROMPT_DEPTHS)}")
    # Pre-classify once so streaming path doesn't re-classify internally
    if "_pre_classified_intent" not in kwargs:
        classification = await _classify_intent(topic)
        kwargs.setdefault("_pre_classified_intent", classification.get("task") or classification.get("intent", "explain"))
        kwargs.setdefault("_pre_classified_depth", classification.get("depth", "accessible"))
        kwargs.setdefault("_pre_classified_reasoning", classification.get("reasoning", "direct"))
        kwargs.setdefault("_pre_classified_style", classification.get("style", "normal"))
        kwargs.setdefault("_pre_classified_capabilities", classification.get("capabilities", []))

    async for chunk in generate_stream_explanation_impl(
        topic,
        level,
        model,
        normalize_mode_fn=normalize_mode,
        load_search_context_fn=search_service.load_search_context,
        detect_intent_and_depth_fn=detect_intent_and_depth,
        detect_diagram_type_fn=detect_diagram_type,
        build_technical_prompt_fn=build_technical_prompt,
        build_prompt_fn=build_prompt,
        build_messages_fn=_build_messages,
        stream_chat_completion_fn=stream_chat_completion,
        technical_mode_handler_fn=technical_mode_handler,
        technical_route_fn=_technical_route,
        model_router=_model_router,
        response_builder=_response_builder,
        prompt_orchestrator=_prompt_orchestrator,
        wants_direct_answer_fn=_wants_direct_answer,
        enforce_socratic_response_constraints_fn=_enforce_socratic_response_constraints,
        normalize_question_signature_fn=_normalize_question_signature,
        word_count_fn=_word_count,
        normalize_whitespace_fn=_normalize_whitespace,
        compress_sentence_fn=_compress_sentence,
        append_cue_if_fits_fn=_append_cue_if_fits,
        **kwargs,
    ):
        yield chunk
