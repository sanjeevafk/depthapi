"""Native multi-provider inference service."""

import asyncio
import json
import re
import time
from typing import cast
import httpx
import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletionMessageParam
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from config import get_settings
from prompts import SYSTEM_PROMPT, DiagramType, build_prompt
from logging_config import logger, anonymize_user_id, log_sampled_success
from services.intent import (
    detect_intent_and_depth,
    detect_diagram_type,
    validate_technical_response,
)
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode
from services.llm_client import close_llm_client, create_chat_completion, stream_chat_completion
from services.token_count import count_prompt_tokens
from services.inference_constants import (
    TECHNICAL_MODEL_PRIMARY,
    TECHNICAL_MODEL_FALLBACK,
    TECHNICAL_TEMPERATURE,
    TECHNICAL_MAX_TOKENS,
    LEARNING_DETAILED_LEVELS,
    TECHNICAL_LAST_RESORT_RESPONSE,
    TECHNICAL_MINIMAL_PROMPT,
)
from services.inference_routing import (
    extract_features,
    score_model,
    route_model_aliases,
    _technical_route,
    _learning_model_for_level,
    _effective_alias_chain,
)
from services.inference_prompting import (
    _extract_length_constraint,
    _apply_length_constraint,
    _normalize_whitespace,
    _word_count,
    _split_sentences,
    _append_cue_if_fits,
    _compress_sentence,
    _enforce_word_limit,
    _enforce_length_constraint,
    _learning_length_policy,
    _is_large_input,
    _drain_complete_sentences,
)
from services.inference_search import _truncate_search_context, _append_search_context, _load_search_context
from services.inference_socratic import (
    _normalize_question_signature,
    _extract_socratic_questions,
    _wants_direct_answer,
    _get_direct_answer_patterns,
    _fallback_socratic_question,
    _enforce_socratic_response_constraints,
)

_tech_logger = structlog.get_logger(__name__)


def is_low_quality(response: str) -> bool:
    text = (response or "").strip()
    return (
        len(text.split()) < 40
        or text.count("\n") < 2
        or "not sure" in text.lower()
    )


async def _call_with_quality_escalation(
    aliases: list[str],
    prompt: str,
    *,
    complexity: float,
    max_tokens: int = 300,
    **kwargs,
) -> str:
    chain = _effective_alias_chain(aliases, complexity=complexity)
    if not chain:
        raise RuntimeError("No eligible model aliases available for quality routing.")

    primary_alias = chain[0]
    primary_response = await call_model(primary_alias, prompt, max_tokens=max_tokens, **kwargs)
    if not is_low_quality(primary_response):
        return primary_response

    if len(chain) < 2:
        return primary_response

    retry_alias = chain[1]
    retry_response = await call_model(retry_alias, prompt, max_tokens=max_tokens, **kwargs)
    return retry_response or primary_response


def build_technical_prompt(
    topic: str,
    intent: str,
    depth: str,
    diagram_type: str | None,
) -> str:
    """
    Assembles the final prompt string from components.
    No LLM calls. Pure string construction.
    """
    mode_key = "technical_structured"
    if intent == "brainstorm":
        mode_key = "technical_brainstorm"
    elif intent == "compare":
        mode_key = "technical_compare"

    def _map_diagram(value: str | None) -> DiagramType:
        normalized = (value or "").strip().lower()
        mapping = {
            "flowchart": DiagramType.FLOWCHART_TD,
            "flowchart td": DiagramType.FLOWCHART_TD,
            "flowchart lr": DiagramType.FLOWCHART,
            "sequencediagram": DiagramType.SEQUENCE,
            "classdiagram": DiagramType.CLASS,
            "erdiagram": DiagramType.ER,
            "statediagram-v2": DiagramType.STATE,
        }
        return mapping.get(normalized, DiagramType.FLOWCHART_TD)

    diagram = None if mode_key == "technical_compare" else _map_diagram(diagram_type)
    return build_prompt(mode_key, topic, diagram_type=diagram)


async def technical_mode_handler(
    topic: str,
    **kwargs,
) -> str:
    """
    Single entry point for technical mode. Handles:
    - Intent + depth detection
    - Diagram type detection
    - Prompt assembly
    - Primary model call with one retry
    - Fallback to secondary model on failure
    - Output validation with one retry on invalid output
    - Guaranteed non-empty return (last resort response if all else fails)

    kwargs are passed through to call_model for telemetry/request_id/etc.
    Never raises. Always returns a non-empty string.
    """
    intent = "unknown"
    depth = "shallow"
    diagram_type = "generic"
    try:
        classification = detect_intent_and_depth(topic)
        intent = classification["intent"]
        depth = classification["depth"]
        diagram_type = detect_diagram_type(topic)
    except Exception as exc:
        _tech_logger.warning(
            "technical_classification_failed",
            error=str(exc),
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )

    prefetched_search_context = kwargs.pop("_search_context", None)
    search_context = (
        _truncate_search_context(prefetched_search_context)
        if isinstance(prefetched_search_context, str)
        else await _load_search_context(topic, mode=TECHNICAL_MODE)
    )
    prompt = build_technical_prompt(topic, intent, depth, diagram_type)
    if not prompt or not prompt.strip():
        _tech_logger.warning(
            "technical_prompt_empty",
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )
        prompt = TECHNICAL_MINIMAL_PROMPT
    prompt = _append_search_context(prompt, search_context)

    fallback_triggered = False
    fallback_reason: str | None = None
    best_effort_response: str | None = None
    is_pro = bool(kwargs.get("is_pro", False))
    technical_complexity = float(
        extract_features(
            topic,
            mode=TECHNICAL_MODE,
            level="technical",
            intent=intent,
            depth=depth,
        ).get("complexity", 0.0)
        or 0.0
    )
    ranked_aliases = _effective_alias_chain(
        route_model_aliases(
            topic,
            mode=TECHNICAL_MODE,
            level="technical",
            intent=intent,
            depth=depth,
            is_pro=is_pro,
            search_api_used=bool(search_context),
        ),
        complexity=technical_complexity,
    )
    primary_alias = ranked_aliases[0] if ranked_aliases else TECHNICAL_MODEL_PRIMARY
    fallback_alias = next((alias for alias in ranked_aliases if alias != primary_alias), TECHNICAL_MODEL_FALLBACK)

    def _ensure_terminal_char(value: str) -> str:
        trimmed = value.rstrip()
        if not trimmed:
            return value
        if trimmed[-1] in {".", "?", "!", "`"}:
            return trimmed
        return f"{trimmed}."

    async def _call(model_alias: str) -> str | None:
        """Single model call. Returns content string or None on any failure."""
        try:
            call_kwargs = dict(kwargs)
            call_kwargs["temperature"] = TECHNICAL_TEMPERATURE
            call_kwargs.pop("max_tokens", None)
            result = await call_model(
                model_alias,
                prompt,
                max_tokens=TECHNICAL_MAX_TOKENS,
                **call_kwargs,
            )
            if not result or not result.strip():
                _tech_logger.warning(
                    "technical_model_empty_response",
                    model=model_alias,
                    intent=intent,
                    depth=depth,
                )
                return None
            nonlocal best_effort_response
            best_effort_response = str(result)
            return result
        except Exception as exc:
            _tech_logger.warning(
                "technical_model_call_failed",
                model=model_alias,
                error=str(exc),
                intent=intent,
                depth=depth,
            )
            return None

    async def _call_and_validate(model_alias: str) -> str | None:
        """Call model and validate output. Returns valid content or None."""
        response = await _call(model_alias)
        if response is None:
            return None
        is_valid, reason = validate_technical_response(response, intent)
        if not is_valid:
            _tech_logger.warning(
                "technical_response_invalid",
                model=model_alias,
                validation_failure=reason,
                intent=intent,
                depth=depth,
                response_length=len(response),
            )
            return None
        return response

    response_alias = primary_alias
    response = await _call_and_validate(primary_alias)

    if response is None:
        _tech_logger.info("technical_primary_retry", intent=intent, depth=depth)
        response = await _call_and_validate(primary_alias)
        response_alias = primary_alias

    if response is None:
        fallback_triggered = True
        fallback_reason = "primary_exhausted"
        _tech_logger.info(
            "technical_fallback_triggered",
            reason=fallback_reason,
            intent=intent,
            depth=depth,
        )
        response = await _call_and_validate(fallback_alias)
        response_alias = fallback_alias

    if response is not None and is_low_quality(response):
        quality_retry_alias: str | None = None
        if response_alias in ranked_aliases:
            current_index = ranked_aliases.index(response_alias)
            if current_index + 1 < len(ranked_aliases):
                quality_retry_alias = ranked_aliases[current_index + 1]
        if quality_retry_alias is not None:
            quality_retry_response = await _call_and_validate(quality_retry_alias)
            if quality_retry_response:
                response = quality_retry_response
                fallback_triggered = True
                fallback_reason = "quality_escalation"
                response_alias = quality_retry_alias

    if response is None:
        fallback_triggered = True
        if best_effort_response and best_effort_response.strip():
            fallback_reason = "best_effort_unvalidated"
            response = _ensure_terminal_char(best_effort_response)
        else:
            fallback_reason = "all_models_failed"
            response = TECHNICAL_LAST_RESORT_RESPONSE

    _tech_logger.info(
        "technical_mode_complete",
        intent=intent,
        depth=depth,
        diagram_type=diagram_type,
        fallback_triggered=fallback_triggered,
        fallback_reason=fallback_reason,
        response_length=len(response),
    )

    return response


async def close_client():
    """Close shared LLM client resources."""
    await close_llm_client()


def _extract_usage_dict(usage_obj) -> dict[str, int] | None:
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        usage_obj = usage_obj.model_dump()
    elif hasattr(usage_obj, "dict"):
        usage_obj = usage_obj.dict()
    if not isinstance(usage_obj, dict):
        return None

    prompt_tokens = usage_obj.get("prompt_tokens")
    completion_tokens = usage_obj.get("completion_tokens")
    total_tokens = usage_obj.get("total_tokens")
    try:
        return {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(total_tokens or 0),
        }
    except (TypeError, ValueError):
        return None


def _extract_estimated_cost(result, usage: dict[str, int] | None) -> float | None:
    direct_cost = getattr(result, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        return float(direct_cost)

    hidden_params = getattr(result, "_hidden_params", None)
    if isinstance(hidden_params, dict):
        hidden_cost = hidden_params.get("response_cost")
        if isinstance(hidden_cost, (int, float)):
            return float(hidden_cost)

    if isinstance(usage, dict):
        usage_cost = usage.get("cost")
        if isinstance(usage_cost, (int, float)):
            return float(usage_cost)

    return None


MODE_SYSTEM_PROMPTS = {
    LEARNING_MODE: (
        "Mode: Learning. Provide clear explanations and adapt depth to the user's request. "
        "Follow the user's query exactly. If the query asks for comparison, respond with a structured comparison. "
        "Do not ignore or override the latest user input."
    ),
    SOCRATIC_MODE: "Mode: Socratic. Guide the user with questions rather than direct answers.",
    TECHNICAL_MODE: "Mode: Technical. Provide precise, structured, technically rigorous responses.",
}

COMPARISON_SYSTEM_PROMPT = (
    "Compare the concepts clearly: definitions, key differences, use cases, and a concise table if helpful."
)


def _is_comparison_query(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        " vs " in lowered
        or " versus " in lowered
        or "compare" in lowered
        or "comparison" in lowered
        or "difference between" in lowered
    )


def trimHistoryForCost(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not history:
        return []
    max_turns = 6
    return history[-max_turns * 2 :]


def _build_messages(
    prompt: str,
    *,
    conversation_messages: list[dict[str, str]] | None = None,
    intent_system_prompt: str | None = None,
    mode: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    system_parts: list[str] = []
    system_prompt = SYSTEM_PROMPT.strip()
    if system_prompt:
        system_parts.append(system_prompt)
    mode_prompt = MODE_SYSTEM_PROMPTS.get(mode or "", "").strip()
    if mode_prompt:
        system_parts.append(mode_prompt)
    if intent_system_prompt:
        system_parts.append(intent_system_prompt.strip())
    if mode == LEARNING_MODE and _is_comparison_query(prompt):
        system_parts.append(COMPARISON_SYSTEM_PROMPT)
    if system_parts:
        messages.append({"role": "system", "content": "\n".join(system_parts)})
    if conversation_messages:
        messages.extend(trimHistoryForCost(conversation_messages))
    messages.append({"role": "user", "content": prompt})
    assert messages[-1].get("role") == "user"
    assert messages[-1].get("content") == prompt
    return messages


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
    reraise=True
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 300, **kwargs) -> str:
    """Call API with given model and prompt."""

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



async def generate_explanation(topic: str, level: str, model: str | None = None, **kwargs) -> str:
    """Generate explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    settings = get_settings()

    # ── TECHNICAL MODE (v2) ─────────────────────────────────────────────────
    if mode == TECHNICAL_MODE:
        return await technical_mode_handler(topic, **kwargs)
    # ────────────────────────────────────────────────────────────────────────

    if mode == SOCRATIC_MODE:
        search_context = await _load_search_context(topic, mode=SOCRATIC_MODE)
        prompt = build_prompt(
            "socratic",
            topic,
            conversation_context=kwargs.get("conversation_context", ""),
        )
        prompt = _append_search_context(prompt, search_context)
        routed_aliases = route_model_aliases(
            topic,
            mode=mode,
            level=level,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        socratic_complexity = float(
            extract_features(
                topic,
                mode=mode,
                level=level,
            ).get("complexity", 0.0)
            or 0.0
        )
        settings = get_settings()
        max_tokens = int(getattr(settings, "max_output_tokens_socratic", 1024))
        response = await _call_with_quality_escalation(
            [model] if model else routed_aliases,
            prompt,
            complexity=socratic_complexity,
            max_tokens=max_tokens,
            **kwargs,
        )
        return _enforce_socratic_response_constraints(
            response,
            topic=topic,
            wants_direct_answer=_wants_direct_answer(topic),
        )

    search_context = await _load_search_context(topic, mode=LEARNING_MODE)
    prompt = build_prompt(level, topic)
    prompt = _append_search_context(prompt, search_context)
    length_constraint = _extract_length_constraint(topic)
    prompt = _apply_length_constraint(prompt, length_constraint)
    is_large_input = _is_large_input(topic)
    learn_cap, learn_cue = _learning_length_policy(topic)
        
    routed_aliases = route_model_aliases(
        topic,
        mode=mode,
        level=level,
        is_pro=bool(kwargs.get("is_pro", False)),
        search_api_used=bool(search_context),
    )
    learning_complexity = float(
        extract_features(
            topic,
            mode=mode,
            level=level,
        ).get("complexity", 0.0)
        or 0.0
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
        return _enforce_length_constraint(response, length_constraint)
    if is_large_input:
        return response
    return _enforce_word_limit(response, learn_cap, cue=learn_cue)
async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs):
    """Stream explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    route_telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
    prompt = ""
    settings = get_settings()

    if mode == TECHNICAL_MODE:
        intent = "unknown"
        depth = "shallow"
        diagram_type = "generic"
        try:
            classification = detect_intent_and_depth(topic)
            intent = classification["intent"]
            depth = classification["depth"]
            diagram_type = detect_diagram_type(topic)
        except Exception as exc:
            _tech_logger.warning(
                "technical_stream_classification_failed",
                error=str(exc),
                intent=intent,
                depth=depth,
                diagram_type=diagram_type,
            )

        search_context = await _load_search_context(topic, mode=TECHNICAL_MODE)
        prompt = build_technical_prompt(topic, intent, depth, diagram_type)
        if not prompt or not prompt.strip():
            prompt = TECHNICAL_MINIMAL_PROMPT
        prompt = _append_search_context(prompt, search_context)
        messages = _build_messages(
            prompt,
            conversation_messages=kwargs.get("conversation_messages"),
            intent_system_prompt=kwargs.get("intent_system_prompt"),
            mode=mode,
        )

        primary_alias, _fallback_alias = _technical_route(
            topic,
            intent=intent,
            depth=depth,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        alias = model or primary_alias
        stream_telemetry: dict[str, object] = {}
        stream_start = time.perf_counter()
        streamed_chunks = 0
        stream_completed = True
        partial_failure = False

        try:
            async for chunk in stream_chat_completion(
                model=alias,
                messages=cast(list[ChatCompletionMessageParam], messages),
                max_tokens=TECHNICAL_MAX_TOKENS,
                temperature=TECHNICAL_TEMPERATURE,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                streamed_chunks += 1
                yield chunk
        except Exception as exc:
            _tech_logger.warning(
                "technical_stream_failed",
                error=str(exc),
                streamed_chunks=streamed_chunks,
                model_alias=alias,
            )
            if streamed_chunks == 0:
                full_response = await technical_mode_handler(topic, _search_context=search_context, **kwargs)
                for index in range(0, len(full_response), 400):
                    yield full_response[index : index + 400]
            else:
                stream_completed = False
                partial_failure = True
                _tech_logger.warning(
                    "technical_stream_partial_failure",
                    error=str(exc),
                    streamed_chunks=streamed_chunks,
                    model_alias=alias,
                    partial_failure=True,
                )
                # Signal incomplete response to client
                yield "\n\n---\n*Response incomplete due to a service interruption.*"
        stream_duration_ms = round((time.perf_counter() - stream_start) * 1000, 2)
        model_inference_ms = stream_telemetry.get("model_inference_ms")
        token_usage = stream_telemetry.get("token_usage")
        estimated_cost_usd = stream_telemetry.get("estimated_cost_usd")
        model_name = stream_telemetry.get("model")

        if route_telemetry_sink is not None:
            route_telemetry_sink["token_usage"] = token_usage
            route_telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
            route_telemetry_sink["model_inference_ms"] = model_inference_ms
            route_telemetry_sink["stream_duration_ms"] = stream_duration_ms
            route_telemetry_sink["model_alias"] = alias
            route_telemetry_sink["model"] = model_name
            route_telemetry_sink["stream_completed"] = stream_completed
            route_telemetry_sink["partial_failure"] = partial_failure

        if stream_completed:
            log_sampled_success(
                "llm_stream_observed",
                request_id=request_id,
                user_id_hash=anonymized_user_id,
                model_alias=alias,
                model=model_name,
                latency_ms=model_inference_ms,
                stream_duration_ms=stream_duration_ms,
                token_usage=token_usage,
                estimated_cost_usd=estimated_cost_usd,
                retry=retry_flag,
                sampled=True,
            )
        else:
            _tech_logger.warning(
                "llm_stream_observed_partial_failure",
                request_id=request_id,
                user_id_hash=anonymized_user_id,
                model_alias=alias,
                model=model_name,
                latency_ms=model_inference_ms,
                stream_duration_ms=stream_duration_ms,
                token_usage=token_usage,
                estimated_cost_usd=estimated_cost_usd,
                retry=retry_flag,
                streamed_chunks=streamed_chunks,
                partial_failure=True,
            )
        return

    length_constraint: tuple[str, int] | None = None
    if mode == SOCRATIC_MODE:
        search_context = await _load_search_context(topic, mode=SOCRATIC_MODE)
        prompt = build_prompt(
            "socratic",
            topic,
            conversation_context=kwargs.get("conversation_context", ""),
        )
        prompt = _append_search_context(prompt, search_context)
    else:
        search_context = await _load_search_context(topic, mode=LEARNING_MODE)
        prompt = build_prompt(level, topic)
        prompt = _append_search_context(prompt, search_context)
        length_constraint = _extract_length_constraint(topic)
        prompt = _apply_length_constraint(prompt, length_constraint)
    
    if model:
        alias = model
    else:
        ranked_aliases = route_model_aliases(
            topic,
            mode=mode,
            level=level,
            is_pro=bool(kwargs.get("is_pro", False)),
            search_api_used=bool(search_context),
        )
        alias = ranked_aliases[0] if ranked_aliases else (
            "socratic" if mode == SOCRATIC_MODE else _learning_model_for_level(level)
        )
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    if mode == SOCRATIC_MODE:
        socratic_raw_chunks: list[str] = []
        pending = ""
        seen_signatures: set[str] = set()
        emitted_count = 0
        wants_direct_answer = _wants_direct_answer(topic)
        socratic_error: Exception | None = None
        max_questions = 3
        footer = "Share your answer, and I will guide the next step."

        try:
            settings = get_settings()
            max_tokens = int(getattr(settings, "max_output_tokens_socratic", 1024))
            async for chunk in stream_chat_completion(
                model=alias,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    _build_messages(
                        prompt,
                        conversation_messages=kwargs.get("conversation_messages"),
                        intent_system_prompt=kwargs.get("intent_system_prompt"),
                        mode=mode,
                    ),
                ),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=max_tokens,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                text_chunk = str(chunk or "")
                socratic_raw_chunks.append(text_chunk)
                if wants_direct_answer or emitted_count >= max_questions:
                    continue

                pending += text_chunk
                while True:
                    match = re.search(r"[^?]*\?", pending)
                    if not match:
                        break

                    candidate = match.group(0).strip()
                    consumed = match.end()
                    pending = pending[consumed:]
                    if not candidate:
                        continue
                        
                    signature = _normalize_question_signature(candidate)
                    if not signature or signature in seen_signatures:
                        continue
                        
                    seen_signatures.add(signature)
                    yield candidate + " "
                    emitted_count += 1
                    
                    if emitted_count >= max_questions:
                        yield footer
                        break
        except Exception as exc:
            socratic_error = exc
            stream_telemetry["stream_error"] = str(exc)
            stream_telemetry["stream_error_type"] = type(exc).__name__
            stream_telemetry["request_id"] = request_id
            _tech_logger.warning(
                "socratic_stream_failed",
                request_id=request_id,
                model_alias=alias,
                error=str(exc),
            )

        if wants_direct_answer or emitted_count == 0:
            constrained_response = _enforce_socratic_response_constraints(
                "".join(socratic_raw_chunks),
                topic=topic,
                wants_direct_answer=wants_direct_answer,
            )
            fallback_response = constrained_response.strip()
            if socratic_error is not None and not fallback_response:
                fallback_response = f"I hit a temporary issue while streaming. Please try again. {footer}"
            elif socratic_error is not None:
                # We had some content but it crashed - make sure it includes the error message if nothing else
                if "temporary issue while streaming" not in fallback_response:
                    fallback_response = f"I hit a temporary issue while streaming. {fallback_response}"
            for index in range(0, len(fallback_response), 400):
                yield fallback_response[index : index + 400]
        elif emitted_count > 0 and emitted_count < max_questions:
            # We emitted some questions but didn't hit the cap
            yield footer
    else:
        streamed_chunks = 0
        remaining_chars = None
        target_words = None
        words_emitted = 0
        pending = ""
        cue: str | None = None
        emitted_any = False
        trimmed_for_limit = False
        is_large_input = _is_large_input(topic)
        if length_constraint:
            unit, count = length_constraint
            if unit == "chars":
                remaining_chars = count
            else:
                target_words = count
        elif not is_large_input:
            target_words, cue = _learning_length_policy(topic)
        try:
            max_tokens = int(getattr(get_settings(), "max_output_tokens_learning", 1024))
            async for chunk in stream_chat_completion(
                model=alias,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    _build_messages(
                        prompt,
                        conversation_messages=kwargs.get("conversation_messages"),
                        intent_system_prompt=kwargs.get("intent_system_prompt"),
                        mode=mode,
                    ),
                ),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=max_tokens,
                request_id=request_id,
                telemetry_sink=stream_telemetry,
            ):
                text_chunk = str(chunk or "")
                if remaining_chars is not None:
                    if remaining_chars <= 0:
                        break
                    if len(text_chunk) <= remaining_chars:
                        streamed_chunks += 1
                        remaining_chars -= len(text_chunk)
                        yield text_chunk
                    else:
                        streamed_chunks += 1
                        yield text_chunk[:remaining_chars]
                        remaining_chars = 0
                        break
                    continue

                if target_words is not None:
                    pending += text_chunk
                    sentences, pending = _drain_complete_sentences(pending)
                    if not sentences:
                        continue
                    for sentence in sentences:
                        sentence_words = _word_count(sentence)
                        if words_emitted + sentence_words <= target_words:
                            streamed_chunks += 1
                            prefix = "" if not emitted_any else " "
                            yield f"{prefix}{sentence}"
                            emitted_any = True
                            words_emitted += sentence_words
                        else:
                            trimmed_for_limit = True
                            pending = ""
                            break
                    if trimmed_for_limit:
                        break
                    continue

                streamed_chunks += 1
                yield text_chunk
        except Exception as exc:
            stream_telemetry["stream_error"] = str(exc)
            stream_telemetry["stream_error_type"] = type(exc).__name__
            stream_telemetry["request_id"] = request_id
            _tech_logger.warning(
                "learning_stream_failed",
                request_id=request_id,
                model_alias=alias,
                streamed_chunks=streamed_chunks,
                error=str(exc),
            )
            if streamed_chunks == 0:
                yield "Unable to stream a response right now. Please try again."
            else:
                yield "\n\n---\n*Response incomplete due to a service interruption.*"

        if target_words is not None:
            if not trimmed_for_limit:
                final_pending = _normalize_whitespace(pending)
                if final_pending:
                    final_words = _word_count(final_pending)
                    if words_emitted + final_words <= target_words:
                        prefix = "" if not emitted_any else " "
                        yield f"{prefix}{final_pending}"
                        emitted_any = True
                        words_emitted += final_words
                    elif not emitted_any:
                        compressed = _compress_sentence(final_pending, target_words)
                        if compressed:
                            result = _append_cue_if_fits(compressed, target_words, cue)
                            yield result
                            emitted_any = True
                            words_emitted = _word_count(result)
            if trimmed_for_limit and cue:
                cue_words = _word_count(cue)
                if words_emitted + cue_words <= target_words:
                    prefix = "" if not emitted_any else " "
                    yield f"{prefix}{cue}"

    stream_duration_ms = round((time.perf_counter() - stream_start) * 1000, 2)
    model_inference_ms = stream_telemetry.get("model_inference_ms")
    token_usage = stream_telemetry.get("token_usage")
    estimated_cost_usd = stream_telemetry.get("estimated_cost_usd")
    model_name = stream_telemetry.get("model")

    if route_telemetry_sink is not None:
        route_telemetry_sink["token_usage"] = token_usage
        route_telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
        route_telemetry_sink["model_inference_ms"] = model_inference_ms
        route_telemetry_sink["stream_duration_ms"] = stream_duration_ms
        route_telemetry_sink["model_alias"] = alias
        route_telemetry_sink["model"] = model_name
        if "stream_error" in stream_telemetry:
            route_telemetry_sink["stream_error"] = stream_telemetry.get("stream_error")
            route_telemetry_sink["stream_error_type"] = stream_telemetry.get("stream_error_type")
            route_telemetry_sink["request_id"] = stream_telemetry.get("request_id")

    log_sampled_success(
        "llm_stream_observed",
        request_id=request_id,
        user_id_hash=anonymized_user_id,
        model_alias=alias,
        model=model_name,
        latency_ms=model_inference_ms,
        stream_duration_ms=stream_duration_ms,
        token_usage=token_usage,
        estimated_cost_usd=estimated_cost_usd,
        retry=retry_flag,
        sampled=True,
    )
