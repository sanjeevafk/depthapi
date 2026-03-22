"""Shared helpers for SSE streaming."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import orjson

SSE_RESPONSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _normalize_data_lines(data: str) -> list[str]:
    lines = data.splitlines()
    return lines if lines else [""]


def format_sse(event: str, data: str, event_id: int) -> str:
    """Format a single SSE event with id, event, and data fields."""
    lines = _normalize_data_lines(data)
    data_block = "\n".join(f"data: {line}" for line in lines)
    return f"id: {event_id}\nevent: {event}\n{data_block}\n\n"


def format_sse_json(event: str, payload: dict[str, Any], event_id: int) -> str:
    """Format SSE event with JSON payload."""
    return format_sse(event, orjson.dumps(payload).decode("utf-8"), event_id)


@dataclass
class SseEventBuilder:
    event_id: int = 0

    def emit(self, event: str, data: str) -> str:
        self.event_id += 1
        return format_sse(event, data, self.event_id)

    def emit_json(self, event: str, payload: dict[str, Any]) -> str:
        self.event_id += 1
        return format_sse_json(event, payload, self.event_id)
