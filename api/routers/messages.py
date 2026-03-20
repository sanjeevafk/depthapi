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
from services.ensemble import ensemble_stream_generate
from services.ensemble import ensemble_generate
from services.inference import generate_explanation, generate_stream_explanation
from services.llm_client import get_litellm_config_state
from services.llm_errors import LLMUnavailable
from services.rate_limit import enforce_request_controls, estimate_tokens_for_text
from services.streaming import SseEventBuilder
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


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
    heartbeat_seconds = min(
        max(float(getattr(config_settings, "stream_heartbeat_seconds", 2)), 0.1),
        2,
    )
    raw_start_timeout = float(getattr(config_settings, "stream_start_timeout_seconds", 2))
    idempotency_ttl_seconds = min(
        max(int(getattr(config_settings, "stream_idempotency_ttl_seconds", 90)), 60),
        120,
    )
    trusted_proxies = _trusted_proxies_from_settings(config_settings)

    idempotency_key = _idempotency_key(user_id, client_message_id)
    idempotency_payload = await cache_get(idempotency_key)
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
            raise HTTPException(status_code=409, detail="Duplicate request already in progress.")

    estimated_tokens = estimate_tokens_for_text(content)
    client_ip = _resolve_client_ip(request, trusted_proxies=trusted_proxies)
    await enforce_request_controls(
        user_id=user_id,
        client_ip=client_ip,
        estimated_tokens=estimated_tokens,
    )

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        conversation_resp = await asyncio.to_thread(
            supabase.table("conversations")
            .select("id, user_id, mode, settings")
            .eq("id", req.conversation_id)
            .eq("user_id", user_id)
            .single()
            .execute
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
        stream_start_timeout_seconds = float(stream_max_seconds)
    else:
        cap = 2.0 if is_prod else 5.0
        stream_start_timeout_seconds = min(max(raw_start_timeout, 0.1), cap)

    requested_prompt_mode = PROMPT_MODE_ALIASES.get(req.prompt_mode or "", req.prompt_mode or "")
    stored_prompt_mode = PROMPT_MODE_ALIASES.get(
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
        cast(str, (conversation.get("settings") or {}).get("prompt_mode") or ""),
    )
    prompt_mode = normalize_prompt_level(requested_prompt_mode or stored_prompt_mode)
    if prompt_mode not in SUPPORTED_PROMPT_MODES:
        prompt_mode = normalize_prompt_level(None)

    is_pro = await check_is_pro(user_id)
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

    idempotency_record = {
        "status": "in_progress",
        "message_id": client_message_id,
        "assistant_client_id": assistant_client_id,
        "mode": selected_mode,
        "prompt_mode": prompt_mode,
    }
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
                raise HTTPException(status_code=409, detail="Duplicate request already in progress.")
            if status == "failed":
                await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)

    user_metadata = {
        "client_id": client_message_id,
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
    try:
        await asyncio.to_thread(
            supabase.table("conversations").update(update_payload).eq("id", conversation.get("id")).execute
        )
    except Exception as exc:
        logger.warning(
            "messages_conversation_update_failed",
            error=str(exc),
            request_id=request_id,
            user_id_hash=user_id_hash,
            conversation_id=req.conversation_id,
            retry=bool(req.regenerate),
            sampled=False,
        )

    assistant_metadata = {
        "assistant_client_id": assistant_client_id,
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
        tokens_after_abort = 0
        timed_out = False
        fallback_used = False
        start_timeout = False
        telemetry_sink: dict[str, Any] = {}
        stream_failed = False

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
                    await close_fn()
                except Exception:
                    pass

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
            stream = (
                ensemble_stream_generate(
                    content,
                    prompt_mode,
                    mode=selected_mode,
                    use_premium=is_pro,
                    temperature=request_temperature,
                    regenerate=req.regenerate,
                    request_id=request_id,
                    user_id=user_id,
                    telemetry_sink=telemetry_sink,
                )
                if selected_mode == LEARNING_MODE
                else generate_stream_explanation(
                    content,
                    prompt_mode,
                    mode=selected_mode,
                    temperature=request_temperature,
                    regenerate=req.regenerate,
                    request_id=request_id,
                    user_id=user_id,
                    telemetry_sink=telemetry_sink,
                )
            )
            stream_iter = stream.__aiter__()
            start_deadline = start_time + stream_start_timeout_seconds

            while True:
                if await request.is_disconnected():
                    aborted = True
                    abort_reason = "client_disconnect"
                    await close_stream(stream)
                    break

                elapsed = time.perf_counter() - start_time
                if elapsed >= stream_max_seconds:
                    timed_out = True
                    await close_stream(stream)
                    break

                timeout = heartbeat_seconds
                if chunk_count == 0:
                    timeout = min(timeout, max(0.0, start_deadline - time.perf_counter()))
                    if timeout <= 0:
                        start_timeout = True
                        await close_stream(stream)
                        break

                try:
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=timeout)
                except asyncio.TimeoutError:
                    yield emit("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
                    if chunk_count == 0 and time.perf_counter() >= start_deadline:
                        start_timeout = True
                        await close_stream(stream)
                        break
                    continue
                except StopAsyncIteration:
                    break

                if aborted:
                    tokens_after_abort += 1
                    continue

                full_content += chunk
                record_chunk()
                yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})

            generation_ms = (time.perf_counter() - generation_start) * 1000

            if (start_timeout or timed_out) and not full_content.strip() and not aborted:
                fallback_used = True
                logger.warning(
                    "messages_stream_fallback",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    reason="start_timeout" if start_timeout else "max_duration",
                    conversation_id=req.conversation_id,
                    message_id=client_message_id,
                    retry=bool(req.regenerate),
                    sampled=False,
                )
                try:
                    fallback_content = await asyncio.wait_for(
                        (
                            ensemble_generate(
                                content,
                                prompt_mode,
                                mode=selected_mode,
                                use_premium=is_pro,
                                temperature=request_temperature,
                                regenerate=req.regenerate,
                                request_id=request_id,
                                user_id=user_id,
                                telemetry_sink=telemetry_sink,
                            )
                            if selected_mode == LEARNING_MODE
                            else generate_explanation(
                                content,
                                prompt_mode,
                                mode=selected_mode,
                                temperature=request_temperature,
                                regenerate=req.regenerate,
                                request_id=request_id,
                                user_id=user_id,
                                telemetry_sink=telemetry_sink,
                            )
                        ),
                        timeout=max(stream_max_seconds - (time.perf_counter() - start_time), 1),
                    )
                except Exception as exc:
                    logger.error(
                        "messages_fallback_failed",
                        error=str(exc),
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=req.conversation_id,
                        content_hash=content_hash,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
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
            await cache_set(
                idempotency_key,
                {"status": "failed", "message_id": client_message_id},
                ttl=idempotency_ttl_seconds,
            )
            if not aborted:
                yield emit("error", {"error": "Streaming failed"})
                yield emit("done", "[DONE]")
        finally:
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
                    tokens_after_abort=tokens_after_abort,
                    reason=abort_reason,
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
                        supabase.table("messages").update({"content": full_content}).eq("id", assistant_message_id).execute
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
