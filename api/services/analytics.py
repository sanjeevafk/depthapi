from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from auth import get_supabase_admin
from logging_config import logger
from monitoring import redact_pii


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def resolve_time_range(start: str | None, end: str | None, default_days: int = 7) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    parsed_start = _parse_iso(start)
    parsed_end = _parse_iso(end)
    resolved_end = parsed_end or now
    resolved_start = parsed_start or (resolved_end - timedelta(days=default_days))
    if resolved_start > resolved_end:
        resolved_start, resolved_end = resolved_end, resolved_start
    return resolved_start, resolved_end


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def record_llm_request(payload: dict[str, Any]) -> None:
    supabase = get_supabase_admin()
    if not supabase:
        return
    sanitized = redact_pii(payload)
    sanitized = {key: value for key, value in sanitized.items() if value is not None}
    try:
        await asyncio.to_thread(lambda: supabase.table("llm_requests").insert(sanitized).execute())
    except Exception as exc:
        logger.error("analytics_llm_insert_failed", error=str(exc))


def build_llm_request_payload(
    *,
    request_id: str | None,
    user_id: str | None,
    conversation_id: str | None,
    model_alias: str | None,
    model_name: str | None,
    provider: str | None,
    mode: str | None,
    status: str | None,
    token_usage: dict[str, Any] | None,
    estimated_cost_usd: Any,
    latency_ms: Any,
    model_inference_ms: Any,
    stream_duration_ms: Any,
    error_type: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    tokens_prompt = _coerce_int(token_usage.get("prompt_tokens")) if isinstance(token_usage, dict) else None
    tokens_completion = _coerce_int(token_usage.get("completion_tokens")) if isinstance(token_usage, dict) else None
    tokens_total = _coerce_int(token_usage.get("total_tokens")) if isinstance(token_usage, dict) else None
    return {
        "request_id": request_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "model_alias": model_alias,
        "model_name": model_name,
        "provider": provider,
        "mode": mode,
        "status": status,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "tokens_total": tokens_total,
        "estimated_cost_usd": _coerce_float(estimated_cost_usd),
        "latency_ms": _coerce_float(latency_ms),
        "model_inference_ms": _coerce_float(model_inference_ms),
        "stream_duration_ms": _coerce_float(stream_duration_ms),
        "error_type": error_type,
        "error_message": error_message,
    }
