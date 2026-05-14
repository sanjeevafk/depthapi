from __future__ import annotations

import math
from typing import Any, Literal

import orjson


MessageMode = Literal["learn", "chat", "summary"]
MESSAGE_MODES: set[str] = {"learn", "chat", "summary"}


def normalize_mode(mode: str | None) -> MessageMode:
    """Normalize and validate incoming message mode."""
    if not mode:
        return "chat"
    normalized = str(mode).strip().lower()
    if normalized in MESSAGE_MODES:
        return normalized  # type: ignore[return-value]
    raise ValueError("invalid mode")


def safe_number(value: Any, *, default: float | int | None = None) -> float | int | None:
    """Safely coerce a numeric input, returning a default on invalid values."""
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(num) or math.isinf(num):
        return default
    if num.is_integer():
        return int(num)
    return num


def safe_json_parse(raw: str | bytes | bytearray) -> Any | None:
    """Safely parse a JSON payload from bytes/string-like values."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            payload = bytes(raw)
        else:
            payload = str(raw).encode("utf-8")
        return orjson.loads(payload)
    except (orjson.JSONDecodeError, TypeError, ValueError, UnicodeEncodeError):
        return None


# Backward-compatible aliases (Phase 1 quick win migration safety).
normalizeMode = normalize_mode
safeNumber = safe_number
safeJsonParse = safe_json_parse
