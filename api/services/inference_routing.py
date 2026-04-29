"""Routing policy helpers for inference."""

from __future__ import annotations

import re
from typing import TypedDict

from api.logging_config import logger
from api.services.inference_classifier import IntentClassifier as _IntentClassifier
from api.utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE
from api.services.inference_constants import (
    MODEL_PROFILES,
    COST_PENALTY,
    LEARNING_MODEL_SIMPLE,
    TECHNICAL_MODEL_PRIMARY,
    TECHNICAL_MODEL_FALLBACK,
    LEARNING_DETAILED_LEVELS,
    LEARN_GEMINI_FLASH_ALIAS,
    LEARN_GROQ_FAST_ALIAS,
    LEARN_OPENROUTER_FALLBACK_ALIAS,
    TECH_GEMINI_FLASH_ALIAS,
    TECH_OPENROUTER_ALIAS,
    TECH_GROQ_FAST_ALIAS,
    TECH_GEMINI_PRO_ALIAS,
    TECH_CEREBRAS_GLM_ALIAS,
    SOCRATIC_OPENROUTER_ALIAS,
    SOCRATIC_CEREBRAS_ALIAS,
    SOCRATIC_GEMINI_ALIAS,
    SOCRATIC_GROQ_ALIAS,
    LATENCY_KEYWORDS,
    COMPLEXITY_KEYWORDS,
    REASONING_KEYWORDS,
    EXPLANATION_KEYWORDS,
)

_classifier_shim = _IntentClassifier()


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
    lowered = (query or "").lower().strip()
    resolved_intent = intent
    resolved_depth = depth
    if not resolved_intent or not resolved_depth:
        try:
            classification = _classifier_shim.detect_intent_and_depth(query)
            resolved_intent = resolved_intent or classification.get("intent", "explain")
            resolved_depth = resolved_depth or classification.get("depth", "medium")
        except Exception as exc:
            logger.debug("intent_depth_classification_failed", error=str(exc))
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
    profile = MODEL_PROFILES.get(model_alias, MODEL_PROFILES[LEARNING_MODEL_SIMPLE])
    score = 0.0
    for feature_name, value in features.items():
        score += float(value if isinstance(value, (int, float)) else 0.0) * profile.get(feature_name, 0.0)
    score -= COST_PENALTY.get(model_alias, 0.0)

    if mode == TECHNICAL_MODE and model_alias == TECHNICAL_MODEL_PRIMARY:
        score += 0.15
    if mode == LEARNING_MODE and model_alias == LEARNING_MODEL_SIMPLE:
        score += 0.06

    return score


def _token_count(query: str) -> int:
    return len((query or "").strip().split())


def _looks_simple_explanation(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in ("what is", "explain", "define"))


def _looks_freshness_query(query: str) -> bool:
    lowered = (query or "").lower()
    markers = ("latest", "today", "current", "recent", "news", "update")
    return any(marker in lowered for marker in markers)


def _looks_programming_query(query: str) -> bool:
    lowered = (query or "").lower()
    markers = (
        "api",
        "pagination",
        "endpoint",
        "database",
        "sql",
        "python",
        "javascript",
        "typescript",
        "bug",
        "algorithm",
        "function",
        "react",
        "fastapi",
        "code",
    )
    return any(marker in lowered for marker in markers)


def _looks_math_query(query: str) -> bool:
    lowered = (query or "").lower()
    markers = (
        "math",
        "equation",
        "solve",
        "integral",
        "derivative",
        "calculus",
        "algebra",
        "proof",
        "theorem",
        "matrix",
        "probability",
    )
    return any(marker in lowered for marker in markers)


def _looks_reasoning_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in ("why", "how", "prove", "reason", "derive"))


def _is_cerebras_alias(alias: str) -> bool:
    return "cerebras" in (alias or "").lower()


def _effective_alias_chain(aliases: list[str], *, complexity: float) -> list[str]:
    chain: list[str] = []
    for alias in aliases:
        if _is_cerebras_alias(alias) and complexity < 0.8:
            continue
        if alias not in chain:
            chain.append(alias)
    return chain


def route_model_aliases(
    query: str,
    *,
    mode: str,
    level: str,
    intent: str | None = None,
    depth: str | None = None,
    is_pro: bool = False,
    search_api_used: bool = False,
) -> list[str]:
    """Route a query to an ordered alias chain based on mode, intent, and depth."""
    features = extract_features(
        query,
        mode=mode,
        level=level,
        intent=intent,
        depth=depth,
    )
    complexity = float(features.get("complexity", 0.0) or 0.0)
    latency_priority = float(features.get("latency_priority", 0.0) or 0.0)
    query_tokens = _token_count(query)
    is_simple_explain = _looks_simple_explanation(query)
    is_freshness = _looks_freshness_query(query)
    is_programming = _looks_programming_query(query)
    is_math = _looks_math_query(query)
    is_reasoning = _looks_reasoning_query(query)
    prefers_low_latency = latency_priority >= 0.72 or query_tokens < 10

    aliases: list[str]

    if mode == LEARNING_MODE:
        if is_freshness:
            aliases = [LEARN_GEMINI_FLASH_ALIAS, LEARN_GROQ_FAST_ALIAS, LEARN_OPENROUTER_FALLBACK_ALIAS]
        elif query_tokens < 8 or latency_priority >= 0.8 or complexity < 0.3:
            aliases = [LEARN_GROQ_FAST_ALIAS, LEARN_GEMINI_FLASH_ALIAS, LEARN_OPENROUTER_FALLBACK_ALIAS]
        elif is_simple_explain and complexity < 0.5:
            aliases = [LEARN_GEMINI_FLASH_ALIAS, LEARN_GROQ_FAST_ALIAS, LEARN_OPENROUTER_FALLBACK_ALIAS]
        else:
            aliases = [LEARN_GEMINI_FLASH_ALIAS, LEARN_GROQ_FAST_ALIAS, LEARN_OPENROUTER_FALLBACK_ALIAS]
    elif mode == TECHNICAL_MODE:
        if prefers_low_latency and complexity < 0.85:
            aliases = [
                TECH_GROQ_FAST_ALIAS,
                TECH_GEMINI_FLASH_ALIAS,
                TECH_GEMINI_PRO_ALIAS,
                TECH_OPENROUTER_ALIAS,
            ]
            if is_pro and complexity >= 0.8:
                aliases.append(TECH_CEREBRAS_GLM_ALIAS)
        elif is_math and complexity >= 0.6:
            aliases = [TECH_GEMINI_PRO_ALIAS]
            if is_pro and complexity >= 0.8:
                aliases.append(TECH_CEREBRAS_GLM_ALIAS)
            aliases.extend([TECH_GROQ_FAST_ALIAS, TECH_OPENROUTER_ALIAS])
        elif is_math and complexity < 0.4:
            aliases = [TECH_GEMINI_FLASH_ALIAS, TECH_GROQ_FAST_ALIAS, TECH_OPENROUTER_ALIAS]
        elif is_programming or search_api_used:
            if complexity < 0.7:
                aliases = [TECH_GROQ_FAST_ALIAS, TECH_GEMINI_FLASH_ALIAS, TECH_GEMINI_PRO_ALIAS, TECH_OPENROUTER_ALIAS]
            else:
                aliases = [TECH_GEMINI_PRO_ALIAS, TECH_GROQ_FAST_ALIAS, TECH_OPENROUTER_ALIAS]
            if is_pro and complexity >= 0.8:
                aliases.append(TECH_CEREBRAS_GLM_ALIAS)
        else:
            if complexity < 0.65:
                aliases = [TECH_GROQ_FAST_ALIAS, TECH_GEMINI_FLASH_ALIAS, TECH_GEMINI_PRO_ALIAS, TECH_OPENROUTER_ALIAS]
            else:
                aliases = [TECH_GEMINI_PRO_ALIAS, TECH_GROQ_FAST_ALIAS, TECH_OPENROUTER_ALIAS]
            if is_pro and complexity >= 0.8:
                if aliases[0] == TECH_GEMINI_PRO_ALIAS:
                    aliases.insert(1, TECH_CEREBRAS_GLM_ALIAS)
                else:
                    aliases.append(TECH_CEREBRAS_GLM_ALIAS)
    else:
        if prefers_low_latency and complexity < 0.8:
            aliases = [SOCRATIC_OPENROUTER_ALIAS, SOCRATIC_GROQ_ALIAS, SOCRATIC_GEMINI_ALIAS]
        else:
            aliases = [SOCRATIC_OPENROUTER_ALIAS, SOCRATIC_GEMINI_ALIAS, SOCRATIC_GROQ_ALIAS]
        if is_reasoning and complexity >= 0.8 and is_pro:
            aliases.insert(1, SOCRATIC_CEREBRAS_ALIAS)

    deduped: list[str] = []
    for alias in aliases:
        if alias not in deduped:
            deduped.append(alias)
    return deduped


def _technical_route(
    query: str,
    *,
    intent: str,
    depth: str,
    is_pro: bool,
    search_api_used: bool,
) -> tuple[str, str]:
    features = extract_features(query, mode=TECHNICAL_MODE, level="technical_depth", intent=intent, depth=depth)
    aliases = route_model_aliases(
        query,
        mode=TECHNICAL_MODE,
        level="technical_depth",
        intent=intent,
        depth=depth,
        is_pro=is_pro,
        search_api_used=search_api_used,
    )
    complexity = float(features.get("complexity", 0.0) or 0.0)
    chain = _effective_alias_chain(aliases, complexity=complexity)
    primary = chain[0] if chain else TECHNICAL_MODEL_PRIMARY
    fallback = chain[1] if len(chain) > 1 else TECHNICAL_MODEL_FALLBACK
    return primary, fallback


def _learning_model_for_level(level: str) -> str:
    if level in LEARNING_DETAILED_LEVELS:
        return LEARN_GEMINI_FLASH_ALIAS
    return LEARN_GEMINI_FLASH_ALIAS
