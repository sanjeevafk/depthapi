"""Shared streaming helpers for query/messages orchestration."""

from __future__ import annotations

import time
from typing import Any, Callable, Awaitable

from logging_config import logger


async def close_stream(stream) -> None:
    close_fn = getattr(stream, "aclose", None)
    if close_fn:
        try:
            await close_fn()
        except Exception:
            pass


def compute_fallback_timeout(
    *,
    start_time: float,
    stream_max_seconds: float,
    fallback_timeout_seconds: float,
    request_id: str,
    user_id_hash: str | None,
    log_context: dict[str, Any],
) -> tuple[float | None, float | None, bool]:
    elapsed = time.perf_counter() - start_time
    remaining = max(float(stream_max_seconds) - elapsed, 0.0)
    if remaining <= 0.5:
        logger.warning(
            "streaming_fallback_skipped_remaining_time",
            request_id=request_id,
            user_id_hash=user_id_hash,
            remaining_seconds=round(remaining, 2),
            fallback_skipped_remaining_time=True,
            **log_context,
        )
        return None, None, True

    cap = min(fallback_timeout_seconds, max(0.5, remaining - 0.5))
    logger.info(
        "streaming_fallback_timeout_capped",
        request_id=request_id,
        user_id_hash=user_id_hash,
        fallback_timeout_cap_seconds=round(cap, 2),
        **log_context,
    )
    return cap, cap, False


async def update_idempotency_progress(
    *,
    cache_set: Callable[..., Awaitable[bool]],
    key: str,
    ttl: int,
    started_at: int,
    response_chars: int,
    record_fields: dict[str, Any],
    last_update_time: float,
    min_interval_seconds: float = 2.0,
) -> float:
    now = time.perf_counter()
    if now - last_update_time < min_interval_seconds:
        return last_update_time
    now_ts = int(time.time())
    payload = {
        "status": "in_progress",
        "started_at": started_at,
        "last_update_ts": now_ts,
        "response_chars": response_chars,
    }
    payload.update(record_fields)
    await cache_set(key, payload, ttl=ttl)
    return now
