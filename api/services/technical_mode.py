"""Technical mode prompt assembly and handlers."""

from __future__ import annotations

import time
import structlog
from typing import Callable, Any, AsyncGenerator

from logging_config import log_sampled_success
from prompts import SYSTEM_PROMPT, DiagramType, build_prompt
from services.intent import (
    detect_intent_and_depth,
    detect_diagram_type,
    validate_technical_response,
)

_tech_logger = structlog.get_logger(__name__)

TECHNICAL_MODEL_PRIMARY = "technical-primary"
TECHNICAL_MODEL_FALLBACK = "technical-fallback"
TECHNICAL_TEMPERATURE = 0.4
TECHNICAL_MAX_TOKENS = 2048

TECHNICAL_LAST_RESORT_RESPONSE = (
    "## Core Idea\n"
    "Unable to generate a response at this time. Please retry in a moment.\n\n"
    "## First Principles Breakdown\n"
    "The model service may be temporarily unavailable.\n\n"
    "## Intuition\n"
    "Retrying often resolves transient issues.\n\n"
    "## Edge Cases / Limitations\n"
    "If this persists, check service status or try a different query.\n\n"
    "## Connections\n"
    "No connections available - response generation failed."
)

TECHNICAL_MINIMAL_PROMPT = "Explain the topic with concise technical clarity."


def build_technical_prompt(
    topic: str,
    intent: str,
    depth: str,
    diagram_type: str | None,
) -> str:
    """Assemble the final prompt string from components."""
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


def _build_messages(prompt: str) -> list[dict[str, str]]:
    system_prompt = SYSTEM_PROMPT.strip()
    if system_prompt:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    return [{"role": "user", "content": prompt}]


async def technical_mode_handler(
    topic: str,
    *,
    build_prompt: Callable[[str, str, str, str | None], str],
    call_model,
    detect_intent_and_depth_fn=None,
    detect_diagram_type_fn=None,
    validate_response_fn=None,
    **kwargs,
) -> str:
    """Single entry point for technical mode."""
    intent = "unknown"
    depth = "shallow"
    diagram_type = "generic"
    intent_detector = detect_intent_and_depth_fn or detect_intent_and_depth
    diagram_detector = detect_diagram_type_fn or detect_diagram_type
    response_validator = validate_response_fn or validate_technical_response

    try:
        classification = intent_detector(topic)
        intent = classification.get("intent", "unknown")
        depth = classification.get("depth", "shallow")
        diagram_type = diagram_detector(topic)
    except Exception as exc:
        _tech_logger.warning(
            "technical_classification_failed",
            error=str(exc),
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )

    prompt = build_prompt(topic, intent, depth, diagram_type)
    if not prompt or not prompt.strip():
        _tech_logger.warning(
            "technical_prompt_empty",
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )
        prompt = TECHNICAL_MINIMAL_PROMPT

    fallback_triggered = False
    fallback_reason: str | None = None
    best_effort_response: str | None = None

    def _ensure_terminal_char(value: str) -> str:
        trimmed = value.rstrip()
        if not trimmed:
            return value
        if trimmed[-1] in {".", "?", "!", "`"}:
            return trimmed
        return f"{trimmed}."

    async def _call(model_alias: str) -> str | None:
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
        response = await _call(model_alias)
        if response is None:
            return None
        is_valid, reason = response_validator(response, intent)
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

    response = await _call_and_validate(TECHNICAL_MODEL_PRIMARY)

    if response is None:
        _tech_logger.info("technical_primary_retry", intent=intent, depth=depth)
        response = await _call_and_validate(TECHNICAL_MODEL_PRIMARY)

    if response is None:
        fallback_triggered = True
        fallback_reason = "primary_exhausted"
        _tech_logger.info(
            "technical_fallback_triggered",
            reason=fallback_reason,
            intent=intent,
            depth=depth,
        )
        response = await _call_and_validate(TECHNICAL_MODEL_FALLBACK)

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


async def technical_stream_explanation(
    topic: str,
    *,
    build_prompt: Callable[[str, str, str, str | None], str],
    stream_chat_completion,
    call_model,
    technical_mode_handler_fn,
    request_id: str | None,
    user_id_hash: str | None,
    retry: bool,
    telemetry_sink: dict[str, Any] | None,
    detect_intent_and_depth_fn=None,
    detect_diagram_type_fn=None,
    **kwargs,
) -> AsyncGenerator[str, None]:
    intent = "unknown"
    depth = "shallow"
    diagram_type = "generic"
    intent_detector = detect_intent_and_depth_fn or detect_intent_and_depth
    diagram_detector = detect_diagram_type_fn or detect_diagram_type
    try:
        classification = intent_detector(topic)
        intent = classification.get("intent", "unknown")
        depth = classification.get("depth", "shallow")
        diagram_type = diagram_detector(topic)
    except Exception as exc:
        _tech_logger.warning(
            "technical_stream_classification_failed",
            error=str(exc),
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )

    prompt = build_prompt(topic, intent, depth, diagram_type)
    if not prompt or not prompt.strip():
        prompt = TECHNICAL_MINIMAL_PROMPT

    alias = kwargs.get("model") or TECHNICAL_MODEL_PRIMARY
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    streamed_chunks = 0
    stream_completed = True
    partial_failure = False

    try:
        async for chunk in stream_chat_completion(
            model=alias,
            messages=_build_messages(prompt),
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
            stream_completed = False
            full_response = await technical_mode_handler_fn(
                topic,
                build_prompt=build_prompt,
                call_model=call_model,
                request_id=request_id,
                **kwargs,
            )
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

    stream_duration_ms = round((time.perf_counter() - stream_start) * 1000, 2)
    model_inference_ms = stream_telemetry.get("model_inference_ms")
    token_usage = stream_telemetry.get("token_usage")
    estimated_cost_usd = stream_telemetry.get("estimated_cost_usd")
    model_name = stream_telemetry.get("model")

    if telemetry_sink is not None:
        telemetry_sink["token_usage"] = token_usage
        telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
        telemetry_sink["model_inference_ms"] = model_inference_ms
        telemetry_sink["stream_duration_ms"] = stream_duration_ms
        telemetry_sink["model_alias"] = alias
        telemetry_sink["model"] = model_name
        telemetry_sink["stream_completed"] = stream_completed
        telemetry_sink["partial_failure"] = partial_failure

    if stream_completed:
        log_sampled_success(
            "llm_stream_observed",
            request_id=request_id,
            user_id_hash=user_id_hash,
            model_alias=alias,
            model=model_name,
            latency_ms=model_inference_ms,
            stream_duration_ms=stream_duration_ms,
            token_usage=token_usage,
            estimated_cost_usd=estimated_cost_usd,
            retry=retry,
            sampled=True,
        )
    else:
        _tech_logger.warning(
            "llm_stream_observed_partial_failure",
            request_id=request_id,
            user_id_hash=user_id_hash,
            model_alias=alias,
            model=model_name,
            latency_ms=model_inference_ms,
            stream_duration_ms=stream_duration_ms,
            token_usage=token_usage,
            estimated_cost_usd=estimated_cost_usd,
            retry=retry,
            streamed_chunks=streamed_chunks,
            partial_failure=True,
        )
