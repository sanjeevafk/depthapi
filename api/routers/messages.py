"""Chat messages endpoint."""

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import check_is_pro, get_supabase_admin, verify_token
from config import get_settings
from logging_config import anonymize_text, anonymize_user_id, logger, log_sampled_success
from monitoring import capture_telemetry_event
from services.cache import cache_get, cache_set, cache_set_if_absent
from services.inference import TECHNICAL_MAX_TOKENS, generate_explanation, generate_stream_explanation
from services.llm_client import get_provider_config_state
from services.llm_errors import LLMUnavailable
from services.rate_limit import enforce_request_controls, refund_tokens
from services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from services.token_count import count_prompt_tokens
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


def _idempotency_key(user_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"knowbear:idempotency:{digest}"


def _require_uuid(value: Optional[str], field_name: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a UUID") from exc


def _build_replay_response(
    *,
    content: str,
    message_id: str,
    assistant_message_id: Optional[str],
    mode: str,
    prompt_mode: str,
) -> StreamingResponse:
    async def replay_generator():
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
            payload = {"delta": content[index : index + 400]}
            if assistant_message_id:
                payload["assistant_message_id"] = assistant_message_id
            yield builder.emit_json("delta", payload)
        yield builder.emit("done", "[DONE]")

    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


def _final_fallback_message(mode: str) -> str:
    mode_label = "response"
    if mode == TECHNICAL_MODE:
        mode_label = "technical response"
    elif mode == SOCRATIC_MODE:
        mode_label = "socratic response"
    return (
        f"Unable to generate a complete {mode_label} right now due to a transient timeout. "
        "Please retry in a moment."
    )


@router.post("/messages")
async def send_message(req: MessageRequest, request: Request, auth_data: dict = Depends(verify_token)):
    request_received = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
    config_state = get_provider_config_state()
    if not bool(config_state.get("chat_enabled", False)):
        raise LLMUnavailable(
            "Model service is temporarily unavailable. Please try again shortly."
        )

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
    stream_max_seconds = max(int(getattr(config_settings, "stream_max_seconds", 24)), 1)
    if not is_prod:
        stream_max_seconds = max(stream_max_seconds, 60)
    function_duration_cap: int | None = None
    if is_prod:
        # Lock production SSE stream cap below Vercel's 25s hard cutoff.
        function_duration_cap = 24
        stream_max_seconds = function_duration_cap
    fallback_budget_seconds = max(
        1.0,
        min(float(getattr(config_settings, "stream_fallback_budget_seconds", 6)), float(stream_max_seconds)),
    )
    if is_prod:
        fallback_budget_seconds = max(fallback_budget_seconds, 8.0)
    fallback_timeout_seconds = max(fallback_budget_seconds, 3.0)
    close_timeout_seconds = 0.25
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

    idempotency_key = _idempotency_key(user_id, client_message_id)
    idempotency_payload = await cache_get(idempotency_key)
    idempotency_claimed = False
    if idempotency_payload:
        status = idempotency_payload.get("status")
        cached_response = idempotency_payload.get("response")
        if status == "completed" and cached_response:
            assistant_message_id = idempotency_payload.get("assistant_message_id")
            replay_mode = idempotency_payload.get("mode") or DEFAULT_CHAT_MODE
            replay_prompt_mode = idempotency_payload.get("prompt_mode") or normalize_prompt_level(None)
            return _build_replay_response(
                content=str(cached_response),
                message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                mode=replay_mode,
                prompt_mode=replay_prompt_mode,
            )

        if status == "in_progress":
            started_at = idempotency_payload.get("started_at")
            now_ts = int(time.time())
            started_ts = int(started_at) if isinstance(started_at, (int, float)) else now_ts
            age_seconds = max(now_ts - started_ts, 0)
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

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        conversation_resp = await asyncio.to_thread(
            lambda: supabase.table("conversations")
            .select("id, user_id, mode, settings")
            .eq("id", req.conversation_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not getattr(conversation_resp, "data", None):
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation = cast(Dict[str, Any], conversation_resp.data)
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
        if function_duration_cap is not None:
            stream_max_seconds = min(stream_max_seconds, function_duration_cap)
        technical_start_timeout = float(
            getattr(config_settings, "technical_stream_start_timeout_seconds", max(raw_start_timeout, 6.0))
        )
        technical_cap = max(4.0, min(float(stream_max_seconds) * 0.75, 20.0))
        stream_start_timeout_seconds = min(max(technical_start_timeout, 2.0), technical_cap)
        fallback_budget_seconds = max(fallback_budget_seconds, 4.0)
        fallback_timeout_seconds = max(fallback_budget_seconds, 4.0)
    else:
        cap = 10.0 if is_prod else 15.0
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
    if selected_mode == SOCRATIC_MODE and not is_pro:
        raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")

    if selected_mode == TECHNICAL_MODE:
        max_output_tokens = TECHNICAL_MAX_TOKENS
    elif selected_mode == SOCRATIC_MODE:
        max_output_tokens = int(getattr(config_settings, "max_output_tokens_socratic", 1024))
    else:
        max_output_tokens = int(getattr(config_settings, "max_output_tokens_learning", 1024))

    prompt_tokens = count_prompt_tokens(content)
    reserved_tokens = max(prompt_tokens + max_output_tokens, 1)
    client_ip = _resolve_client_ip(request, trusted_proxies=trusted_proxies)
    quota_reservation = await enforce_request_controls(
        user_id=user_id,
        client_ip=client_ip,
        reserved_tokens=reserved_tokens,
        mode=selected_mode,
        is_pro=is_pro,
    )
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

    idempotency_record = {
        "status": "in_progress",
        "started_at": int(time.time()),
        "message_id": client_message_id,
        "assistant_client_id": assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }
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
                return _build_replay_response(
                    content=str(idempotency_response),
                    message_id=client_message_id,
                    assistant_message_id=existing.get("assistant_message_id"),
                    mode=existing.get("mode") or selected_mode,
                    prompt_mode=existing.get("prompt_mode") or prompt_mode,
                )
            if status == "in_progress":
                started_at = existing.get("started_at")
                now_ts = int(time.time())
                started_ts = int(started_at) if isinstance(started_at, (int, float)) else now_ts
                age_seconds = max(now_ts - started_ts, 0)
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

    try:
        await asyncio.to_thread(
            lambda: supabase.table("messages")
            .insert(
                {
                    "conversation_id": conversation.get("id"),
                    "role": "user",
                    "content": content,
                    "metadata": user_metadata,
                }
            )
            .execute()
        )
    except Exception as exc:
        logger.error(
            "messages_user_insert_failed",
            error=str(exc),
            request_id=request_id,
            user_id_hash=user_id_hash,
            conversation_id=req.conversation_id,
            retry=bool(req.regenerate),
            sampled=False,
        )
        await cache_set(
            idempotency_key,
            {"status": "failed", "message_id": client_message_id},
            ttl=idempotency_ttl_seconds,
        )
        raise HTTPException(status_code=500, detail="Failed to save user message") from exc

    now_iso = datetime.now(timezone.utc).isoformat()
    update_payload = {
        "mode": selected_mode,
        "settings": {**(conversation.get("settings") or {}), "mode": selected_mode, "prompt_mode": prompt_mode},
        "updated_at": now_iso,
    }
    assistant_metadata = {
        "assistant_client_id": assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }

    try:
        assistant_result, conversation_update_result = await asyncio.gather(
            asyncio.to_thread(
                lambda: supabase.table("messages")
                .insert(
                    {
                        "conversation_id": conversation.get("id"),
                        "role": "assistant",
                        "content": "",
                        "metadata": assistant_metadata,
                    }
                )
                .execute()
            ),
            asyncio.to_thread(
                lambda: supabase.table("conversations")
                .update(update_payload)
                .eq("id", conversation.get("id"))
                .execute()
            ),
            return_exceptions=True,
        )

        if isinstance(conversation_update_result, Exception):
            logger.warning(
                "messages_conversation_update_failed",
                error=str(conversation_update_result),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=req.conversation_id,
                retry=bool(req.regenerate),
                sampled=False,
            )

        if isinstance(assistant_result, Exception):
            raise assistant_result

        assistant_resp = assistant_result
        assistant_data = cast(list[Dict[str, Any]], assistant_resp.data) if assistant_resp.data else []
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
        logger.error(
            "messages_assistant_insert_failed",
            error=str(exc),
            request_id=request_id,
            user_id_hash=user_id_hash,
            conversation_id=req.conversation_id,
            retry=bool(req.regenerate),
            sampled=False,
        )
        await cache_set(
            idempotency_key,
            {"status": "failed", "message_id": client_message_id},
            ttl=idempotency_ttl_seconds,
        )
        raise HTTPException(status_code=500, detail="Failed to start assistant message") from exc

    async def event_generator():
        start_time = time.perf_counter()
        full_content = ""
        builder = SseEventBuilder()
        first_event_ms = None
        first_token_ms = None
        last_chunk_time = None
        total_chunk_interval_ms = 0.0
        chunk_count = 0
        chunk_size = 400
        generation_ms = None
        aborted = False
        abort_reason = None

        timed_out = False
        response_truncated = False
        fallback_used = False
        start_timeout = False
        telemetry_sink: dict[str, Any] = {}
        stream_failed = False
        pending_chunk_task: asyncio.Task[str] | None = None

        capture_telemetry_event(
            "stream_start",
            request_id=request_id,
            user_id_hash=user_id_hash,
            mode=selected_mode,
            prompt_mode=prompt_mode,
            regenerate=bool(req.regenerate),
        )

        def record_chunk():
            nonlocal first_token_ms, last_chunk_time, total_chunk_interval_ms, chunk_count
            now = time.perf_counter()
            if first_token_ms is None:
                first_token_ms = (now - start_time) * 1000
            if last_chunk_time is not None:
                total_chunk_interval_ms += (now - last_chunk_time) * 1000
            last_chunk_time = now
            chunk_count += 1

        def emit(event: str, payload: dict[str, Any] | str) -> str:
            nonlocal first_event_ms
            if first_event_ms is None:
                first_event_ms = (time.perf_counter() - start_time) * 1000
            if isinstance(payload, dict):
                return builder.emit_json(event, payload)
            return builder.emit(event, payload)

        async def close_stream(stream):
            close_fn = getattr(stream, "aclose", None)
            if close_fn:
                try:
                    await asyncio.wait_for(close_fn(), timeout=close_timeout_seconds)
                except asyncio.TimeoutError:
                    logger.warning(
                        "messages_stream_close_timeout",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=req.conversation_id,
                        mode=selected_mode,
                        sampled=False,
                    )
                except Exception:
                    pass

        async def cancel_pending_chunk_task() -> None:
            nonlocal pending_chunk_task
            if pending_chunk_task is None:
                return
            pending_chunk_task.cancel()
            try:
                await pending_chunk_task
            except BaseException:
                pass
            pending_chunk_task = None

        stream = None
        try:
            meta_payload = {
                "assistant_message_id": assistant_message_id,
                "mode": selected_mode,
                "prompt_mode": prompt_mode,
                "message_id": client_message_id,
            }
            yield emit("meta", meta_payload)

            if cached_response:
                log_sampled_success(
                    "messages_cache_hit",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    model_alias="cache",
                    latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    estimated_cost_usd=0.0,
                    retry=bool(req.regenerate),
                    conversation_id=req.conversation_id,
                    sampled=True,
                )
                full_content = cached_response
                await cache_set(
                    idempotency_key,
                    {
                        "status": "completed",
                        "response": full_content,
                        "assistant_message_id": assistant_message_id,
                        "mode": selected_mode,
                        "prompt_mode": prompt_mode,
                    },
                    ttl=idempotency_ttl_seconds,
                )
                for index in range(0, len(cached_response), chunk_size):
                    chunk = cached_response[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                yield emit("done", "[DONE]")
                return

            generation_start = time.perf_counter()
            stream = generate_stream_explanation(
                content,
                prompt_mode,
                mode=selected_mode,
                temperature=request_temperature,
                regenerate=req.regenerate,
                request_id=request_id,
                user_id=user_id,
                is_pro=is_pro,
                telemetry_sink=telemetry_sink,
            )
            stream_iter = stream.__aiter__()
            start_deadline = start_time + stream_start_timeout_seconds

            while True:
                if await request.is_disconnected():
                    aborted = True
                    abort_reason = "client_disconnect"
                    await cancel_pending_chunk_task()
                    await close_stream(stream)
                    break

                elapsed = time.perf_counter() - start_time
                if elapsed >= stream_max_seconds:
                    timed_out = True
                    await cancel_pending_chunk_task()
                    await close_stream(stream)
                    break

                timeout = heartbeat_seconds
                if chunk_count == 0:
                    timeout = min(timeout, max(0.0, start_deadline - time.perf_counter()))
                    if timeout <= 0:
                        start_timeout = True
                        await cancel_pending_chunk_task()
                        await close_stream(stream)
                        break

                try:
                    if pending_chunk_task is None:
                        async def get_next_chunk():
                            return await anext(stream_iter)
                        pending_chunk_task = asyncio.create_task(get_next_chunk())
                    chunk = await asyncio.wait_for(asyncio.shield(pending_chunk_task), timeout=timeout)
                    pending_chunk_task = None
                except asyncio.TimeoutError:
                    yield emit("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
                    if chunk_count == 0 and time.perf_counter() >= start_deadline:
                        start_timeout = True
                        await cancel_pending_chunk_task()
                        await close_stream(stream)
                        break
                    continue
                except StopAsyncIteration:
                    pending_chunk_task = None
                    break



                full_content += chunk
                record_chunk()
                yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})

            generation_ms = (time.perf_counter() - generation_start) * 1000

            no_chunks = chunk_count == 0 and not full_content.strip()
            if (start_timeout or timed_out or no_chunks) and not full_content.strip() and not aborted:
                fallback_used = True
                logger.warning(
                    "messages_stream_fallback",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    reason=(
                        "start_timeout"
                        if start_timeout
                        else "max_duration"
                        if timed_out
                        else "empty_stream"
                    ),
                    conversation_id=req.conversation_id,
                    message_id=client_message_id,
                    retry=bool(req.regenerate),
                    sampled=False,
                )
                try:
                    fallback_content = await asyncio.wait_for(
                        generate_explanation(
                            content,
                            prompt_mode,
                            mode=selected_mode,
                            temperature=request_temperature,
                            regenerate=req.regenerate,
                            request_id=request_id,
                            user_id=user_id,
                            is_pro=is_pro,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        "messages_fallback_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=req.conversation_id,
                        content_hash=content_hash,
                        mode=selected_mode,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
                    full_content = _final_fallback_message(selected_mode)
                    yield emit("delta", {"delta": full_content, "assistant_message_id": assistant_message_id})
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "completed",
                            "response": full_content,
                            "assistant_message_id": assistant_message_id,
                            "mode": selected_mode,
                            "prompt_mode": prompt_mode,
                        },
                        ttl=idempotency_ttl_seconds,
                    )
                    yield emit("done", "[DONE]")
                    return

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                yield emit("done", "[DONE]")
                if not req.regenerate:
                    await cache_set(cache_key, {"response": full_content}, ttl=cache_ttl_seconds)
                await cache_set(
                    idempotency_key,
                    {
                        "status": "completed",
                        "response": full_content,
                        "assistant_message_id": assistant_message_id,
                        "mode": selected_mode,
                        "prompt_mode": prompt_mode,
                    },
                    ttl=idempotency_ttl_seconds,
                )
                return

            response_truncated = bool(timed_out and not aborted)
            if response_truncated:
                cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                full_content += cutoff_message
                yield emit("delta", {"delta": cutoff_message, "assistant_message_id": assistant_message_id})

            if full_content.strip() and not response_truncated and not req.regenerate:
                await cache_set(cache_key, {"response": full_content}, ttl=cache_ttl_seconds)

            if full_content.strip():
                await cache_set(
                    idempotency_key,
                    {
                        "status": "completed",
                        "response": full_content,
                        "assistant_message_id": assistant_message_id,
                        "mode": selected_mode,
                        "prompt_mode": prompt_mode,
                        "truncated": response_truncated,
                    },
                    ttl=idempotency_ttl_seconds,
                )
            else:
                await cache_set(
                    idempotency_key,
                    {"status": "failed", "message_id": client_message_id},
                    ttl=idempotency_ttl_seconds,
                )

            if not aborted:
                yield emit("done", "[DONE]")
        except Exception as exc:
            stream_failed = True
            logger.error(
                "messages_stream_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=req.conversation_id,
                content_hash=content_hash,
                retry=bool(req.regenerate),
                sampled=False,
            )
            if not aborted and not full_content.strip():
                fallback_used = True
                try:
                    fallback_content = await asyncio.wait_for(
                        generate_explanation(
                            content,
                            prompt_mode,
                            mode=selected_mode,
                            temperature=request_temperature,
                            regenerate=req.regenerate,
                            request_id=request_id,
                            user_id=user_id,
                            is_pro=is_pro,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout_seconds,
                    )
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        chunk = full_content[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                    yield emit("done", "[DONE]")
                    if not req.regenerate:
                        await cache_set(cache_key, {"response": full_content}, ttl=cache_ttl_seconds)
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "completed",
                            "response": full_content,
                            "assistant_message_id": assistant_message_id,
                            "mode": selected_mode,
                            "prompt_mode": prompt_mode,
                        },
                        ttl=idempotency_ttl_seconds,
                    )
                    return
                except Exception as fallback_exc:
                    logger.error(
                        "messages_exception_fallback_failed",
                        error=str(fallback_exc),
                        error_type=type(fallback_exc).__name__,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=req.conversation_id,
                        content_hash=content_hash,
                        mode=selected_mode,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
                    full_content = _final_fallback_message(selected_mode)
                    yield emit("delta", {"delta": full_content, "assistant_message_id": assistant_message_id})
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "completed",
                            "response": full_content,
                            "assistant_message_id": assistant_message_id,
                            "mode": selected_mode,
                            "prompt_mode": prompt_mode,
                        },
                        ttl=idempotency_ttl_seconds,
                    )
                    yield emit("done", "[DONE]")
                    return
            if aborted:
                await cache_set(
                    idempotency_key,
                    {"status": "failed", "message_id": client_message_id},
                    ttl=idempotency_ttl_seconds,
                )
                return
            if full_content.strip():
                if not req.regenerate and not response_truncated:
                    await cache_set(cache_key, {"response": full_content}, ttl=cache_ttl_seconds)
                await cache_set(
                    idempotency_key,
                    {
                        "status": "completed",
                        "response": full_content,
                        "assistant_message_id": assistant_message_id,
                        "mode": selected_mode,
                        "prompt_mode": prompt_mode,
                        "partial": True,
                    },
                    ttl=idempotency_ttl_seconds,
                )
                mode_label = ""
                if selected_mode == TECHNICAL_MODE:
                    mode_label = "technical "
                elif selected_mode == SOCRATIC_MODE:
                    mode_label = "socratic "
                yield emit(
                    "delta",
                    {
                        "delta": f"\n\n[Connection interrupted. Partial {mode_label}response delivered.]",
                        "assistant_message_id": assistant_message_id,
                    },
                )
                yield emit("done", "[DONE]")
                return
            await cache_set(
                idempotency_key,
                {"status": "failed", "message_id": client_message_id},
                ttl=idempotency_ttl_seconds,
            )
            yield emit("error", {"error": "Streaming failed"})
            yield emit("done", "[DONE]")
        finally:
            await cancel_pending_chunk_task()
            if stream is not None:
                await close_stream(stream)
            total_ms = (time.perf_counter() - start_time) * 1000
            avg_chunk_interval_ms = None
            if chunk_count > 1:
                avg_chunk_interval_ms = total_chunk_interval_ms / (chunk_count - 1)
            if aborted:
                logger.info(
                    "messages_abort_confirmed",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    message_id=client_message_id,
                    abort_confirmed=True,
                    reason=abort_reason,
                    tokens_after_abort=0,
                )
            queue_time_ms = round((start_time - request_received) * 1000, 2)
            model_inference_ms = telemetry_sink.get("model_inference_ms")
            stream_duration_ms = telemetry_sink.get("stream_duration_ms")
            token_usage = telemetry_sink.get("token_usage")
            estimated_cost_usd = telemetry_sink.get("estimated_cost_usd")
            log_sampled_success(
                "messages_stream_observed",
                request_id=request_id,
                user_id_hash=user_id_hash,
                model_alias=str(telemetry_sink.get("model_alias") or selected_mode),
                mode=selected_mode,
                prompt_mode=prompt_mode,
                latency_ms=round(total_ms, 2),
                queue_time_ms=queue_time_ms,
                model_inference_ms=model_inference_ms,
                stream_duration_ms=stream_duration_ms,
                token_usage=token_usage,
                estimated_cost_usd=estimated_cost_usd,
                retry=bool(req.regenerate),
                first_event_ms=round(first_event_ms, 2) if first_event_ms is not None else None,
                first_token_ms=round(first_token_ms, 2) if first_token_ms is not None else None,
                avg_chunk_interval_ms=round(avg_chunk_interval_ms, 2) if avg_chunk_interval_ms is not None else None,
                chunk_count=chunk_count,
                chunk_size=chunk_size,
                content_chars=len(full_content),
                is_pro=is_pro,
                generation_ms=round(generation_ms, 2) if generation_ms is not None else None,
                streaming=True,
                timed_out=timed_out,
                fallback_used=fallback_used,
                stream_max_seconds=stream_max_seconds,
                sampled=True,
            )
            if assistant_message_id:
                try:
                    await asyncio.to_thread(
                        lambda: supabase.table("messages")
                        .update({"content": full_content})
                        .eq("id", assistant_message_id)
                        .execute()
                    )
                except Exception as exc:
                    logger.error(
                        "messages_assistant_update_failed",
                        error=str(exc),
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        message_id=assistant_message_id,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )

            status = "success"
            if aborted:
                status = "aborted"
            elif timed_out or start_timeout:
                status = "timed_out"
            elif stream_failed:
                status = "error"
            capture_telemetry_event(
                "stream_end",
                request_id=request_id,
                user_id_hash=user_id_hash,
                mode=selected_mode,
                prompt_mode=prompt_mode,
                regenerate=bool(req.regenerate),
                status=status,
                duration_ms=round(total_ms, 2),
                fallback_used=fallback_used,
            )
            if isinstance(token_usage, dict):
                actual_tokens = int(token_usage.get("prompt_tokens") or 0) + int(
                    token_usage.get("completion_tokens") or 0
                )
                if actual_tokens > 0:
                    try:
                        await refund_tokens(quota_reservation, actual_tokens)
                    except Exception as exc:
                        logger.warning(
                            "messages_quota_refund_failed",
                            error=str(exc),
                            request_id=request_id,
                            user_id_hash=user_id_hash,
                        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )
