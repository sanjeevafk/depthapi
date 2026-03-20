"""Shared helpers for SSE streaming."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import orjson


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
    _lock: Lock = field(default_factory=Lock, repr=False, init=False)

    def emit(self, event: str, data: str) -> str:
        with self._lock:
            self.event_id += 1
            event_id = self.event_id
        return format_sse(event, data, event_id)

    def emit_json(self, event: str, payload: dict[str, Any]) -> str:
        with self._lock:
            self.event_id += 1
            event_id = self.event_id
        return format_sse_json(event, payload, event_id)
