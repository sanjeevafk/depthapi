"""Streaming helper utilities extracted from pipeline."""

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from monitoring import capture_telemetry_event

import api.services.cache as cache_module
from api.services.analytics import build_llm_request_payload, record_llm_request
from api.services.message_gate import cache_set_value
from api.services.conversation_context import ConversationMessage
from api.services.message_dispatcher import MessageDispatcher
from api.services.inference import generate_explanation
from api.services.redis_safe import safe_redis_call
from api.logging_config import logger, log_sampled_success
from api.utils import SOCRATIC_MODE, TECHNICAL_MODE


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


def build_replay_response(
    *,
    content: str,
    message_id: str,
    assistant_message_id: str | None,
    mode: str,
    prompt_mode: str,
    message_dispatcher: MessageDispatcher,
) -> StreamingResponse:
    return message_dispatcher.dispatch_normal_message(
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


async def run_fallback_generation(
    *,
    effective_content: str,
    prompt_mode: str,
    llm_mode: str,
    request_temperature: float,
    regenerate: bool,
    request_id: str,
    user_id: str,
    is_pro: bool,
    telemetry_sink: dict[str, Any],
    conversation_messages: list[ConversationMessage],
    conversation_context: str,
    intent_system_prompt: str,
    fallback_timeout_seconds: float,
    collection_id: str | None = None,
    use_trusted_corpus: bool = True,
) -> str:
    result = await asyncio.wait_for(
        generate_explanation(
            effective_content,
            prompt_mode,
            mode=llm_mode,
            temperature=request_temperature,
            regenerate=regenerate,
            request_id=request_id,
            user_id=user_id,
            is_pro=is_pro,
            telemetry_sink=telemetry_sink,
            conversation_messages=conversation_messages,
            conversation_context=conversation_context,
            intent_system_prompt=intent_system_prompt,
            collection_id=collection_id,
            use_trusted_corpus=use_trusted_corpus,
        ),
        timeout=fallback_timeout_seconds,
    )
    return str(result)


async def drain_stream_chunks(
    *,
    request: Request,
    stream_iter: Any,
    stream: Any,
    start_time: float,
    start_deadline: float,
    stream_max_seconds: int,
    heartbeat_seconds: float,
    assistant_message_id: str,
    emit: Any,
    record_chunk: Any,
    close_stream: Any,
    close_timeout_seconds: float,
    request_id: str,
    user_id_hash: str,
    conversation_id: str,
    mode: str,
    chunk_count: int,
) -> AsyncGenerator[tuple[str, str | None, bool, bool, bool, str | None, bool], None]:
    pending_chunk_task: asyncio.Task[str] | None = None
    timed_out = False
    start_timeout = False
    aborted = False
    abort_reason: str | None = None
    stream_completed = False

    async def cancel_pending_chunk_task() -> None:
        nonlocal pending_chunk_task
        if pending_chunk_task is None:
            return
        pending_chunk_task.cancel()
        try:
            await asyncio.wait_for(pending_chunk_task, timeout=close_timeout_seconds)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(
                "messages_pending_chunk_cancel_failed",
                request_id=request_id,
                conversation_id=conversation_id,
                error=str(exc),
            )
        pending_chunk_task = None

    seen_chunks = chunk_count
    try:
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
            if seen_chunks == 0:
                timeout = min(timeout, max(0.0, start_deadline - time.perf_counter()))
                if timeout <= 0:
                    start_timeout = True
                    await cancel_pending_chunk_task()
                    await close_stream(stream)
                    break

            try:
                if pending_chunk_task is None:
                    async def get_next_chunk() -> str:
                        return await anext(stream_iter)
                    pending_chunk_task = asyncio.create_task(get_next_chunk())
                chunk = await asyncio.wait_for(asyncio.shield(pending_chunk_task), timeout=timeout)
                pending_chunk_task = None
            except asyncio.TimeoutError:
                yield emit("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()}), None, timed_out, start_timeout, aborted, abort_reason, stream_completed
                if seen_chunks == 0 and time.perf_counter() >= start_deadline:
                    start_timeout = True
                    await cancel_pending_chunk_task()
                    await close_stream(stream)
                    break
                continue
            except StopAsyncIteration:
                pending_chunk_task = None
                stream_completed = True
                break

            record_chunk()
            seen_chunks += 1
            yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id}), chunk, timed_out, start_timeout, aborted, abort_reason, stream_completed
    finally:
        await cancel_pending_chunk_task()

    yield "", None, timed_out, start_timeout, aborted, abort_reason, stream_completed


async def finalize_stream_side_effects(
    *,
    stream: Any,
    close_stream: Any,
    client_message_id: str,
    start_time: float,
    request_received: float,
    chunk_count: int,
    total_chunk_interval_ms: float,
    aborted: bool,
    abort_reason: str | None,
    request_id: str,
    user_id_hash: str,
    conversation_id: str,
    telemetry_sink: dict[str, Any],
    full_content: str,
    gatekeeper: Any,
    idempotency_key: str,
    idempotency_ttl_seconds: int,
    assistant_message_id: str,
    selected_mode: str,
    prompt_mode: str,
    regenerate: bool,
    is_pro: bool,
    first_event_ms: float | None,
    first_token_ms: float | None,
    chunk_size: int,
    generation_ms: float | None,
    timed_out: bool,
    start_timeout: bool,
    fallback_used: bool,
    stream_max_seconds: int,
    redis_eval_ms: float,
    prompt_build_ms: float,
    redis_degraded: bool,
    redis_append_failed: bool,
    snapshot_degraded: bool,
    stream_failed: bool,
    user_id: str,
    lock_released: bool,
    ingress_dedupe_clear: Callable[[str], Any],
    release_lock: Callable[[str], None],
) -> bool:
    if stream is not None:
        await close_stream(stream)
    await ingress_dedupe_clear(client_message_id)

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
            reason=abort_reason,
            tokens_after_abort=0,
        )
    queue_time_ms = round((start_time - request_received) * 1000, 2)
    model_inference_ms = telemetry_sink.get("model_inference_ms")
    stream_duration_ms = telemetry_sink.get("stream_duration_ms")
    token_usage = telemetry_sink.get("token_usage")
    estimated_cost_usd = telemetry_sink.get("estimated_cost_usd")
    if not gatekeeper.degraded:
        try:
            redis = await safe_redis_call(cache_module.get_redis, operation="connect")
            if full_content.strip():
                response_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
                if redis is not None:
                    await safe_redis_call(redis.hset, idempotency_key, "status", "COMPLETED", operation="hset")
                    await safe_redis_call(redis.hset, idempotency_key, "response", full_content, operation="hset")
                    await safe_redis_call(redis.hset, idempotency_key, "response_hash", response_hash, operation="hset")
                    await safe_redis_call(
                        redis.hset,
                        idempotency_key,
                        "assistant_message_id",
                        assistant_message_id,
                        operation="hset",
                    )
                    await safe_redis_call(
                        redis.hset,
                        idempotency_key,
                        "completed_at",
                        int(time.time()),
                        operation="hset",
                    )
            else:
                if redis is not None:
                    await safe_redis_call(redis.hset, idempotency_key, "status", "EXPIRED", operation="hset")
                    await safe_redis_call(
                        redis.hset,
                        idempotency_key,
                        "expired_at",
                        int(time.time()),
                        operation="hset",
                    )
            if redis is not None:
                await safe_redis_call(redis.expire, idempotency_key, idempotency_ttl_seconds, operation="expire")
        except Exception as exc:
            logger.warning(
                "messages_idempotency_update_failed",
                request_id=request_id,
                error=str(exc),
            )
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
        retry=bool(regenerate),
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
        redis_eval_ms=redis_eval_ms,
        prompt_build_ms=round(prompt_build_ms, 2),
        time_to_first_token=round(first_token_ms, 2) if first_token_ms is not None else None,
        redis_degraded=redis_degraded,
        redis_append_failed=redis_append_failed,
        snapshot_degraded=snapshot_degraded,
        sampled=True,
    )
    status = "success"
    if aborted:
        status = "aborted"
    elif timed_out or start_timeout:
        status = "timed_out"
    elif stream_failed:
        status = "error"
    asyncio.create_task(
        capture_telemetry_async(
            "stream_end",
            request_id=request_id,
            user_id_hash=user_id_hash,
            mode=selected_mode,
            prompt_mode=prompt_mode,
            regenerate=bool(regenerate),
            status=status,
            duration_ms=round(total_ms, 2),
            fallback_used=fallback_used,
        )
    )
    error_type = None
    error_message = None
    if status == "error":
        error_type = "stream_failed"
        error_message = "Streaming failed"
    elif status == "timed_out":
        error_type = "timed_out"
        error_message = "Streaming timed out"
    elif status == "aborted":
        error_type = "aborted"
        error_message = "User aborted stream"
    safe_user_id = user_id or None
    payload = build_llm_request_payload(
        request_id=request_id,
        user_id=safe_user_id,
        conversation_id=str(conversation_id or "") or None,
        model_alias=str(telemetry_sink.get("model_alias") or selected_mode),
        model_name=telemetry_sink.get("model"),
        provider=telemetry_sink.get("provider"),
        mode=selected_mode,
        status=status,
        token_usage=token_usage if isinstance(token_usage, dict) else None,
        estimated_cost_usd=estimated_cost_usd,
        latency_ms=round(total_ms, 2),
        model_inference_ms=model_inference_ms,
        stream_duration_ms=stream_duration_ms,
        error_type=error_type,
        error_message=error_message,
    )
    asyncio.create_task(record_llm_request(payload))
    if not lock_released:
        release_lock(conversation_id)
        return True
    return lock_released
