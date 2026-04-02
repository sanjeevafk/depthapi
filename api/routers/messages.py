"""Chat messages endpoint."""

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import check_is_pro, verify_token
from api.repositories.chat_repository import ChatRepository
from config import get_settings
from logging_config import anonymize_text, anonymize_user_id, logger, log_sampled_success
from monitoring import capture_telemetry_event
from services.cache import cache_get, cache_set, cache_set_if_absent
from services.inference import generate_explanation, generate_stream_explanation
from services.llm_client import get_litellm_config_state
from services.llm_errors import LLMUnavailable
from services.rate_limit import enforce_request_controls, estimate_tokens_for_text
from services.message_streaming import (
    build_message_replay_response,
    build_message_stream_response,
)
from services.idempotency import (
    message_idempotency_key,
    compute_age_seconds,
    resolve_started_ts,
)
from utils import (
    DEFAULT_CHAT_MODE,
    PROMPT_MODE_ALIASES,
    SUPPORTED_PROMPT_MODES,
    LEARNING_MODE,
    SOCRATIC_MODE,
    TECHNICAL_MODE,
    normalize_mode,
    normalize_prompt_level,
)

router = APIRouter(tags=["messages"])


def _trusted_proxies_from_settings(config_settings: Any) -> set[str]:
    raw = str(getattr(config_settings, "trusted_proxies", "") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _resolve_client_ip(request: Request, *, trusted_proxies: set[str]) -> str:
    peer_host = (request.client.host if request.client else "") or ""
    if peer_host in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        forwarded_chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        # Use the leftmost forwarded IP (original client) when behind trusted proxy.
        forwarded_ip = forwarded_chain[0] if forwarded_chain else None
        real_ip = (request.headers.get("x-real-ip") or "").strip() or None
        return str(forwarded_ip or real_ip or peer_host or "unknown")

    return str(peer_host or "unknown")


class MessageRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=8000)
    client_generated_id: Optional[str] = None
    assistant_client_id: Optional[str] = None
    mode: Optional[str] = None
    prompt_mode: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    regenerate: bool = False


def _message_cache_key(content: str, mode: str, prompt_mode: str, temperature: float) -> str:
    digest = hashlib.sha256(
        f"{content}\x00{mode}\x00{prompt_mode}\x00{temperature:.2f}".encode("utf-8")
    ).hexdigest()
    return f"knowbear:cache:{digest}"


def _require_uuid(value: Optional[str], field_name: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a UUID") from exc


 


@router.post("/messages")
async def send_message(req: MessageRequest, request: Request, auth_data: dict = Depends(verify_token)):
    request_received = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
    config_state = get_litellm_config_state()
    if not bool(config_state.get("chat_enabled", False)):
        raise LLMUnavailable("Chat is disabled because LiteLLM is not configured correctly.")

    user = auth_data["user"]
    user_id = str(getattr(user, "id", "") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user id is missing")

    content = (req.content or "").strip()
    user_id_hash = anonymize_user_id(user_id)
    content_hash = anonymize_text(content)

    capture_telemetry_event(
        "message_send",
        request_id=request_id,
        user_id_hash=user_id_hash,
        mode=req.mode,
        prompt_mode=req.prompt_mode,
        regenerate=bool(req.regenerate),
    )

    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    client_message_id = _require_uuid(req.client_generated_id, "client_generated_id")
    assistant_client_id = _require_uuid(req.assistant_client_id, "assistant_client_id")

    config_settings = get_settings()
    environment = str(getattr(config_settings, "environment", "") or "").strip().lower()
    is_prod = environment == "production"
    cache_ttl_seconds = max(int(getattr(config_settings, "message_cache_ttl_seconds", 3600)), 1)
    stream_max_seconds = max(int(getattr(config_settings, "stream_max_seconds", 25)), 1)
    if not is_prod:
        stream_max_seconds = max(stream_max_seconds, 60)
    fallback_budget_seconds = max(
        1.0,
        min(float(getattr(config_settings, "stream_fallback_budget_seconds", 6)), float(stream_max_seconds)),
    )
    fallback_timeout_seconds = max(fallback_budget_seconds, 3.0)
    heartbeat_seconds = min(
        max(float(getattr(config_settings, "stream_heartbeat_seconds", 2)), 0.1),
        2,
    )
    raw_start_timeout = float(getattr(config_settings, "stream_start_timeout_seconds", 2))
    idempotency_ttl_seconds = min(
        max(int(getattr(config_settings, "stream_idempotency_ttl_seconds", 90)), 60),
        120,
    )
    idempotency_stale_seconds = max(
        5,
        min(int(getattr(config_settings, "stream_idempotency_stale_seconds", 20)), idempotency_ttl_seconds),
    )
    trusted_proxies = _trusted_proxies_from_settings(config_settings)

    idempotency_key = message_idempotency_key(user_id, client_message_id)
    idempotency_payload = await cache_get(idempotency_key)
    idempotency_claimed = False
    if idempotency_payload:
        status = idempotency_payload.get("status")
        cached_response = idempotency_payload.get("response")
        if status == "completed" and cached_response:
            assistant_message_id = idempotency_payload.get("assistant_message_id")
            replay_mode = idempotency_payload.get("mode") or DEFAULT_CHAT_MODE
            replay_prompt_mode = idempotency_payload.get("prompt_mode") or normalize_prompt_level(None)
            return build_message_replay_response(
                content=str(cached_response),
                message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                mode=replay_mode,
                prompt_mode=replay_prompt_mode,
            )

        if status == "in_progress":
            now_ts = int(time.time())
            started_ts = resolve_started_ts(idempotency_payload, now_ts=now_ts)
            age_seconds = compute_age_seconds(idempotency_payload, now_ts=now_ts)
            if age_seconds < idempotency_stale_seconds:
                raise HTTPException(status_code=409, detail="Duplicate request already in progress.")
            reclaimed = await cache_set(
                idempotency_key,
                {
                    "status": "reclaimed",
                    "reclaimed_at": now_ts,
                    "previous_started_at": started_ts,
                    "message_id": client_message_id,
                },
                ttl=idempotency_ttl_seconds,
            )
            if not reclaimed:
                raise HTTPException(status_code=409, detail="Duplicate request already in progress.")
            idempotency_claimed = True

    is_pro = await check_is_pro(user_id)
    estimated_tokens = estimate_tokens_for_text(content)
    client_ip = _resolve_client_ip(request, trusted_proxies=trusted_proxies)
    await enforce_request_controls(
        user_id=user_id,
        client_ip=client_ip,
        estimated_tokens=estimated_tokens,
        is_pro=is_pro,
    )

    try:
        conversation = await ChatRepository.get_conversation(req.conversation_id, user_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "messages_conversation_fetch_failed",
            error=str(exc),
            request_id=request_id,
            user_id_hash=user_id_hash,
            conversation_id=req.conversation_id,
            retry=bool(req.regenerate),
            sampled=False,
        )
        raise HTTPException(status_code=500, detail="Failed to load conversation") from exc

    selected_mode = normalize_mode(req.mode or conversation.get("mode") or conversation.get("settings", {}).get("mode"))
    if selected_mode not in {LEARNING_MODE, TECHNICAL_MODE, SOCRATIC_MODE}:
        selected_mode = DEFAULT_CHAT_MODE
    if selected_mode == LEARNING_MODE and not is_prod:
        stream_start_timeout_seconds = max(raw_start_timeout, float(stream_max_seconds))
    elif selected_mode == TECHNICAL_MODE:
        stream_max_seconds = max(
            stream_max_seconds,
            int(getattr(config_settings, "technical_stream_max_seconds", 45)),
        )
        technical_start_timeout = float(
            getattr(config_settings, "technical_stream_start_timeout_seconds", max(raw_start_timeout, 6.0))
        )
        technical_cap = max(4.0, min(float(stream_max_seconds) * 0.75, 20.0))
        stream_start_timeout_seconds = min(max(technical_start_timeout, 2.0), technical_cap)
        fallback_budget_seconds = max(fallback_budget_seconds, 4.0)
        fallback_timeout_seconds = max(fallback_budget_seconds, 4.0)
    else:
        cap = 8.0 if is_prod else 15.0
        stream_start_timeout_seconds = min(max(raw_start_timeout, 0.1), cap)

    requested_prompt_mode = PROMPT_MODE_ALIASES.get(req.prompt_mode or "", req.prompt_mode or "")
    stored_prompt_mode = PROMPT_MODE_ALIASES.get(
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
    )
    prompt_mode = normalize_prompt_level(requested_prompt_mode or stored_prompt_mode)
    if prompt_mode not in SUPPORTED_PROMPT_MODES:
        prompt_mode = normalize_prompt_level(None)

    if selected_mode == TECHNICAL_MODE and not is_pro:
        raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")
    request_temperature = max(0.0, min(float(req.temperature), 1.0))
    cache_key = _message_cache_key(
        content=content,
        mode=selected_mode,
        prompt_mode=prompt_mode,
        temperature=request_temperature,
    )
    cached_payload = None if req.regenerate else await cache_get(cache_key)
    cached_response = cached_payload.get("response") if cached_payload else None
    if cached_response and not isinstance(cached_response, str):
        cached_response = str(cached_response)

    now_ts = int(time.time())
    idempotency_record = {
        "status": "in_progress",
        "started_at": now_ts,
        "last_update_ts": now_ts,
        "response_chars": 0,
        "message_id": client_message_id,
        "assistant_client_id": assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }
    idempotency_started_at = now_ts
    if idempotency_claimed:
        reserved = await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)
    else:
        reserved = await cache_set_if_absent(idempotency_key, idempotency_record, idempotency_ttl_seconds)
    if not reserved:
        existing = await cache_get(idempotency_key)
        if existing:
            status = existing.get("status")
            idempotency_response = existing.get("response")
            if status == "completed" and idempotency_response:
                return build_message_replay_response(
                    content=str(idempotency_response),
                    message_id=client_message_id,
                    assistant_message_id=existing.get("assistant_message_id"),
                    mode=existing.get("mode") or selected_mode,
                    prompt_mode=existing.get("prompt_mode") or prompt_mode,
                )
            if status == "in_progress":
                now_ts = int(time.time())
                started_ts = resolve_started_ts(existing, now_ts=now_ts)
                age_seconds = compute_age_seconds(existing, now_ts=now_ts)
                if age_seconds < idempotency_stale_seconds:
                    raise HTTPException(status_code=409, detail="Duplicate request already in progress.")
                await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)
            if status == "failed":
                await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)

    user_metadata = {
        "client_id": client_message_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }
    assistant_metadata = {
        "assistant_client_id": assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    update_payload = {
        "mode": selected_mode,
        "settings": {**(conversation.get("settings") or {}), "mode": selected_mode, "prompt_mode": prompt_mode},
        "updated_at": now_iso,
    }

    _insert_user, _update_conv, _insert_assistant = ChatRepository.batch_insert_message_setup(
        conversation.get("id"),
        content,
        user_metadata,
        assistant_metadata,
        update_payload,
    )

    try:
        user_res, conv_res, assistant_resp = await asyncio.gather(
            asyncio.to_thread(_insert_user),
            asyncio.to_thread(_update_conv),
            asyncio.to_thread(_insert_assistant),
            return_exceptions=True
        )

        for res, name in [(user_res, "user_insert"), (conv_res, "conv_update"), (assistant_resp, "assistant_insert")]:
            if isinstance(res, Exception):
                logger.error(f"messages_{name}_failed", error=str(res), request_id=request_id, user_id_hash=user_id_hash, conversation_id=req.conversation_id, retry=bool(req.regenerate), sampled=False)
                if name != "conv_update":
                    raise res

        assistant_data = cast(list[Dict[str, Any]], assistant_resp.data) if not isinstance(assistant_resp, Exception) and getattr(assistant_resp, "data", None) else []
        assistant_message_id = assistant_data[0]["id"] if assistant_data else None

        await cache_set(
            idempotency_key,
            {
                "status": "in_progress",
                "message_id": client_message_id,
                "assistant_message_id": assistant_message_id,
                "mode": selected_mode,
                "prompt_mode": prompt_mode,
            },
            ttl=idempotency_ttl_seconds,
        )
    except Exception as exc:
        await cache_set(
            idempotency_key,
            {"status": "failed", "message_id": client_message_id},
            ttl=idempotency_ttl_seconds,
        )
        raise HTTPException(status_code=500, detail="Failed to save database records") from exc

    return build_message_stream_response(
        request=request,
        req=req,
        request_id=request_id,
        request_received=request_received,
        user_id=user_id,
        user_id_hash=user_id_hash,
        content=content,
        content_hash=content_hash,
        selected_mode=selected_mode,
        prompt_mode=prompt_mode,
        assistant_message_id=assistant_message_id,
        client_message_id=client_message_id,
        conversation_id=req.conversation_id,
        request_temperature=request_temperature,
        cached_response=cached_response,
        cache_key=cache_key,
        cache_ttl_seconds=cache_ttl_seconds,
        stream_max_seconds=stream_max_seconds,
        stream_start_timeout_seconds=stream_start_timeout_seconds,
        heartbeat_seconds=heartbeat_seconds,
        fallback_timeout_seconds=fallback_timeout_seconds,
        idempotency_key=idempotency_key,
        idempotency_ttl_seconds=idempotency_ttl_seconds,
        idempotency_started_at=idempotency_started_at,
        is_pro=is_pro,
        generate_stream_explanation=generate_stream_explanation,
        generate_explanation=generate_explanation,
        cache_set=cache_set,
        log_context={"mode": selected_mode, "prompt_mode": prompt_mode},
        log_sampled_success_fn=log_sampled_success,
    )
