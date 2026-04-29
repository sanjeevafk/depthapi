"""Streaming event loop extracted from StreamingMessagePipeline."""

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable

import orjson
from fastapi import Request

from api.logging_config import logger, log_sampled_success
from api.prompts import SYSTEM_PROMPT
from api.services.inference_message_builder import MODE_SYSTEM_PROMPTS
from api.services.inference import generate_stream_explanation
from api.services.llm_client import get_provider_config_state
from api.services.message_gate import append_conversation_message
from api.services.streaming import SseEventBuilder
from api.services.stream_helpers import (
    capture_telemetry_async,
    drain_stream_chunks,
    final_fallback_message,
    finalize_stream_side_effects,
    run_fallback_generation,
)
from api.services.stream_event_finalize import finalize_assistant_message
from api.services.stream_persistence import StreamPersistence
from api.services.token_count import count_prompt_tokens
from api.services.conversation_context import ConversationMessage
from api.utils import SOCRATIC_MODE, TECHNICAL_MODE


class StreamEventLoop:
    """Encapsulates the SSE event generator for message streaming."""

    def __init__(
        self,
        *,
        request: Request,
        request_received: float,
        request_id: str,
        req: Any,
        user_id: str,
        is_pro: bool,
        user_id_hash: str,
        content: str,
        content_hash: str,
        client_message_id: str,
        assistant_client_id: str,
        idempotency_key: str,
        idempotency_key_hash: str,
        selected_mode: str,
        llm_mode: str,
        prompt_mode: str,
        request_temperature: float,
        cache_ttl_seconds: int,
        stream_max_seconds: int,
        fallback_timeout_seconds: float,
        close_timeout_seconds: float,
        heartbeat_seconds: float,
        stream_start_timeout_seconds: float,
        idempotency_ttl_seconds: int,
        history_limit: int,
        ack_message: str | None,
        intent_system_prompt: str | None,
        socratic_context: str,
        context_messages: list[ConversationMessage],
        ensure_context_for_stream: Callable[[], Any],
        cache_key: str,
        cached_response: str | None,
        gatekeeper: Any,
        redis_eval_ms: float | None,
        redis_degraded: bool,
        force_non_stream: bool,
        assistant_message_id: str,
        persistence: StreamPersistence,
        ingress_dedupe_clear: Callable[[str], Any],
        release_lock: Callable[[str], None],
    ) -> None:
        self.request = request
        self.request_received = request_received
        self.request_id = request_id
        self.req = req
        self.user_id = user_id
        self.is_pro = is_pro
        self.user_id_hash = user_id_hash
        self.content = content
        self.content_hash = content_hash
        self.client_message_id = client_message_id
        self.assistant_client_id = assistant_client_id
        self.idempotency_key = idempotency_key
        self.idempotency_key_hash = idempotency_key_hash
        self.selected_mode = selected_mode
        self.llm_mode = llm_mode
        self.prompt_mode = prompt_mode
        self.request_temperature = request_temperature
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stream_max_seconds = stream_max_seconds
        self.fallback_timeout_seconds = fallback_timeout_seconds
        self.close_timeout_seconds = close_timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stream_start_timeout_seconds = stream_start_timeout_seconds
        self.idempotency_ttl_seconds = idempotency_ttl_seconds
        self.history_limit = history_limit
        self.ack_message = ack_message
        self.intent_system_prompt = intent_system_prompt or ""
        self.socratic_context = socratic_context
        self.context_messages = context_messages
        self.ensure_context_for_stream = ensure_context_for_stream
        self.cache_key = cache_key
        self.cached_response = cached_response
        self.gatekeeper = gatekeeper
        self.redis_eval_ms = float(redis_eval_ms) if redis_eval_ms is not None else 0.0
        self.redis_degraded = redis_degraded
        self.force_non_stream = force_non_stream
        self.assistant_message_id = assistant_message_id
        self.persistence = persistence
        self.ingress_dedupe_clear = ingress_dedupe_clear
        self.release_lock = release_lock

    async def run(self) -> AsyncGenerator[str, None]:
        start_time = time.perf_counter()
        full_content = ""
        stream_completed = False
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
        assistant_sequence_id: int | None = None
        redis_append_failed_ref = {"value": False}

        asyncio.create_task(
            capture_telemetry_async(
                "stream_start",
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                mode=self.selected_mode,
                prompt_mode=self.prompt_mode,
                regenerate=bool(self.req.regenerate),
            )
        )

        def record_chunk() -> None:
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

        async def close_stream(stream: Any) -> None:
            close_fn = getattr(stream, "aclose", None)
            if close_fn:
                try:
                    close_task = asyncio.create_task(close_fn())
                    try:
                        await asyncio.wait_for(close_task, timeout=self.close_timeout_seconds)
                    except asyncio.TimeoutError:
                        close_task.cancel()
                        raise
                except asyncio.TimeoutError:
                    logger.warning(
                        "messages_stream_close_timeout",
                        request_id=self.request_id,
                        user_id_hash=self.user_id_hash,
                        conversation_id=self.req.conversation_id,
                        mode=self.selected_mode,
                        sampled=False,
                    )
                except Exception as exc:
                    logger.debug(
                        "messages_stream_close_failed",
                        request_id=self.request_id,
                        conversation_id=self.req.conversation_id,
                        error=str(exc),
                    )

        async def finalize_message(
            content_value: str,
            *,
            cacheable: bool = True,
            stream_completed: bool = False,
        ) -> int | None:
            return await finalize_assistant_message(
                content_value=content_value,
                cacheable=cacheable,
                stream_completed=stream_completed,
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.req.conversation_id,
                assistant_client_id=self.assistant_client_id,
                assistant_message_id=self.assistant_message_id,
                history_limit=self.history_limit,
                cache_key=self.cache_key,
                cache_ttl_seconds=self.cache_ttl_seconds,
                idempotency_key=self.idempotency_key,
                idempotency_ttl_seconds=self.idempotency_ttl_seconds,
                idempotency_key_hash=self.idempotency_key_hash,
                gatekeeper=self.gatekeeper,
                persistence=self.persistence,
                schedule_task=asyncio.create_task,
                redis_append_failed_ref=redis_append_failed_ref,
            )

        stream = None
        try:
            pre_stream_latency = time.perf_counter() - self.request_received
            if pre_stream_latency >= 0.2:
                logger.warning(
                    "messages_pre_stream_latency_high",
                    request_id=self.request_id,
                    conversation_id=self.req.conversation_id,
                    pre_stream_latency_ms=round(pre_stream_latency * 1000, 2),
                )
            yield emit("start", {"type": "start"})
            meta_payload = {
                "assistant_message_id": self.assistant_message_id,
                "mode": self.selected_mode,
                "prompt_mode": self.prompt_mode,
                "message_id": self.client_message_id,
            }
            if self.cached_response:
                meta_payload["replay"] = "true"
            yield emit("meta", meta_payload)

            user_payload = {
                "role": "user",
                "content": self.content,
                "sequence_id": "__SEQ__",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "client_id": self.client_message_id,
            }
            user_sequence_id = await append_conversation_message(
                conversation_id=self.req.conversation_id,
                message_json=orjson.dumps(user_payload).decode("utf-8"),
                max_messages=self.history_limit,
                timeout_seconds=0.8,
            )
            if user_sequence_id is None:
                redis_append_failed_ref["value"] = True
                self.force_non_stream = True
            asyncio.create_task(self.persistence.persist_user_message(user_sequence_id))
            asyncio.create_task(self.persistence.persist_conversation_update())

            if self.ack_message:
                full_content = self.ack_message
                assistant_payload = {
                    "role": "assistant",
                    "content": full_content,
                    "sequence_id": "__SEQ__",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "assistant_client_id": self.assistant_client_id,
                }
                assistant_sequence_id = await append_conversation_message(
                    conversation_id=self.req.conversation_id,
                    message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                    max_messages=self.history_limit,
                    timeout_seconds=0.8,
                )
                if assistant_sequence_id is None:
                    redis_append_failed_ref["value"] = True
                asyncio.create_task(self.persistence.persist_assistant_message(assistant_sequence_id, full_content))
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": self.assistant_message_id})
                yield emit("done", "[DONE]")
                logger.info(
                    "messages_response_completed",
                    request_id=self.request_id,
                    response_length=len(full_content),
                    stream_completed=True,
                    cached=False,
                    idempotency_key_hash=self.idempotency_key_hash,
                )
                return

            if self.cached_response:
                telemetry_sink["token_usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                log_sampled_success(
                    "messages_cache_hit",
                    request_id=self.request_id,
                    user_id_hash=self.user_id_hash,
                    model_alias="cache",
                    latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    estimated_cost_usd=0.0,
                    retry=bool(self.req.regenerate),
                    conversation_id=self.req.conversation_id,
                    sampled=True,
                )
                full_content = self.cached_response
                assistant_payload = {
                    "role": "assistant",
                    "content": full_content,
                    "sequence_id": "__SEQ__",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "assistant_client_id": self.assistant_client_id,
                }
                assistant_sequence_id = await append_conversation_message(
                    conversation_id=self.req.conversation_id,
                    message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                    max_messages=self.history_limit,
                    timeout_seconds=0.8,
                )
                if assistant_sequence_id is None:
                    redis_append_failed_ref["value"] = True
                asyncio.create_task(self.persistence.persist_assistant_message(assistant_sequence_id, full_content))
                for index in range(0, len(self.cached_response), chunk_size):
                    chunk = self.cached_response[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": self.assistant_message_id})
                yield emit("done", "[DONE]")
                logger.info(
                    "messages_response_completed",
                    request_id=self.request_id,
                    response_length=len(self.cached_response),
                    stream_completed=True,
                    cached=True,
                    idempotency_key_hash=self.idempotency_key_hash,
                )
                return

            if self.force_non_stream:
                await self.ensure_context_for_stream()
                try:
                    fallback_content = await run_fallback_generation(
                        effective_content=self.content,
                        prompt_mode=self.prompt_mode,
                        llm_mode=self.llm_mode,
                        request_temperature=self.request_temperature,
                        regenerate=self.req.regenerate,
                        request_id=self.request_id,
                        user_id=self.user_id,
                        is_pro=self.is_pro,
                        telemetry_sink=telemetry_sink,
                        conversation_messages=self.context_messages,
                        conversation_context=self.socratic_context,
                        intent_system_prompt=self.intent_system_prompt,
                        fallback_timeout_seconds=self.fallback_timeout_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        "messages_non_stream_fallback_failed",
                        error=str(exc),
                        request_id=self.request_id,
                        user_id_hash=self.user_id_hash,
                        conversation_id=self.req.conversation_id,
                        content_hash=self.content_hash,
                        mode=self.selected_mode,
                        sampled=False,
                    )
                    fallback_content = final_fallback_message(self.selected_mode)

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": self.assistant_message_id})
                yield emit("done", "[DONE]")
                await finalize_message(
                    full_content,
                    cacheable=not self.req.regenerate,
                    stream_completed=True,
                )
                return

            system_parts: list[str] = []
            await self.ensure_context_for_stream()
            base_prompt = SYSTEM_PROMPT.strip()
            if base_prompt:
                system_parts.append(base_prompt)
            mode_prompt = MODE_SYSTEM_PROMPTS.get(self.selected_mode, "").strip()
            if mode_prompt:
                system_parts.append(mode_prompt)
            if self.intent_system_prompt:
                system_parts.append(self.intent_system_prompt.strip())

            prompt_messages: list[ConversationMessage] = []
            if system_parts:
                prompt_messages.append({"role": "system", "content": "\n".join(system_parts)})
            prompt_messages.extend(self.context_messages)
            prompt_messages.append({"role": "user", "content": self.content})

            prompt_hash_base = "\n".join(
                f"{msg['role']}:{msg['content']}" for msg in prompt_messages
            )
            final_prompt_hash = hashlib.sha256(prompt_hash_base.encode("utf-8")).hexdigest()

            logger.info(
                "messages_prompt_assembled",
                request_id=self.request_id,
                model_alias=str(get_provider_config_state().get("model_alias")),
                prompt_token_count=count_prompt_tokens(self.content),
                final_prompt_hash_prefix=final_prompt_hash[:16],
                message_chain_length=len(prompt_messages),
                system_prompt_present=any(msg["role"] == "system" for msg in prompt_messages),
            )

            generation_start = time.perf_counter()
            stream = generate_stream_explanation(
                self.content,
                self.prompt_mode,
                mode=self.llm_mode,
                temperature=self.request_temperature,
                regenerate=self.req.regenerate,
                request_id=self.request_id,
                user_id=self.user_id,
                is_pro=self.is_pro,
                telemetry_sink=telemetry_sink,
                conversation_messages=self.context_messages,
                conversation_context=self.socratic_context,
                intent_system_prompt=self.intent_system_prompt,
            )
            stream_iter = stream.__aiter__()
            start_deadline = start_time + self.stream_start_timeout_seconds
            async for event, chunk_text, timed_out, start_timeout, aborted, abort_reason, stream_completed in drain_stream_chunks(
                request=self.request,
                stream_iter=stream_iter,
                stream=stream,
                start_time=start_time,
                start_deadline=start_deadline,
                stream_max_seconds=self.stream_max_seconds,
                heartbeat_seconds=self.heartbeat_seconds,
                assistant_message_id=self.assistant_message_id,
                emit=emit,
                record_chunk=record_chunk,
                close_stream=close_stream,
                close_timeout_seconds=self.close_timeout_seconds,
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.req.conversation_id,
                mode=self.selected_mode,
                chunk_count=chunk_count,
            ):
                if event:
                    if chunk_text is not None:
                        full_content += chunk_text
                    yield event

            generation_ms = (time.perf_counter() - generation_start) * 1000

            no_chunks = chunk_count == 0 and not full_content.strip()
            if (start_timeout or timed_out or no_chunks) and not full_content.strip() and not aborted:
                fallback_used = True
                logger.warning(
                    "messages_stream_fallback",
                    request_id=self.request_id,
                    user_id_hash=self.user_id_hash,
                    reason=(
                        "start_timeout"
                        if start_timeout
                        else "max_duration"
                        if timed_out
                        else "empty_stream"
                    ),
                    conversation_id=self.req.conversation_id,
                    message_id=self.client_message_id,
                    retry=bool(self.req.regenerate),
                    sampled=False,
                )
                try:
                    fallback_content = await run_fallback_generation(
                        effective_content=self.content,
                        prompt_mode=self.prompt_mode,
                        llm_mode=self.llm_mode,
                        request_temperature=self.request_temperature,
                        regenerate=self.req.regenerate,
                        request_id=self.request_id,
                        user_id=self.user_id,
                        is_pro=self.is_pro,
                        telemetry_sink=telemetry_sink,
                        conversation_messages=self.context_messages,
                        conversation_context=self.socratic_context,
                        intent_system_prompt=self.intent_system_prompt,
                        fallback_timeout_seconds=self.fallback_timeout_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        "messages_fallback_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        request_id=self.request_id,
                        user_id_hash=self.user_id_hash,
                        conversation_id=self.req.conversation_id,
                        content_hash=self.content_hash,
                        mode=self.selected_mode,
                        fallback_timeout_seconds=self.fallback_timeout_seconds,
                        retry=bool(self.req.regenerate),
                        sampled=False,
                    )
                    full_content = final_fallback_message(self.selected_mode)
                    yield emit("delta", {"delta": full_content, "assistant_message_id": self.assistant_message_id})
                    await finalize_message(
                        full_content,
                        cacheable=not self.req.regenerate,
                        stream_completed=True,
                    )
                    yield emit("done", "[DONE]")
                    return

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": self.assistant_message_id})
                yield emit("done", "[DONE]")
                await finalize_message(
                    full_content,
                    cacheable=not self.req.regenerate,
                    stream_completed=True,
                )
                return

            response_truncated = bool(timed_out and not aborted)
            if response_truncated:
                cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                full_content += cutoff_message
                yield emit("delta", {"delta": cutoff_message, "assistant_message_id": self.assistant_message_id})

            if full_content.strip():
                await finalize_message(
                    full_content,
                    cacheable=not self.req.regenerate,
                    stream_completed=stream_completed,
                )

            if not aborted:
                yield emit("done", "[DONE]")
        except Exception as exc:
            stream_failed = True
            logger.error(
                "messages_stream_failed",
                error=str(exc),
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.req.conversation_id,
                content_hash=self.content_hash,
                retry=bool(self.req.regenerate),
                sampled=False,
            )
            if not aborted and not full_content.strip():
                fallback_used = True
                try:
                    fallback_content = await run_fallback_generation(
                        effective_content=self.content,
                        prompt_mode=self.prompt_mode,
                        llm_mode=self.llm_mode,
                        request_temperature=self.request_temperature,
                        regenerate=self.req.regenerate,
                        request_id=self.request_id,
                        user_id=self.user_id,
                        is_pro=self.is_pro,
                        telemetry_sink=telemetry_sink,
                        conversation_messages=self.context_messages,
                        conversation_context=self.socratic_context,
                        intent_system_prompt=self.intent_system_prompt,
                        fallback_timeout_seconds=self.fallback_timeout_seconds,
                    )
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        chunk = full_content[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": self.assistant_message_id})
                    yield emit("done", "[DONE]")
                    await finalize_message(
                        full_content,
                        cacheable=not self.req.regenerate,
                        stream_completed=True,
                    )
                    return
                except Exception as fallback_exc:
                    logger.error(
                        "messages_exception_fallback_failed",
                        error=str(fallback_exc),
                        error_type=type(fallback_exc).__name__,
                        request_id=self.request_id,
                        user_id_hash=self.user_id_hash,
                        conversation_id=self.req.conversation_id,
                        content_hash=self.content_hash,
                        mode=self.selected_mode,
                        fallback_timeout_seconds=self.fallback_timeout_seconds,
                        retry=bool(self.req.regenerate),
                        sampled=False,
                    )
                    full_content = final_fallback_message(self.selected_mode)
                    yield emit("delta", {"delta": full_content, "assistant_message_id": self.assistant_message_id})
                    await finalize_message(
                        full_content,
                        cacheable=not self.req.regenerate,
                        stream_completed=True,
                    )
                    yield emit("done", "[DONE]")
                    return
            if aborted:
                return
            if full_content.strip():
                await finalize_message(
                    full_content,
                    cacheable=not self.req.regenerate and not response_truncated,
                    stream_completed=False,
                )
                mode_label = ""
                if self.selected_mode == TECHNICAL_MODE:
                    mode_label = "technical "
                elif self.selected_mode == SOCRATIC_MODE:
                    mode_label = "socratic "
                yield emit(
                    "delta",
                    {
                        "delta": f"\n\n[Connection interrupted. Partial {mode_label}response delivered.]",
                        "assistant_message_id": self.assistant_message_id,
                    },
                )
                yield emit("done", "[DONE]")
                return
            yield emit("error", {"error": "Streaming failed"})
            yield emit("done", "[DONE]")
        finally:
            await finalize_stream_side_effects(
                stream=stream,
                close_stream=close_stream,
                client_message_id=self.client_message_id,
                start_time=start_time,
                request_received=self.request_received,
                chunk_count=chunk_count,
                total_chunk_interval_ms=total_chunk_interval_ms,
                aborted=aborted,
                abort_reason=abort_reason,
                request_id=self.request_id,
                user_id_hash=self.user_id_hash,
                conversation_id=self.req.conversation_id,
                telemetry_sink=telemetry_sink,
                full_content=full_content,
                gatekeeper=self.gatekeeper,
                idempotency_key=self.idempotency_key,
                idempotency_ttl_seconds=self.idempotency_ttl_seconds,
                assistant_message_id=self.assistant_message_id,
                selected_mode=self.selected_mode,
                prompt_mode=self.prompt_mode,
                regenerate=bool(self.req.regenerate),
                is_pro=self.is_pro,
                first_event_ms=first_event_ms,
                first_token_ms=first_token_ms,
                chunk_size=chunk_size,
                generation_ms=generation_ms,
                timed_out=timed_out,
                start_timeout=start_timeout,
                fallback_used=fallback_used,
                stream_max_seconds=self.stream_max_seconds,
                redis_eval_ms=self.redis_eval_ms,
                prompt_build_ms=0.0,
                redis_degraded=self.redis_degraded,
                redis_append_failed=redis_append_failed_ref["value"],
                snapshot_degraded=False,
                stream_failed=stream_failed,
                user_id=self.user_id,
                lock_released=False,
                ingress_dedupe_clear=self.ingress_dedupe_clear,
                release_lock=self.release_lock,
            )
