"""Chat messages endpoint."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import check_is_pro, get_supabase_admin, verify_token
from config import get_settings
from logging_config import logger
from services.cache import cache_get, cache_set
from services.ensemble import ensemble_stream_generate
from services.inference import generate_stream_explanation
from services.rate_limit import check_rate_limit
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


class MessageRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=8000)
    client_generated_id: Optional[str] = None
    assistant_client_id: Optional[str] = None
    mode: Optional[str] = None
    prompt_mode: Optional[str] = None


def _message_cache_key(content: str, mode: str, prompt_mode: str) -> str:
    digest = hashlib.sha256(f"{content}\x00{mode}\x00{prompt_mode}".encode("utf-8")).hexdigest()
    return f"knowbear:cache:{digest}"


@router.post("/messages")
async def send_message(req: MessageRequest, request: Request, auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    config_settings = get_settings()
    max_requests = max(int(getattr(config_settings, "message_rate_limit_max", 30)), 1)
    window_seconds = max(int(getattr(config_settings, "message_rate_limit_window_seconds", 60)), 1)
    cache_ttl_seconds = max(int(getattr(config_settings, "message_cache_ttl_seconds", 3600)), 1)

    requester_id = f"user:{getattr(user, 'id', '')}" if getattr(user, "id", None) else ""
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_identifier = requester_id or f"ip:{client_ip}"
    rate_limit_result = await check_rate_limit(
        identifier=rate_limit_identifier,
        limit=max_requests,
        window_seconds=window_seconds,
    )
    if not rate_limit_result.allowed:
        retry_after = max(rate_limit_result.retry_after, 1)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached. Please wait {retry_after} seconds and try again.",
            headers={"Retry-After": str(retry_after)},
        )

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        conversation_resp = await asyncio.to_thread(
            supabase.table("conversations")
            .select("id, user_id, mode, settings")
            .eq("id", req.conversation_id)
            .eq("user_id", user.id)
            .single()
            .execute
        )
        if not getattr(conversation_resp, "data", None):
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation = cast(Dict[str, Any], conversation_resp.data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("messages_conversation_fetch_failed", error=str(exc), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to load conversation") from exc

    selected_mode = normalize_mode(req.mode or conversation.get("mode") or conversation.get("settings", {}).get("mode"))
    if selected_mode not in {LEARNING_MODE, TECHNICAL_MODE, SOCRATIC_MODE}:
        selected_mode = DEFAULT_CHAT_MODE

    requested_prompt_mode = PROMPT_MODE_ALIASES.get(req.prompt_mode or "", req.prompt_mode or "")
    stored_prompt_mode = PROMPT_MODE_ALIASES.get(
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
    )
    prompt_mode = normalize_prompt_level(requested_prompt_mode or stored_prompt_mode)
    if prompt_mode not in SUPPORTED_PROMPT_MODES:
        prompt_mode = normalize_prompt_level(None)

    is_pro = await check_is_pro(user.id)
    if selected_mode == TECHNICAL_MODE and not is_pro:
        selected_mode = DEFAULT_CHAT_MODE
    cache_key = _message_cache_key(content=content, mode=selected_mode, prompt_mode=prompt_mode)
    cached_payload = await cache_get(cache_key)
    cached_response = cached_payload.get("response") if cached_payload else None
    if cached_response and not isinstance(cached_response, str):
        cached_response = str(cached_response)

    user_metadata = {
        "client_id": req.client_generated_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }

    try:
        await asyncio.to_thread(
            supabase.table("messages")
            .insert(
                {
                    "conversation_id": conversation.get("id"),
                    "role": "user",
                    "content": content,
                    "metadata": user_metadata,
                }
            )
            .execute
        )
    except Exception as exc:
        logger.error("messages_user_insert_failed", error=str(exc), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to save user message") from exc

    now_iso = datetime.now(timezone.utc).isoformat()
    update_payload = {
        "mode": selected_mode,
        "settings": {**(conversation.get("settings") or {}), "mode": selected_mode, "prompt_mode": prompt_mode},
        "updated_at": now_iso,
    }
    try:
        await asyncio.to_thread(
            supabase.table("conversations").update(update_payload).eq("id", conversation.get("id")).execute
        )
    except Exception as exc:
        logger.warning("messages_conversation_update_failed", error=str(exc), conversation_id=req.conversation_id)

    assistant_metadata = {
        "assistant_client_id": req.assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }

    try:
        assistant_resp = await asyncio.to_thread(
            supabase.table("messages")
            .insert(
                {
                    "conversation_id": conversation.get("id"),
                    "role": "assistant",
                    "content": "",
                    "metadata": assistant_metadata,
                }
            )
            .execute
        )
        assistant_data = cast(list[Dict[str, Any]], assistant_resp.data) if assistant_resp.data else []
        assistant_message_id = assistant_data[0]["id"] if assistant_data else None
    except Exception as exc:
        logger.error("messages_assistant_insert_failed", error=str(exc), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to start assistant message") from exc

    async def event_generator():
        start_time = time.perf_counter()
        full_content = ""
        sent_id = False
        first_token_ms = None
        last_chunk_time = None
        total_chunk_interval_ms = 0.0
        chunk_count = 0
        chunk_size = 400
        generation_ms = None

        def record_chunk():
            nonlocal first_token_ms, last_chunk_time, total_chunk_interval_ms, chunk_count
            now = time.perf_counter()
            if first_token_ms is None:
                first_token_ms = (now - start_time) * 1000
            if last_chunk_time is not None:
                total_chunk_interval_ms += (now - last_chunk_time) * 1000
            last_chunk_time = now
            chunk_count += 1

        try:
            if assistant_message_id:
                yield f"data: {json.dumps({'assistant_message_id': assistant_message_id, 'mode': selected_mode, 'prompt_mode': prompt_mode})}\n\n"
                sent_id = True

            if cached_response:
                logger.info("messages_cache_hit", conversation_id=req.conversation_id, cache_key=cache_key)
                full_content = cached_response
                for index in range(0, len(cached_response), chunk_size):
                    chunk = cached_response[index : index + chunk_size]
                    record_chunk()
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"
                yield "data: [DONE]\n\n"
                return

            generation_start = time.perf_counter()
            generator = (
                ensemble_stream_generate(
                    content,
                    prompt_mode,
                    mode=selected_mode,
                    use_premium=is_pro,
                )
                if selected_mode == LEARNING_MODE
                else generate_stream_explanation(
                    content,
                    prompt_mode,
                    mode=selected_mode,
                    regenerate=False,
                )
            )
            async for chunk in generator:
                full_content += chunk
                record_chunk()
                payload = {"delta": chunk}
                if not sent_id and assistant_message_id:
                    payload["assistant_message_id"] = assistant_message_id
                    sent_id = True
                yield f"data: {json.dumps(payload)}\n\n"
            generation_ms = (time.perf_counter() - generation_start) * 1000

            if full_content.strip():
                await cache_set(cache_key, {"response": full_content}, ttl=cache_ttl_seconds)

            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.error("messages_stream_failed", error=str(exc), conversation_id=req.conversation_id)
            yield f"data: {json.dumps({'error': 'Streaming failed'})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            total_ms = (time.perf_counter() - start_time) * 1000
            avg_chunk_interval_ms = None
            if chunk_count > 1:
                avg_chunk_interval_ms = total_chunk_interval_ms / (chunk_count - 1)
            logger.info(
                "messages_latency",
                mode=selected_mode,
                prompt_mode=prompt_mode,
                total_ms=round(total_ms, 2),
                first_token_ms=round(first_token_ms, 2) if first_token_ms is not None else None,
                avg_chunk_interval_ms=round(avg_chunk_interval_ms, 2) if avg_chunk_interval_ms is not None else None,
                chunk_count=chunk_count,
                chunk_size=chunk_size,
                content_chars=len(full_content),
                is_pro=is_pro,
                generation_ms=round(generation_ms, 2) if generation_ms is not None else None,
                streaming=True,
            )
            if assistant_message_id:
                try:
                    await asyncio.to_thread(
                        supabase.table("messages").update({"content": full_content}).eq("id", assistant_message_id).execute
                    )
                except Exception as exc:
                    logger.error("messages_assistant_update_failed", error=str(exc), message_id=assistant_message_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
