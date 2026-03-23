"""LiteLLM-backed inference service."""

import re
import time
from prompts import PROMPTS
from logging_config import anonymize_user_id, log_sampled_success
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE, normalize_mode
from services.llm_client import close_llm_client, stream_chat_completion, create_chat_completion
from services.model_runner import call_model as _call_model, stream_model
from services import technical_mode as _technical_mode
from services.intent import detect_intent_and_depth, detect_diagram_type, validate_technical_response

LEARNING_MODEL_SIMPLE = "default-fast"
LEARNING_MODEL_DETAILED = "learning-detailed"
LEARNING_DETAILED_LEVELS = {"eli15", "meme"}

TECHNICAL_MODEL_PRIMARY = _technical_mode.TECHNICAL_MODEL_PRIMARY
TECHNICAL_MODEL_FALLBACK = _technical_mode.TECHNICAL_MODEL_FALLBACK
TECHNICAL_TEMPERATURE = _technical_mode.TECHNICAL_TEMPERATURE
TECHNICAL_MAX_TOKENS = _technical_mode.TECHNICAL_MAX_TOKENS
TECHNICAL_LAST_RESORT_RESPONSE = _technical_mode.TECHNICAL_LAST_RESORT_RESPONSE
TECHNICAL_MINIMAL_PROMPT = _technical_mode.TECHNICAL_MINIMAL_PROMPT


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
    return _technical_mode.build_technical_prompt(topic, intent, depth, diagram_type)


async def call_model(model: str | None, prompt: str, max_tokens: int = 1024, **kwargs) -> str:
    return await _call_model(
        model,
        prompt,
        max_tokens=max_tokens,
        create_chat_completion_fn=create_chat_completion,
        log_sampled_success_fn=log_sampled_success,
        **kwargs,
    )


async def technical_mode_handler(topic: str, **kwargs) -> str:
    return await _technical_mode.technical_mode_handler(
        topic,
        build_prompt=build_technical_prompt,
        call_model=call_model,
        detect_intent_and_depth_fn=detect_intent_and_depth,
        detect_diagram_type_fn=detect_diagram_type,
        validate_response_fn=validate_technical_response,
        **kwargs,
    )


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

    if mode == TECHNICAL_MODE:
        passthrough_kwargs = dict(kwargs)
        passthrough_kwargs.pop("telemetry_sink", None)
        async for chunk in _technical_mode.technical_stream_explanation(
            topic,
            build_prompt=build_technical_prompt,
            stream_chat_completion=stream_chat_completion,
            call_model=call_model,
            technical_mode_handler_fn=technical_mode_handler,
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            retry=retry_flag,
            telemetry_sink=route_telemetry_sink,
            detect_intent_and_depth_fn=detect_intent_and_depth,
            detect_diagram_type_fn=detect_diagram_type,
            model=model,
            **passthrough_kwargs,
        ):
            yield chunk
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
        async for chunk in stream_model(
            model=alias,
            messages=[{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            retry=retry_flag,
            telemetry_sink=stream_telemetry,
            stream_chat_completion=stream_chat_completion,
            route_telemetry_sink=route_telemetry_sink,
        ):
            yield chunk
        return

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
