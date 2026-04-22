"""Chat messages endpoint (compatibility facade)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from services.api_key_auth import ApiKeyRecord, verify_api_key
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
gatekeep_message_request = _core.gatekeep_message_request
fetch_conversation_snapshot = _core.fetch_conversation_snapshot
warm_conversation_snapshot = _core.warm_conversation_snapshot
get_supabase_admin = _core.get_supabase_admin
get_settings = _core.get_settings
log_sampled_success = _core.log_sampled_success
cache_set_value = _core.cache_set_value
logger = _core.logger


async def _send_message_handler(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    # Ensure patched functions on this module are honored by core execution/tests.
    _core.generate_explanation = generate_explanation
    _core.generate_stream_explanation = generate_stream_explanation
    _core.MODE_SYSTEM_PROMPTS = MODE_SYSTEM_PROMPTS
    _core.SYSTEM_PROMPT = SYSTEM_PROMPT
    _core.TECHNICAL_MAX_TOKENS = TECHNICAL_MAX_TOKENS
    _core.gatekeep_message_request = gatekeep_message_request
    _core.fetch_conversation_snapshot = fetch_conversation_snapshot
    _core.warm_conversation_snapshot = warm_conversation_snapshot
    _core.get_supabase_admin = get_supabase_admin
    _core.get_settings = get_settings
    _core.log_sampled_success = log_sampled_success
    _core.cache_set_value = cache_set_value
    _core.logger = logger
    return await _core._send_message_handler(request=request, api_key=api_key)


@router.post("/messages")
async def send_message(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    return await _core._message_workflow.process_message(
        request=request,
        api_key=api_key,
        handler=_send_message_handler,
    )
