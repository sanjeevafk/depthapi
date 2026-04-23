"""Message response dispatch helpers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

from api.services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS


class MessageDispatcher:
    """Dispatches message responses to the correct streaming strategy."""

    async def dispatch(
        self,
        *,
        streaming: bool,
        stream_factory: Callable[[], AsyncGenerator[str, None]],
        content: str | None = None,
        message_id: str | None = None,
        assistant_message_id: str | None = None,
        mode: str = "chat",
        prompt_mode: str = "default",
    ) -> StreamingResponse:
        if streaming:
            return self.dispatch_streaming_message(stream_factory)
        return self.dispatch_normal_message(
            content=content or "",
            message_id=message_id or "",
            assistant_message_id=assistant_message_id,
            mode=mode,
            prompt_mode=prompt_mode,
        )

    def dispatch_normal_message(
        self,
        *,
        content: str,
        message_id: str,
        assistant_message_id: str | None,
        mode: str,
        prompt_mode: str,
    ) -> StreamingResponse:
        async def replay_generator() -> AsyncGenerator[str, None]:
            builder = SseEventBuilder()
            meta_payload = {
                "assistant_message_id": assistant_message_id,
                "mode": mode,
                "prompt_mode": prompt_mode,
                "message_id": message_id,
                "replay": True,
            }
            yield builder.emit_json("meta", meta_payload)
            for index in range(0, len(content), 400):
                payload: dict[str, Any] = {"delta": content[index : index + 400]}
                if assistant_message_id:
                    payload["assistant_message_id"] = assistant_message_id
                yield builder.emit_json("delta", payload)
            yield builder.emit("done", "[DONE]")

        return StreamingResponse(
            replay_generator(),
            media_type="text/event-stream",
            headers=SSE_RESPONSE_HEADERS,
        )

    def dispatch_streaming_message(
        self,
        stream_factory: Callable[[], AsyncGenerator[str, None]],
    ) -> StreamingResponse:
        return StreamingResponse(
            stream_factory(),
            media_type="text/event-stream",
            headers=SSE_RESPONSE_HEADERS,
        )

    def dispatch_mode_specific(
        self,
        *,
        mode: str,
        stream_factory: Callable[[], AsyncGenerator[str, None]],
        normal_payload: dict[str, Any],
    ) -> StreamingResponse:
        stream_modes = {"chat", "summary", "technical", "socratic", "learn"}
        if mode in stream_modes:
            return self.dispatch_streaming_message(stream_factory)
        return self.dispatch_normal_message(
            content=str(normal_payload.get("content") or ""),
            message_id=str(normal_payload.get("message_id") or ""),
            assistant_message_id=normal_payload.get("assistant_message_id"),
            mode=str(normal_payload.get("mode") or mode),
            prompt_mode=str(normal_payload.get("prompt_mode") or "default"),
        )
