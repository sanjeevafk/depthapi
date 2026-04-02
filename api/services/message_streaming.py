"""Messages streaming orchestration."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse

from logging_config import logger, log_sampled_success
from monitoring import capture_telemetry_event
from services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from services.streaming_orchestrator import (
    close_stream,
    compute_fallback_timeout,
    update_idempotency_progress,
)
from api.repositories.chat_repository import ChatRepository
from utils import TECHNICAL_MODE, SOCRATIC_MODE


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def build_message_replay_response(
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


def build_message_stream_response(
    *,
    request: Request,
    req,
    request_id: str,
    request_received: float,
    user_id: str,
    user_id_hash: str | None,
    content: str,
    content_hash: str,
    selected_mode: str,
    prompt_mode: str,
    assistant_message_id: Optional[str],
    client_message_id: str,
    conversation_id: str,
    request_temperature: float,
    cached_response: Optional[str],
    cache_key: str,
    cache_ttl_seconds: int,
    stream_max_seconds: int,
    stream_start_timeout_seconds: float,
    heartbeat_seconds: float,
    fallback_timeout_seconds: float,
    idempotency_key: str,
    idempotency_ttl_seconds: int,
    idempotency_started_at: int,
    is_pro: bool,
    generate_stream_explanation,
    generate_explanation,
    cache_set,
    log_context: dict[str, Any],
    log_sampled_success_fn=None,
) -> StreamingResponse:
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
        response_truncated = False
        fallback_used = False
        fallback_timeout_cap_seconds: float | None = None
        fallback_skipped_remaining_time = False
        start_timeout = False
        telemetry_sink: dict[str, Any] = {}
        stream_failed = False
        last_progress_update = start_time

        async def update_progress() -> None:
            nonlocal last_progress_update
            last_progress_update = await update_idempotency_progress(
                cache_set=cache_set,
                key=idempotency_key,
                ttl=idempotency_ttl_seconds,
                started_at=idempotency_started_at,
                response_chars=len(full_content),
                record_fields={
                    "message_id": client_message_id,
                    "assistant_message_id": assistant_message_id,
                    "mode": selected_mode,
                    "prompt_mode": prompt_mode,
                },
                last_update_time=last_progress_update,
            )

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
                    conversation_id=conversation_id,
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
                telemetry_sink=telemetry_sink,
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
                await update_progress()
                yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})

            generation_ms = (time.perf_counter() - generation_start) * 1000

            if (start_timeout or timed_out) and not full_content.strip() and not aborted:
                fallback_used = True
                logger.warning(
                    "messages_stream_fallback",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    reason="start_timeout" if start_timeout else "max_duration",
                    conversation_id=conversation_id,
                    message_id=client_message_id,
                    retry=bool(req.regenerate),
                    sampled=False,
                )
                fallback_timeout, fallback_timeout_cap_seconds, fallback_skipped_remaining_time = (
                    compute_fallback_timeout(
                        start_time=start_time,
                        stream_max_seconds=stream_max_seconds,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        log_context=log_context,
                    )
                )
                if fallback_timeout is None:
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
                    yield emit("done", "[DONE]")
                    return
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
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout,
                    )
                except Exception as exc:
                    logger.error(
                        "messages_fallback_failed",
                        error=_error_text(exc),
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=conversation_id,
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
                error=_error_text(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=conversation_id,
                content_hash=content_hash,
                retry=bool(req.regenerate),
                sampled=False,
            )
            if not aborted and not full_content.strip():
                fallback_used = True
                fallback_timeout, fallback_timeout_cap_seconds, fallback_skipped_remaining_time = (
                    compute_fallback_timeout(
                        start_time=start_time,
                        stream_max_seconds=stream_max_seconds,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        log_context=log_context,
                    )
                )
                if fallback_timeout is None:
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
                    yield emit("done", "[DONE]")
                    return
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
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout,
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
                        error=_error_text(fallback_exc),
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        conversation_id=conversation_id,
                        content_hash=content_hash,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
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
            total_ms = (time.perf_counter() - start_time) * 1000
            avg_chunk_interval_ms = None
            if chunk_count > 1:
                avg_chunk_interval_ms = total_chunk_interval_ms / (chunk_count - 1)
            if aborted:
                logger.info(
                    "messages_abort_confirmed",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=conversation_id,
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
            log_fn = log_sampled_success_fn or log_sampled_success
            log_fn(
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
                fallback_timeout_cap_seconds=round(fallback_timeout_cap_seconds, 2)
                if fallback_timeout_cap_seconds is not None
                else None,
                fallback_skipped_remaining_time=fallback_skipped_remaining_time,
                stream_max_seconds=stream_max_seconds,
                sampled=True,
            )
            if assistant_message_id:
                def _update_db():
                    try:
                        ChatRepository.update_assistant_message(assistant_message_id, full_content)
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
                asyncio.create_task(asyncio.to_thread(_update_db))

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
        headers=SSE_RESPONSE_HEADERS,
    )
