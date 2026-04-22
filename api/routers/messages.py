"""Chat messages endpoint (compatibility facade)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from auth import verify_token
from services.inference import (
    MODE_SYSTEM_PROMPTS,
    SYSTEM_PROMPT,
    TECHNICAL_MAX_TOKENS,
    generate_explanation,
    generate_stream_explanation,
)

from . import messages_core as _core

router = APIRouter(tags=["messages"])

# Compatibility exports used in tests and existing call sites.
_acquire_conversation_lock = _core._acquire_conversation_lock
_release_conversation_lock = _core._release_conversation_lock
_resolve_client_ip = _core._resolve_client_ip
_idempotency_key = _core._idempotency_key
_message_cache_key = _core._message_cache_key


async def _send_message_handler(
    request: Request,
    auth_data: dict = Depends(verify_token),
) -> StreamingResponse:
    # Ensure patched functions on this module are honored by core execution/tests.
    _core.generate_explanation = generate_explanation
    _core.generate_stream_explanation = generate_stream_explanation
    _core.MODE_SYSTEM_PROMPTS = MODE_SYSTEM_PROMPTS
    _core.SYSTEM_PROMPT = SYSTEM_PROMPT
    _core.TECHNICAL_MAX_TOKENS = TECHNICAL_MAX_TOKENS
    return await _core._send_message_handler(request=request, auth_data=auth_data)


@router.post("/messages")
async def send_message(
    request: Request,
    auth_data: dict = Depends(verify_token),
) -> StreamingResponse:
    return await _core._message_workflow.process_message(
        request=request,
        auth_data=auth_data,
        handler=_send_message_handler,
    )
