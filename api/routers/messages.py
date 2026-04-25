"""Chat messages endpoint (compatibility facade)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from api.services.api_key_auth import ApiKeyRecord, verify_api_key

from . import messages_core as _core

router = APIRouter(tags=["messages"])


async def _send_message_handler(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    """Delegate to messages_core handler.
    
    Note: Module-level imports above ensure tests can monkeypatch via
    api.services.inference or api.routers.messages_core directly.
    Per-request patching was removed as it is unnecessary (both modules
    import from the same sources) and caused performance/thread-safety issues.
    """
    return await _core.send_message_handler(request=request, api_key=api_key)


@router.post("/messages")
async def send_message(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    return await _core.message_workflow.process_message(
        request=request,
        api_key=api_key,
        handler=_send_message_handler,
    )
