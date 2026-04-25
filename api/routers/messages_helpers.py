"""Helper utilities for messages router."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from monitoring import capture_telemetry_event
from api.services.conversation_lock_manager import ConversationLockManager
from api.services.message_dispatcher import MessageDispatcher
from api.services.request_validator import RequestValidator
from api.utils import SOCRATIC_MODE, TECHNICAL_MODE

_request_validator = RequestValidator()
_message_dispatcher = MessageDispatcher()

# Singleton lock manager replaces inline _CONVERSATION_LOCKS dict.
# This consolidates lock state and eliminates duplication with
# api/services/conversation_lock_manager.py.
_lock_manager = ConversationLockManager(max_locks=10000, ttl_seconds=600)


class MessageRequest(BaseModel):
    """Validated payload for `/messages` requests."""

    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=8000)
    client_generated_id: Optional[str] = None
    assistant_client_id: Optional[str] = None
    mode: Optional[str] = None
    prompt_mode: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    regenerate: bool = False


async def acquire_conversation_lock(conversation_id: str, timeout_seconds: float = 1.0) -> bool:
    """Acquire conversation lock via the shared ConversationLockManager."""
    return await _lock_manager.acquire(conversation_id, timeout_seconds=timeout_seconds)


def release_conversation_lock(conversation_id: str) -> None:
    """Release conversation lock via the shared ConversationLockManager."""
    _lock_manager.release(conversation_id)


def trusted_proxies_from_settings(config_settings: Any) -> set[str]:
    raw = str(getattr(config_settings, "trusted_proxies", "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def resolve_client_ip(request: Request, *, trusted_proxies: set[str]) -> str:
    peer_host = (request.client.host if request.client else "") or ""
    if peer_host in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        forwarded_chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        forwarded_ip = forwarded_chain[0] if forwarded_chain else None
        real_ip = (request.headers.get("x-real-ip") or "").strip() or None
        return str(forwarded_ip or real_ip or peer_host or "unknown")

    return str(peer_host or "unknown")


async def ingress_dedupe_check(message_id: str, ttl_seconds: float = 3.0) -> bool:
    return await _request_validator.check_deduplication(message_id, ttl_seconds=ttl_seconds)


async def ingress_dedupe_clear(message_id: str) -> None:
    await _request_validator.clear_deduplication(message_id)


async def capture_telemetry_async(event: str, **payload: Any) -> None:
    await asyncio.to_thread(capture_telemetry_event, event, **payload)


def message_cache_key(
    content: str,
    mode: str,
    prompt_mode: str,
    temperature: float,
    model_alias: str,
    system_prompt: str,
    context_signature: str = "",
    intent_type: str = "",
    intent_payload: str = "",
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> str:
    digest = hashlib.sha256(
        f"{conversation_id or ''}\x00{user_id or ''}\x00{system_prompt}\x00{context_signature}\x00{content}\x00{temperature:.2f}\x00{model_alias}\x00{mode}\x00{prompt_mode}\x00{intent_type}\x00{intent_payload}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"depthapi:cache:{digest}"


def ack_response(mode: str) -> str:
    if mode == TECHNICAL_MODE:
        return "Understood. Share the next technical detail or question when ready."
    if mode == SOCRATIC_MODE:
        return "Got it. Whenever you're ready, share your next thought."
    return "Got it. Let me know what you'd like to explore next."


def idempotency_key(user_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"depthapi:idempotency:{digest}"


def bad_request(detail: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"type": "bad_request", "message": detail, "retry_allowed": False},
    )


def auth_required(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"type": "auth_required", "message": detail, "retry_allowed": False},
    )


def require_uuid(value: Optional[str], field_name: str) -> str:
    try:
        return _request_validator.require_uuid(value, field_name)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


def validate_message_boundary(payload: Any) -> tuple[str, str | None]:
    result = _request_validator.validate_message_request(payload)
    if not result.ok:
        raise bad_request(str(result.error_message or "Invalid request payload"))
    return result.content, result.normalized_mode


def build_replay_response(
    *,
    content: str,
    message_id: str,
    assistant_message_id: Optional[str],
    mode: str,
    prompt_mode: str,
):
    return _message_dispatcher.dispatch_normal_message(
        content=content,
        message_id=message_id,
        assistant_message_id=assistant_message_id,
        mode=mode,
        prompt_mode=prompt_mode,
    )


def final_fallback_message(mode: str) -> str:
    mode_label = "response"
    if mode == TECHNICAL_MODE:
        mode_label = "technical response"
    elif mode == SOCRATIC_MODE:
        mode_label = "socratic response"
    return (
        f"Unable to generate a complete {mode_label} right now due to a transient timeout. "
        "Please retry in a moment."
    )
