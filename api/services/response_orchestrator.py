"""Shared SSE response orchestration helpers for message streaming."""

from __future__ import annotations
from typing import Any, AsyncGenerator, AsyncIterable, Iterable

from api.logging_config import logger
from api.repositories.chat_repository import ChatRepository
from services.streaming import SseEventBuilder

SsePayload = dict[str, Any] | str
SseEvent = tuple[str, SsePayload]


class ResponseOrchestrator:
    """Coordinates SSE formatting/streaming and async stream persistence."""

    def format_sse_event(self, event_type: str, data: SsePayload) -> str:
        builder = SseEventBuilder()
        if isinstance(data, dict):
            return builder.emit_json(event_type, data)
        return builder.emit(event_type, data)

    async def build_sse_stream(
        self,
        inference_task: AsyncIterable[SseEvent] | Iterable[SseEvent],
        conversation_id: str,
    ) -> AsyncGenerator[str, None]:
        try:
            if isinstance(inference_task, AsyncIterable):
                async for event_type, payload in inference_task:
                    yield self.format_sse_event(event_type, payload)
                return
            for event_type, payload in inference_task:
                yield self.format_sse_event(event_type, payload)
        except Exception as exc:
            logger.error(
                "response_orchestrator_stream_failed",
                error=str(exc),
                conversation_id=conversation_id,
            )
            raise

    async def persist_message_stream(
        self,
        token_buffer: str,
        conversation_id: str,
        *,
        request_id: str | None = None,
        user_id_hash: str | None = None,
        retry: bool = False,
    ) -> None:
        try:
            await ChatRepository.update_assistant_message(conversation_id, token_buffer)
        except Exception as exc:
            logger.error(
                "messages_assistant_update_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                message_id=conversation_id,
                retry=retry,
                sampled=False,
            )
