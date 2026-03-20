"""LiteLLM-backed inference service."""

import asyncio
import json
import re
import time
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import get_settings
from prompts import (
    PROMPTS,
    TECHNICAL_DEPTH_PROMPT,
    TECHNICAL_STRUCTURED_PROMPT,
    TECHNICAL_COMPARE_PROMPT,
    TECHNICAL_BRAINSTORM_PROMPT,
    _TECHNICAL_DEEPER_LAYER,
    _TECHNICAL_DIAGRAM_INSTRUCTION,
)
from logging_config import logger, anonymize_user_id, log_sampled_success
from services.search import search_service
from services.intent import (
    detect_intent_and_depth,
    detect_diagram_type,
    validate_technical_response,
)
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode
from services.llm_client import close_llm_client, create_chat_completion, stream_chat_completion

_tech_logger = structlog.get_logger(__name__)

TECHNICAL_MODEL_PRIMARY = "technical-primary"
TECHNICAL_MODEL_FALLBACK = "technical-fallback"
TECHNICAL_TEMPERATURE = 0.4
TECHNICAL_MAX_TOKENS = 2048

LEARNING_MODEL_SIMPLE = "default-fast"
LEARNING_MODEL_DETAILED = "learning-detailed"
LEARNING_DETAILED_LEVELS = {"eli15", "meme"}

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


def _learning_model_for_level(level: str) -> str:
    if level in LEARNING_DETAILED_LEVELS:
        return LEARNING_MODEL_DETAILED
    return LEARNING_MODEL_SIMPLE


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
    diagram_instruction = (
        _TECHNICAL_DIAGRAM_INSTRUCTION.format(diagram_type=diagram_type)
        if diagram_type and intent != "compare"
        else ""
    )

    if intent == "brainstorm":
        return TECHNICAL_BRAINSTORM_PROMPT.format(
            topic=topic,
            diagram_instruction=diagram_instruction,
        )

    if intent == "compare":
        return TECHNICAL_COMPARE_PROMPT.format(topic=topic)

    deeper_layer_instruction = _TECHNICAL_DEEPER_LAYER if depth == "deep" else ""

    return TECHNICAL_STRUCTURED_PROMPT.format(
        topic=topic,
        deeper_layer_instruction=deeper_layer_instruction,
        diagram_instruction=diagram_instruction,
    )


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

    prompt = build_technical_prompt(topic, intent, depth, diagram_type)
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


def _normalize_question_signature(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()


def _extract_socratic_questions(response: str) -> list[str]:
    if not isinstance(response, str) or not response.strip():
        return []

    candidates = [segment.strip() for segment in re.findall(r"[^?]*\?", response)]
    if not candidates:
        return []

    unique_questions: list[str] = []
    seen_signatures: set[str] = set()
    for question in candidates:
        signature = _normalize_question_signature(question)
        if not signature or signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_questions.append(question)

    return unique_questions


def _enforce_socratic_response_constraints(response: str) -> str:
    """Return a concise Socratic reply capped to 2-3 progressive questions."""
    questions = _extract_socratic_questions(response)
    if not questions:
        return response

    constrained = "\n".join(questions[:3])
    return f"{constrained}\n\nShare your answer, and I will guide the next step."


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


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 1024, **kwargs) -> str:
    """Call API with given model and prompt."""
    task = kwargs.get("task", "general")
    if model in ["openai/gpt-oss-20b", "gpt-oss-20b", "deep_dive"]:
        task = "coding"
            
    try:
        alias = model or "default-fast"
        request_id = kwargs.get("request_id")
        retry_flag = bool(kwargs.get("regenerate", False))
        anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
        telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
        model_start = time.perf_counter()
        result = await create_chat_completion(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
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

    # ── TECHNICAL MODE (v2) ─────────────────────────────────────────────────
    if mode == TECHNICAL_MODE:
        return await technical_mode_handler(topic, **kwargs)
    # ────────────────────────────────────────────────────────────────────────

    if mode == SOCRATIC_MODE:
        template = PROMPTS.get("socratic")
        if not template:
            raise ValueError("Unknown mode template: socratic")
        prompt = template.format(
            topic=topic,
            conversation_context=kwargs.get("conversation_context", "No prior context."),
        )
        response = await call_model(model or "socratic", prompt, **kwargs)
        return _enforce_socratic_response_constraints(response)

    template = PROMPTS.get(level)
    if not template:
        raise ValueError(f"Unknown level: {level}")
        
    prompt = template.format(topic=topic)
        
    model_alias = model or _learning_model_for_level(level)
    return await call_model(model_alias, prompt, **kwargs)
async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs):
    """Stream explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    route_telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
    prompt = ""

    # ── TECHNICAL MODE (v2) — pseudo-streaming ──────────────────────────────
    if mode == TECHNICAL_MODE:
        full_response = await technical_mode_handler(topic, **kwargs)
        chunk_size = 400
        for i in range(0, len(full_response), chunk_size):
            yield full_response[i : i + chunk_size]
        return
    # ────────────────────────────────────────────────────────────────────────
    elif mode == SOCRATIC_MODE:
        template = PROMPTS.get("socratic")
        if not template:
            raise ValueError("Unknown mode template: socratic")
        prompt = template.format(
            topic=topic,
            conversation_context=kwargs.get("conversation_context", "No prior context."),
        )
    else:
        template = PROMPTS.get(level)
        if not template:
            raise ValueError(f"Unknown level: {level}")
        prompt = template.format(topic=topic)
    
    alias = model or ("socratic" if mode == SOCRATIC_MODE else _learning_model_for_level(level))
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    if mode == SOCRATIC_MODE:
        socratic_chunks: list[str] = []
        async for chunk in stream_chat_completion(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
            telemetry_sink=stream_telemetry,
        ):
            socratic_chunks.append(chunk)

        constrained_response = _enforce_socratic_response_constraints("".join(socratic_chunks))
        for index in range(0, len(constrained_response), 400):
            yield constrained_response[index : index + 400]
    else:
        async for chunk in stream_chat_completion(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
            telemetry_sink=stream_telemetry,
        ):
            yield chunk

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
