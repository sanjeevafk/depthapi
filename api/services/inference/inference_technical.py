"""Technical-mode orchestration extracted from inference service."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from api.prompts import DiagramType, PromptSpec, build_prompt
from api.logging_config import logger
from api.services.inference.inference_constants import (
    TECHNICAL_LAST_RESORT_RESPONSE,
    TECHNICAL_MAX_TOKENS,
    TECHNICAL_MINIMAL_PROMPT,
    TECHNICAL_MODEL_FALLBACK,
    TECHNICAL_MODEL_PRIMARY,
    TECHNICAL_TEMPERATURE,
)
from api.services.inference.inference_routing import extract_features
from api.services.inference.inference_search import _append_search_context, _truncate_search_context
from api.utils import TECHNICAL_MODE, canonical_prompt_depth

_tech_logger = structlog.get_logger(__name__)


def is_low_quality(response: str) -> bool:
    """Detect low-signal output that should trigger quality escalation."""
    text = (response or "").strip()
    return (
        len(text.split()) < 40
        or text.count("\n") < 2
        or "not sure" in text.lower()
    )


def has_complete_ending(response: str) -> bool:
    text = (response or "").strip()
    if not text:
        return False
    if text.endswith("..."):
        return False
    last_char = text[-1]
    if last_char in {".", "?", "!", "`"}:
        return True
    return last_char.isalnum()


async def call_with_quality_escalation(
    aliases: list[str],
    prompt: str,
    *,
    complexity: float,
    max_tokens: int,
    call_model_fn: Callable[..., Awaitable[str]],
    effective_alias_chain_fn: Callable[..., list[str]],
    **kwargs: Any,
) -> str:
    if len(aliases) == 1:
        chain = [aliases[0]]
    else:
        chain = effective_alias_chain_fn(aliases, complexity=complexity)
    if not chain:
        raise RuntimeError("No eligible model aliases available for quality routing.")

    primary_alias = chain[0]
    primary_response = await call_model_fn(primary_alias, prompt, max_tokens=max_tokens, **kwargs)
    if not is_low_quality(primary_response):
        return primary_response

    if len(chain) < 2:
        return primary_response

    retry_alias = chain[1]
    retry_response = await call_model_fn(retry_alias, prompt, max_tokens=max_tokens, **kwargs)
    return retry_response or primary_response


def build_technical_prompt(
    topic: str,
    intent: str,
    depth: str,
    diagram_type: str | None,
    *,
    reasoning: str = "direct",
    style: str = "academic",
    capabilities: frozenset[str] | None = None,
) -> str:
    """Assemble a technical prompt from independent prompt axes."""
    valid_tasks = {"explain", "compare", "brainstorm", "analyze", "summarize"}
    task = intent if intent in valid_tasks else {
        "brainstorm": "brainstorm",
        "compare": "compare",
        "summarize": "summarize",
    }.get(intent, "analyze")

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

    resolved_capabilities = set(capabilities or ())
    needs_diagram = "requires_diagram" in resolved_capabilities or task in {"analyze", "brainstorm"}
    if needs_diagram:
        resolved_capabilities.add("requires_diagram")
    spec = PromptSpec(
        topic=topic,
        depth=canonical_prompt_depth(depth),
        task=task,
        reasoning=reasoning,
        style=style,
        capabilities=frozenset(resolved_capabilities),
    )
    return build_prompt(spec, diagram_type=_map_diagram(diagram_type) if needs_diagram else None)


async def technical_mode_handler(
    topic: str,
    *,
    level: str | None = None,
    build_technical_prompt_fn: Callable[..., str] | None = None,
    detect_intent_and_depth_fn: Callable[[str], dict[str, str]],
    detect_diagram_type_fn: Callable[[str], str | None],
    validate_technical_response_fn: Callable[[str, str], tuple[bool, str]],
    load_search_context_fn: Callable[..., Awaitable[str]],
    route_aliases_fn: Callable[..., list[str]],
    call_model_fn: Callable[..., Awaitable[str]],
    **kwargs: Any,
) -> str:
    explicit_prompt_spec = kwargs.get("prompt_spec")
    reasoning = "direct"
    style = "academic"
    capabilities: frozenset[str] | None = None
    intent = "explain"
    depth = canonical_prompt_depth(level) if level else "technical"
    diagram_type: str | None = "generic"

    if isinstance(explicit_prompt_spec, PromptSpec):
        intent = str(explicit_prompt_spec.task or "explain")
        depth = canonical_prompt_depth(level or explicit_prompt_spec.depth)
        reasoning = str(explicit_prompt_spec.reasoning or "direct")
        style = str(explicit_prompt_spec.style or "academic")
        capabilities = frozenset(explicit_prompt_spec.capabilities)
        diagram_type = (
            detect_diagram_type_fn(topic)
            if "requires_diagram" in capabilities
            else None
        )
    else:
        try:
            classification = detect_intent_and_depth_fn(topic)
            intent = str(classification.get("task") or classification.get("intent", "explain"))
            depth = canonical_prompt_depth(str(classification.get("depth", depth)))
            diagram_type = detect_diagram_type_fn(topic)
        except Exception as exc:
            _tech_logger.warning(
                "technical_classification_failed",
                error=str(exc),
                intent=intent,
                depth=depth,
                diagram_type=diagram_type,
            )

    prefetched_search_context = kwargs.pop("_search_context", None)
    rag_context = kwargs.pop("_rag_context", None)
    
    search_context = (
        _truncate_search_context(prefetched_search_context)
        if isinstance(prefetched_search_context, str)
        else await load_search_context_fn(topic, mode=TECHNICAL_MODE)
    )
    prompt_builder = build_technical_prompt_fn or build_technical_prompt
    prompt = prompt_builder(
        topic,
        intent,
        depth,
        diagram_type,
        reasoning=reasoning,
        style=style,
        capabilities=capabilities,
    )
    use_minimal_prompt = False
    if not prompt or not prompt.strip():
        _tech_logger.warning(
            "technical_prompt_empty",
            intent=intent,
            depth=depth,
            diagram_type=diagram_type,
        )
        prompt = TECHNICAL_MINIMAL_PROMPT
        use_minimal_prompt = True
    
    if not use_minimal_prompt:
        # 1. Append RAG context if available
        if rag_context:
            prompt = _append_search_context(prompt, f"--- RAG CONTEXT ---\n{rag_context}\n--- END RAG CONTEXT ---")

        # 2. Append Web Search context
        prompt = _append_search_context(prompt, search_context)

    fallback_triggered = False
    fallback_reason: str | None = None
    best_effort_response: str | None = None
    requested_model = str(kwargs.get("model") or "").strip() or None
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
    ranked_aliases = route_aliases_fn(
        topic,
        intent=intent,
        mode=TECHNICAL_MODE,
        level="technical",
        depth=depth,
        is_pro=is_pro,
        search_api_used=bool(search_context),
    )
    if requested_model:
        primary_alias = requested_model
        fallback_alias = None
        ranked_aliases = [requested_model]
    else:
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
        try:
            call_kwargs = dict(kwargs)
            call_kwargs["temperature"] = TECHNICAL_TEMPERATURE
            call_kwargs.pop("max_tokens", None)
            result = await call_model_fn(
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
        is_valid, reason = validate_technical_response_fn(response, intent)
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
        if not has_complete_ending(response):
            _tech_logger.warning(
                "technical_response_incomplete",
                model=model_alias,
                intent=intent,
                depth=depth,
                response_length=len(response),
            )
            return None
        return response

    response_alias = primary_alias
    response = await _call_and_validate(primary_alias)

    if response is None:
        retry_response = await _call_and_validate(primary_alias)
        if retry_response is not None:
            response = retry_response

    if response is None:
        fallback_triggered = True
        fallback_reason = "primary_failed_no_retry"
        _tech_logger.info(
            "technical_fallback_triggered",
            reason=fallback_reason,
            intent=intent,
            depth=depth,
        )
        if fallback_alias is not None:
            response = await _call_and_validate(fallback_alias)
            response_alias = fallback_alias

    if response is not None and is_low_quality(response):
        quality_retry_alias: str | None = None
        if requested_model:
            quality_retry_alias = None
        elif response_alias in ranked_aliases:
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
