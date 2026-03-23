"""Shared idempotency helpers."""

from __future__ import annotations

import hashlib
import time
from typing import Any


def query_stream_idempotency_key(scope: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{scope}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"knowbear:query_stream:idempotency:{digest}"


def message_idempotency_key(user_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"knowbear:idempotency:{digest}"


def resolve_started_ts(payload: dict[str, Any] | None, *, now_ts: int | None = None) -> int:
    if now_ts is None:
        now_ts = int(time.time())
    if not payload:
        return now_ts
    started_at = payload.get("started_at")
    if isinstance(started_at, (int, float)):
        return int(started_at)
    return now_ts


def compute_age_seconds(payload: dict[str, Any] | None, *, now_ts: int | None = None) -> int:
    if now_ts is None:
        now_ts = int(time.time())
    if payload:
        last_update_ts = payload.get("last_update_ts")
        if isinstance(last_update_ts, (int, float)):
            return max(now_ts - int(last_update_ts), 0)
    started_ts = resolve_started_ts(payload, now_ts=now_ts)
    return max(now_ts - started_ts, 0)


def compute_retry_after_ms(stale_seconds: int, age_seconds: int) -> int:
    return max(250, int(max(stale_seconds - age_seconds, 0) * 1000))
