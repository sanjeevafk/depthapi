from __future__ import annotations

import math
from typing import Any, Literal

import orjson


MessageMode = Literal["learn", "chat", "summary"]
MESSAGE_MODES: set[str] = {"learn", "chat", "summary"}


def normalizeMode(mode: str | None) -> MessageMode:
    if not mode:
        return "chat"
    normalized = str(mode).strip().lower()
    if normalized in MESSAGE_MODES:
        return normalized  # type: ignore[return-value]
    raise ValueError("invalid mode")


def safeNumber(value: Any, *, default: float | int | None = None) -> float | int | None:
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


def safeJsonParse(raw: str | bytes | bytearray) -> Any | None:
    try:
        if isinstance(raw, (bytes, bytearray)):
            payload = bytes(raw)
        else:
            payload = str(raw).encode("utf-8")
        return orjson.loads(payload)
    except Exception:
        return None
