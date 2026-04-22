"""Shared utility helpers used across multiple service modules."""

from __future__ import annotations

from typing import Any


def extract_usage_dict(usage_obj: object) -> dict[str, int] | None:
    """Extract normalized token usage fields from provider response objects."""
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


def extract_estimated_cost(
    result_obj: object,
    usage: dict[str, int] | None = None,
) -> float | None:
    """Extract estimated cost from response metadata or usage payload."""
    direct_cost = getattr(result_obj, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        return float(direct_cost)

    hidden_params = getattr(result_obj, "_hidden_params", None)
    if isinstance(hidden_params, dict):
        hidden_cost = hidden_params.get("response_cost")
        if isinstance(hidden_cost, (int, float)):
            return float(hidden_cost)

    if isinstance(usage, dict):
        usage_cost = usage.get("cost")
        if isinstance(usage_cost, (int, float)):
            return float(usage_cost)

    return None


def error_text(exc: Exception, *, fallback: str | None = None) -> str:
    """Build a user-safe error string from an exception."""
    text = str(exc).strip()
    if text:
        return text
    if fallback:
        return fallback
    return type(exc).__name__
