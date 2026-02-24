"""Chat messages endpoint."""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import verify_token, get_supabase_admin, check_is_pro
from logging_config import logger
from services.inference import generate_stream_explanation
from services.ensemble import ensemble_generate
from utils import (
    CHAT_MODES,
    CHAT_PREMIUM_MODES,
    CHAT_PROMPT_MODES,
    SUPPORTED_CHAT_MODES,
    SUPPORTED_PROMPT_MODES,
    DEFAULT_CHAT_MODE,
    CHAT_INFERENCE_MODE_ALIASES,
    PROMPT_MODE_ALIASES,
)

router = APIRouter(tags=["messages"])


class MessageRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=8000)
    client_generated_id: Optional[str] = None
    assistant_client_id: Optional[str] = None
    mode: Optional[str] = None
    prompt_mode: Optional[str] = None


@router.post("/messages")
async def send_message(req: MessageRequest, auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]

    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

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
    except Exception as e:
        logger.error("messages_conversation_fetch_failed", error=str(e), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to load conversation")

    if not getattr(conversation_resp, "data", None):
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation = conversation_resp.data

    requested_mode = req.mode
    requested_prompt_mode = req.prompt_mode
    if requested_mode and requested_mode not in SUPPORTED_CHAT_MODES:
        raise HTTPException(status_code=400, detail="Unsupported mode")
    if requested_prompt_mode and requested_prompt_mode not in SUPPORTED_PROMPT_MODES:
        raise HTTPException(status_code=400, detail="Unsupported prompt_mode")

    settings = conversation.get("settings") or {}
    selected_mode = requested_mode or conversation.get("mode") or settings.get("mode") or DEFAULT_CHAT_MODE
    if selected_mode not in SUPPORTED_CHAT_MODES:
        selected_mode = DEFAULT_CHAT_MODE

    is_pro = await check_is_pro(user.id)
    if selected_mode in CHAT_PREMIUM_MODES and not is_pro:
        selected_mode = DEFAULT_CHAT_MODE

    if selected_mode == "ensemble":
        raw_prompt_mode = requested_prompt_mode or settings.get("prompt_mode") or settings.get("mode") or conversation.get("mode")
        if raw_prompt_mode in PROMPT_MODE_ALIASES:
            raw_prompt_mode = PROMPT_MODE_ALIASES[raw_prompt_mode]
        prompt_mode = raw_prompt_mode if raw_prompt_mode in CHAT_PROMPT_MODES else DEFAULT_CHAT_MODE
        if prompt_mode in CHAT_PREMIUM_MODES and not is_pro:
            prompt_mode = DEFAULT_CHAT_MODE
    else:
        raw_prompt_mode = requested_prompt_mode or selected_mode
        if raw_prompt_mode in PROMPT_MODE_ALIASES:
            raw_prompt_mode = PROMPT_MODE_ALIASES[raw_prompt_mode]
        prompt_mode = raw_prompt_mode if raw_prompt_mode in CHAT_PROMPT_MODES else DEFAULT_CHAT_MODE
        if prompt_mode in CHAT_PREMIUM_MODES and not is_pro:
            prompt_mode = DEFAULT_CHAT_MODE
    inference_mode = CHAT_INFERENCE_MODE_ALIASES.get(selected_mode, "ensemble")

    user_metadata = {
        "client_id": req.client_generated_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }

    try:
        await asyncio.to_thread(
            supabase.table("messages").insert(
                {
                    "conversation_id": conversation.get("id"),
                    "role": "user",
                    "content": content,
                    "metadata": user_metadata,
                }
            ).execute
        )
    except Exception as e:
        logger.error("messages_user_insert_failed", error=str(e), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to save user message")

    now_iso = datetime.now(timezone.utc).isoformat()
    if selected_mode in CHAT_MODES:
        next_settings = {**settings, "mode": selected_mode, "prompt_mode": prompt_mode}
        update_payload = {"mode": selected_mode, "settings": next_settings, "updated_at": now_iso}
    else:
        update_payload = {"updated_at": now_iso}

    try:
        await asyncio.to_thread(
            supabase.table("conversations")
            .update(update_payload)
            .eq("id", conversation.get("id"))
            .execute
        )
    except Exception as e:
        logger.warning("messages_conversation_update_failed", error=str(e), conversation_id=req.conversation_id)

    assistant_metadata = {
        "assistant_client_id": req.assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }

    try:
        assistant_resp = await asyncio.to_thread(
            supabase.table("messages").insert(
                {
                    "conversation_id": conversation.get("id"),
                    "role": "assistant",
                    "content": "",
                    "metadata": assistant_metadata,
                }
            ).execute
        )
        assistant_message_id = assistant_resp.data[0]["id"] if assistant_resp.data else None
    except Exception as e:
        logger.error("messages_assistant_insert_failed", error=str(e), conversation_id=req.conversation_id)
        raise HTTPException(status_code=500, detail="Failed to start assistant message")

    async def event_generator():
        start_time = time.perf_counter()
        full_content = ""
        sent_id = False
        first_token_ms = None
        last_chunk_time = None
        total_chunk_interval_ms = 0.0
        chunk_count = 0
        chunk_size = 400
        ensemble_ms = None

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

            if selected_mode == "ensemble":
                ensemble_start = time.perf_counter()
                result = await ensemble_generate(content, prompt_mode, use_premium=is_pro, mode="ensemble")
                ensemble_ms = (time.perf_counter() - ensemble_start) * 1000
                full_content = result
                for i in range(0, len(result), chunk_size):
                    chunk = result[i:i + chunk_size]
                    record_chunk()
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"
            else:
                async for chunk in generate_stream_explanation(
                    topic=content,
                    level=prompt_mode,
                    mode=inference_mode,
                    is_pro=is_pro,
                ):
                    full_content += chunk
                    record_chunk()
                    payload = {"delta": chunk}
                    if not sent_id and assistant_message_id:
                        payload["assistant_message_id"] = assistant_message_id
                        sent_id = True
                    yield f"data: {json.dumps(payload)}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error("messages_stream_failed", error=str(e), conversation_id=req.conversation_id)
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
                ensemble_ms=round(ensemble_ms, 2) if ensemble_ms is not None else None,
                streaming=selected_mode != "ensemble",
            )
            if assistant_message_id:
                try:
                    await asyncio.to_thread(
                        supabase.table("messages")
                        .update({"content": full_content})
                        .eq("id", assistant_message_id)
                        .execute
                    )
                except Exception as e:
                    logger.error("messages_assistant_update_failed", error=str(e), message_id=assistant_message_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
