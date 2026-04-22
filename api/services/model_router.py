"""Thin model-routing facade over inference routing heuristics.

Responsibilities:
- Score feature complexity from query/intent/mode.
- Resolve ordered model aliases for execution and fallbacks.
- Keep router-facing API stable while routing internals evolve.
"""

from __future__ import annotations

from services.inference_routing import (
    _effective_alias_chain,
    extract_features,
    route_model_aliases,
    score_model,
)


class ModelRouter:
    def route_model(self, query: str, intent: str, mode: str, *, level: str = "eli10", is_pro: bool = False, search_api_used: bool = False) -> str:
        aliases = self.route_aliases(
            query,
            intent=intent,
            mode=mode,
            level=level,
            is_pro=is_pro,
            search_api_used=search_api_used,
        )
        return aliases[0] if aliases else "default-fast"

    def route_aliases(
        self,
        query: str,
        *,
        intent: str | None,
        mode: str,
        level: str,
        depth: str | None = None,
        is_pro: bool = False,
        search_api_used: bool = False,
    ) -> list[str]:
        features = extract_features(query, mode=mode, level=level, intent=intent, depth=depth)
        aliases = route_model_aliases(
            query,
            mode=mode,
            level=level,
            intent=intent,
            depth=depth,
            is_pro=is_pro,
            search_api_used=search_api_used,
        )
        complexity = float(features.get("complexity", 0.0) or 0.0)
        return _effective_alias_chain(aliases, complexity=complexity)

    def score_model(self, query: str, features: dict[str, float], mode: str) -> dict[str, float]:
        _ = query
        aliases = (
            "learn-groq-llama8b",
            "learn-gemini-flash",
            "technical-gemini-pro",
            "technical-groq-llama8b",
            "socratic-gemini-pro",
        )
        normalized = {
            "complexity": float(features.get("complexity", 0.0) or 0.0),
            "reasoning": float(features.get("reasoning", 0.0) or 0.0),
            "explanation": float(features.get("explanation", 0.0) or 0.0),
            "latency_priority": float(features.get("latency_priority", 0.0) or 0.0),
        }
        return {alias: score_model(normalized, alias, mode=mode) for alias in aliases}
