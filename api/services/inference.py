"""Native multi-provider inference service."""

import asyncio
import json
import re
import time
from typing import TypedDict
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
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

MODEL_PROFILES: dict[str, dict[str, float]] = {
    LEARNING_MODEL_SIMPLE: {
        "complexity": 0.45,
        "reasoning": 0.45,
        "explanation": 0.60,
        "latency_priority": 0.95,
    },
    LEARNING_MODEL_DETAILED: {
        "complexity": 0.70,
        "reasoning": 0.78,
        "explanation": 0.72,
        "latency_priority": 0.70,
    },
    TECHNICAL_MODEL_PRIMARY: {
        "complexity": 0.95,
        "reasoning": 0.95,
        "explanation": 0.88,
        "latency_priority": 0.40,
    },
    TECHNICAL_MODEL_FALLBACK: {
        "complexity": 0.60,
        "reasoning": 0.62,
        "explanation": 0.65,
        "latency_priority": 0.80,
    },
}

COST_PENALTY: dict[str, float] = {
    LEARNING_MODEL_SIMPLE: 0.08,
    LEARNING_MODEL_DETAILED: 0.16,
    TECHNICAL_MODEL_PRIMARY: 0.24,
    TECHNICAL_MODEL_FALLBACK: 0.12,
}

LATENCY_KEYWORDS = (
    r"\bquick\b",
    r"\bbrief\b",
    r"\bsummary\b",
    r"\btldr\b",
    r"\bshort\b",
    r"\bfast\b",
)
COMPLEXITY_KEYWORDS = (
    r"\boptimi[sz]e\b",
    r"\bdistributed\b",
    r"\bconcurrency\b",
    r"\btrade[ -]?offs?\b",
    r"\barchitecture\b",
    r"\bscal\w+\b",
    r"\bproof\b",
    r"\bderive\b",
)
REASONING_KEYWORDS = (
    r"\bwhy\b",
    r"\bcompare\b",
    r"\bversus\b",
    r"\bshould\b",
    r"\bpros?\b",
    r"\bcons?\b",
    r"\bdecision\b",
)
EXPLANATION_KEYWORDS = (
    r"\bexplain\b",
    r"\bhow\b",
    r"\bwalk me through\b",
    r"\bintuition\b",
    r"\bexample\b",
)


class IntentFeatures(TypedDict):
    complexity: float
    reasoning: float
    explanation: float
    latency_priority: float


def _clamp_feature(value: float) -> float:
    return max(0.0, min(1.0, value))


def _count_keyword_hits(text: str, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def extract_features(
    query: str,
    *,
    mode: str,
    level: str,
    intent: str | None = None,
    depth: str | None = None,
) -> IntentFeatures:
    """
    Build routing features from deterministic intent/depth + keyword signals.
    """
    lowered = (query or "").lower().strip()
    resolved_intent = intent
    resolved_depth = depth
    if not resolved_intent or not resolved_depth:
        try:
            classification = detect_intent_and_depth(query)
            resolved_intent = resolved_intent or classification.get("intent", "explain")
            resolved_depth = resolved_depth or classification.get("depth", "medium")
        except Exception:
            resolved_intent = resolved_intent or "explain"
            resolved_depth = resolved_depth or "medium"

    complexity = 0.35
    reasoning = 0.30
    explanation = 0.45
    latency_priority = 0.50

    if resolved_depth == "deep":
        complexity += 0.40
        reasoning += 0.25
        latency_priority -= 0.25
    elif resolved_depth == "shallow":
        complexity -= 0.10
        latency_priority += 0.30
        explanation += 0.08

    if resolved_intent == "compare":
        reasoning += 0.35
        complexity += 0.10
    elif resolved_intent == "brainstorm":
        reasoning += 0.28
        complexity += 0.16
    else:
        explanation += 0.22

    complexity += 0.08 * _count_keyword_hits(lowered, COMPLEXITY_KEYWORDS)
    reasoning += 0.07 * _count_keyword_hits(lowered, REASONING_KEYWORDS)
    explanation += 0.06 * _count_keyword_hits(lowered, EXPLANATION_KEYWORDS)
    latency_priority += 0.09 * _count_keyword_hits(lowered, LATENCY_KEYWORDS)

    if level in LEARNING_DETAILED_LEVELS:
        explanation += 0.08
        complexity += 0.06
        latency_priority -= 0.10

    if mode == TECHNICAL_MODE:
        complexity += 0.15
        reasoning += 0.12
        latency_priority -= 0.10
    elif mode == SOCRATIC_MODE:
        explanation += 0.06

    return {
        "complexity": _clamp_feature(complexity),
        "reasoning": _clamp_feature(reasoning),
        "explanation": _clamp_feature(explanation),
        "latency_priority": _clamp_feature(latency_priority),
    }


def score_model(features: IntentFeatures, model_alias: str, *, mode: str) -> float:
    """
    Weighted model scoring with explicit cost offsets.
    """
    profile = MODEL_PROFILES.get(model_alias, MODEL_PROFILES[LEARNING_MODEL_SIMPLE])
    score = 0.0
    for feature_name, value in features.items():
        score += float(value if isinstance(value, (int, float)) else 0.0) * profile.get(feature_name, 0.0)
    score -= COST_PENALTY.get(model_alias, 0.0)

    # Mode-specific tie-breakers to keep behavior intentional.
    if mode == TECHNICAL_MODE and model_alias == TECHNICAL_MODEL_PRIMARY:
        score += 0.15
    if mode == LEARNING_MODE and model_alias == LEARNING_MODEL_SIMPLE:
        score += 0.06

    return score


def route_model_aliases(
    query: str,
    *,
    mode: str,
    level: str,
    intent: str | None = None,
    depth: str | None = None,
) -> list[str]:
    """
    Rank model aliases using weighted feature scores.
    """
    if mode == SOCRATIC_MODE:
        return ["socratic"]

    features = extract_features(
        query,
        mode=mode,
        level=level,
        intent=intent,
        depth=depth,
    )
    candidates = [LEARNING_MODEL_SIMPLE, LEARNING_MODEL_DETAILED, TECHNICAL_MODEL_PRIMARY]
    if mode == TECHNICAL_MODE:
        candidates.append(TECHNICAL_MODEL_FALLBACK)

    ranked = sorted(
        candidates,
        key=lambda alias: score_model(features, alias, mode=mode),
        reverse=True,
    )
    # Keep deterministic ordering for equal scores.
    deduped: list[str] = []
    for alias in ranked:
        if alias not in deduped:
            deduped.append(alias)
    return deduped


def _technical_route(
    topic: str,
    *,
    intent: str,
    depth: str,
) -> tuple[str, str]:
    ranked = route_model_aliases(
        topic,
        mode=TECHNICAL_MODE,
        level="technical",
        intent=intent,
        depth=depth,
    )
    primary = ranked[0] if ranked else TECHNICAL_MODEL_PRIMARY
    fallback = next((alias for alias in ranked if alias != primary), TECHNICAL_MODEL_FALLBACK)
    return primary, fallback


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
    best_effort_response: str | None = None
    primary_alias, fallback_alias = _technical_route(topic, intent=intent, depth=depth)

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

    response = await _call_and_validate(primary_alias)

    if response is None:
        _tech_logger.info("technical_primary_retry", intent=intent, depth=depth)
        response = await _call_and_validate(primary_alias)

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


def is_transient_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(is_transient_http_error),
    reraise=True
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 1024, **kwargs) -> str:
    """Call API with given model and prompt."""
            
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
        routed_alias = model or route_model_aliases(topic, mode=mode, level=level)[0]
        response = await call_model(routed_alias, prompt, **kwargs)
        return _enforce_socratic_response_constraints(response)

    template = PROMPTS.get(level)
    if not template:
        raise ValueError(f"Unknown level: {level}")
        
    prompt = template.format(topic=topic)
        
    routed_aliases = route_model_aliases(topic, mode=mode, level=level)
    model_alias = model or (routed_aliases[0] if routed_aliases else _learning_model_for_level(level))
    return await call_model(model_alias, prompt, **kwargs)
async def generate_stream_explanation(topic: str, level: str, model: str | None = None, **kwargs):
    """Stream explanation for topic at given level."""
    mode = normalize_mode(kwargs.get("mode", LEARNING_MODE))
    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    route_telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
    prompt = ""

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

        prompt = build_technical_prompt(topic, intent, depth, diagram_type)
        if not prompt or not prompt.strip():
            prompt = TECHNICAL_MINIMAL_PROMPT

        primary_alias, _fallback_alias = _technical_route(topic, intent=intent, depth=depth)
        alias = model or primary_alias
        stream_telemetry: dict[str, object] = {}
        stream_start = time.perf_counter()
        streamed_chunks = 0
        stream_completed = True
        partial_failure = False

        try:
            async for chunk in stream_chat_completion(
                model=alias,
                messages=[{"role": "user", "content": prompt}],
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
                full_response = await technical_mode_handler(topic, **kwargs)
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

    if mode == SOCRATIC_MODE:
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
    
    if model:
        alias = model
    else:
        ranked_aliases = route_model_aliases(topic, mode=mode, level=level)
        alias = ranked_aliases[0] if ranked_aliases else (
            "socratic" if mode == SOCRATIC_MODE else _learning_model_for_level(level)
        )
    stream_telemetry: dict[str, object] = {}
    stream_start = time.perf_counter()
    if mode == SOCRATIC_MODE:
        socratic_raw_chunks: list[str] = []
        pending = ""
        seen_signatures: set[str] = set()
        emitted_questions = 0
        async for chunk in stream_chat_completion(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
            telemetry_sink=stream_telemetry,
        ):
            text_chunk = str(chunk or "")
            socratic_raw_chunks.append(text_chunk)
            if emitted_questions >= 3:
                continue

            pending += text_chunk
            matches = list(re.finditer(r"[^?]*\?", pending))
            if not matches:
                continue

            consumed = 0
            for match in matches:
                if emitted_questions >= 3:
                    break
                candidate = match.group(0).strip()
                consumed = match.end()
                if not candidate:
                    continue
                signature = _normalize_question_signature(candidate)
                if not signature or signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                prefix = "" if emitted_questions == 0 else "\n"
                yield f"{prefix}{candidate}"
                emitted_questions += 1
            pending = pending[consumed:]

        if emitted_questions > 0:
            yield "\n\nShare your answer, and I will guide the next step."
        else:
            constrained_response = _enforce_socratic_response_constraints("".join(socratic_raw_chunks))
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
