"""State-machine style workflow wrapper for `/messages` orchestration."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import Request
from fastapi.responses import StreamingResponse

from api.services.api_key_auth import ApiKeyRecord

T = TypeVar("T")


class MessageWorkflow:
    """Executes message processing through explicit workflow stages."""

    async def run_stage(self, name: str, operation: Callable[[], Awaitable[T]]) -> T:
        started = time.perf_counter()
        try:
            result = await operation()
            logger.debug("message_workflow_stage_ok", stage=name, duration_ms=round((time.perf_counter() - started) * 1000, 2))
            return result
        except Exception:
            logger.debug(
                "message_workflow_stage_failed",
                stage=name,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise

    async def process_message(
        self,
        *,
        request: Request,
        api_key: ApiKeyRecord,
        handler: Callable[[Request, ApiKeyRecord], Awaitable[StreamingResponse]],
    ) -> StreamingResponse:
        async def _execute() -> StreamingResponse:
            return await handler(request, api_key)

        return await self.run_stage("process_message", _execute)
