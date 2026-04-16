"""Query streaming orchestration."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from collections.abc import AsyncIterable, AsyncIterator, Iterable

from fastapi.responses import StreamingResponse

from logging_config import logger, log_sampled_success
from services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from services.streaming_orchestrator import (
    close_stream,
    compute_fallback_timeout,
    update_idempotency_progress,
)


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def build_query_stream_replay_response(
    *,
    topic: str,
    level: str,
    mode: str,
    message_id: str,
    content: str,
) -> StreamingResponse:
    async def replay_generator():
        builder = SseEventBuilder()
        yield builder.emit_json(
            "meta",
            {
                "topic": topic,
                "level": level,
                "mode": mode,
                "message_id": message_id,
                "replay": True,
            },
        )
        for index in range(0, len(content), 400):
            yield builder.emit_json("chunk", {"chunk": content[index : index + 400]})
        yield builder.emit("done", "[DONE]")

    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


def build_query_stream_wait_response(
    *,
    retry_after_ms: int,
    message_id: str,
    mode: str,
    level: str,
    topic: str,
) -> StreamingResponse:
    async def wait_generator():
        builder = SseEventBuilder()
        yield builder.emit_json(
            "status",
            {
                "status": "waiting",
                "retry_after_ms": retry_after_ms,
                "message_id": message_id,
                "mode": mode,
                "level": level,
                "topic": topic,
            },
        )
        yield builder.emit("done", "[DONE]")

    return StreamingResponse(
        wait_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


async def _stream_chunks(stream: AsyncIterable[str] | Iterable[str]) -> AsyncIterator[str]:
    if isinstance(stream, AsyncIterable):
        async for chunk in stream:
            yield chunk
    else:
        for chunk in stream:
            yield chunk


def build_query_stream_response(
    *,
    req,
    request_id: str,
    request_received: float,
    topic: str,
    level: str,
    mode: str,
    message_id: str | None,
    user_id_raw: str | None,
    user_id_hash: str | None,
    topic_hash: str,
    auth_data,
    cache_get,
    cache_set,
    cache_key_value: str,
    generate_stream_explanation,
    generate_explanation,
    persist_history,
    stream_max_seconds: int,
    stream_start_timeout_seconds: float,
    heartbeat_seconds: float,
    fallback_timeout_seconds: float,
    idempotency_key: str | None,
    idempotency_ttl_seconds: int,
    idempotency_started_at: int | None,
) -> StreamingResponse:
    async def event_generator():
        full_content = ""
        builder = SseEventBuilder()
        start_time = time.perf_counter()
        queue_started = start_time
        first_event_ms = None
        first_token_ms = None
        last_chunk_time = None
        total_chunk_interval_ms = 0.0
        chunk_count = 0
        chunk_size = 400
        timed_out = False
        start_timeout = False
        fallback_used = False
        fallback_timeout_cap_seconds: float | None = None
        fallback_skipped_remaining_time = False
        telemetry_sink: dict[str, Any] = {}
        model_alias: str | None = None
        last_progress_update = start_time
        pending_chunk_task: asyncio.Task[str] | None = None

        async def cancel_pending_chunk_task() -> None:
            nonlocal pending_chunk_task
            if pending_chunk_task is None:
                return
            pending_chunk_task.cancel()
            try:
                await asyncio.wait_for(pending_chunk_task, timeout=0.25)
            except BaseException:
                pass
            pending_chunk_task = None

        async def update_progress() -> None:
            nonlocal last_progress_update
            if not idempotency_key or not message_id:
                return
            last_progress_update = await update_idempotency_progress(
                cache_set=cache_set,
                key=idempotency_key,
                ttl=idempotency_ttl_seconds,
                started_at=idempotency_started_at or int(time.time()),
                response_chars=len(full_content),
                record_fields={
                    "message_id": message_id,
                    "mode": mode,
                    "level": level,
                },
                last_update_time=last_progress_update,
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
            yield emit("status", {"status": "Gathering context..."})
            yield emit(
                "meta",
                {"topic": topic, "level": level, "mode": mode, "message_id": message_id},
            )

            if not req.bypass_cache:
                cached = await cache_get(cache_key_value)
                if cached and cached.get("text"):
                    content = cached["text"]
                    for index in range(0, len(content), chunk_size):
                        yield emit("chunk", {"chunk": content[index : index + chunk_size]})
                    yield emit("done", "[DONE]")
                    if auth_data:
                        await persist_history(auth_data["user"], topic, [level], mode)
                    return

            stream = generate_stream_explanation(
                topic,
                level,
                mode=mode,
                temperature=req.temperature,
                regenerate=req.regenerate,
                request_id=request_id,
                user_id=user_id_raw,
                telemetry_sink=telemetry_sink,
            )
            stream_iter = _stream_chunks(stream)
            start_deadline = start_time + stream_start_timeout_seconds

            while True:
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
                    yield emit("heartbeat", {"ts": time.time()})
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
                await update_progress()
                yield emit("chunk", {"chunk": chunk})

            no_chunks = chunk_count == 0 and not full_content.strip()
            if (start_timeout or timed_out or no_chunks) and not full_content.strip():
                fallback_used = True
                fallback_timeout, fallback_timeout_cap_seconds, fallback_skipped_remaining_time = (
                    compute_fallback_timeout(
                        start_time=start_time,
                        stream_max_seconds=stream_max_seconds,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        log_context={"mode": mode, "level": level},
                    )
                )
                if fallback_timeout is None:
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
                    yield emit("done", "[DONE]")
                    return
                try:
                    fallback_content = await asyncio.wait_for(
                        generate_explanation(
                            topic,
                            level,
                            mode=mode,
                            temperature=req.temperature,
                            regenerate=req.regenerate,
                            request_id=request_id,
                            user_id=user_id_raw,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout,
                    )
                except Exception as exc:
                    is_timeout = isinstance(exc, asyncio.TimeoutError)
                    logger.error(
                        "streaming_fallback_failed",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        topic_hash=topic_hash,
                        error=_error_text(exc),
                        mode=mode,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
                    error_msg = "Streaming timed out. Please retry." if is_timeout else "An error occurred. Please retry."
                    yield emit("error", {"error": error_msg})
                    yield emit("done", "[DONE]")
                    return

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    yield emit("chunk", {"chunk": full_content[index : index + chunk_size]})
                yield emit("done", "[DONE]")
                if full_content.strip():
                    await cache_set(cache_key_value, {"text": full_content})
                if auth_data:
                    await persist_history(auth_data["user"], topic, [level], mode)
                return
            if timed_out:
                cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                yield emit("chunk", {"chunk": cutoff_message})

            if full_content.strip():
                await cache_set(cache_key_value, {"text": full_content})
            if auth_data:
                await persist_history(auth_data["user"], topic, [level], mode)

            yield emit("done", "[DONE]")
        except Exception as exc:
            logger.error(
                "streaming_failed",
                request_id=request_id,
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                error=_error_text(exc),
                mode=mode,
                retry=bool(req.regenerate),
                sampled=False,
            )
            if not full_content.strip():
                fallback_used = True
                fallback_timeout, fallback_timeout_cap_seconds, fallback_skipped_remaining_time = (
                    compute_fallback_timeout(
                        start_time=start_time,
                        stream_max_seconds=stream_max_seconds,
                        fallback_timeout_seconds=fallback_timeout_seconds,
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        log_context={"mode": mode, "level": level},
                    )
                )
                if fallback_timeout is None:
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
                    yield emit("done", "[DONE]")
                    return
                try:
                    fallback_content = await asyncio.wait_for(
                        generate_explanation(
                            topic,
                            level,
                            mode=mode,
                            temperature=req.temperature,
                            regenerate=req.regenerate,
                            request_id=request_id,
                            user_id=user_id_raw,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout,
                    )
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        record_chunk()
                        yield emit("chunk", {"chunk": full_content[index : index + chunk_size]})
                    yield emit("done", "[DONE]")
                    if full_content.strip():
                        await cache_set(cache_key_value, {"text": full_content})
                    if auth_data:
                        await persist_history(auth_data["user"], topic, [level], mode)
                    return
                except Exception as fallback_exc:
                    logger.error(
                        "streaming_exception_fallback_failed",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        topic_hash=topic_hash,
                        error=_error_text(fallback_exc),
                        original_error=_error_text(exc),
                        mode=mode,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
            if full_content.strip():
                mode_label = "technical " if mode == "technical" else ""
                yield emit("chunk", {"chunk": f"\n\n[Connection interrupted. Partial {mode_label}response delivered.]"})
                yield emit("done", "[DONE]")
                if full_content.strip():
                    await cache_set(cache_key_value, {"text": full_content})
                if auth_data:
                    await persist_history(auth_data["user"], topic, [level], mode)
                return
            yield emit("error", {"error": "An error occurred while streaming. Please try again."})
            yield emit("done", "[DONE]")
        finally:
            await cancel_pending_chunk_task()
            if idempotency_key and message_id:
                now_ts = int(time.time())
                if full_content.strip():
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "completed",
                            "response": full_content,
                            "message_id": message_id,
                            "mode": mode,
                            "level": level,
                            "last_update_ts": now_ts,
                            "response_chars": len(full_content),
                        },
                        ttl=idempotency_ttl_seconds,
                    )
                else:
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "failed",
                            "message_id": message_id,
                            "mode": mode,
                            "level": level,
                            "last_update_ts": now_ts,
                            "response_chars": len(full_content),
                        },
                        ttl=idempotency_ttl_seconds,
                    )
            total_ms = (time.perf_counter() - start_time) * 1000
            avg_chunk_interval_ms = None
            if chunk_count > 1:
                avg_chunk_interval_ms = total_chunk_interval_ms / (chunk_count - 1)
            queue_time_ms = round((queue_started - request_received) * 1000, 2)
            model_alias = str(telemetry_sink.get("model_alias") or mode)
            model_inference_ms = telemetry_sink.get("model_inference_ms")
            stream_duration_ms = telemetry_sink.get("stream_duration_ms")
            token_usage = telemetry_sink.get("token_usage")
            estimated_cost_usd = telemetry_sink.get("estimated_cost_usd")
            log_sampled_success(
                "query_stream_observed",
                request_id=request_id,
                user_id_hash=user_id_hash,
                model_alias=model_alias,
                level=level,
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
                timed_out=timed_out,
                fallback_used=fallback_used,
                fallback_timeout_cap_seconds=round(fallback_timeout_cap_seconds, 2)
                if fallback_timeout_cap_seconds is not None
                else None,
                fallback_skipped_remaining_time=fallback_skipped_remaining_time,
                stream_max_seconds=stream_max_seconds,
                sampled=True,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )
