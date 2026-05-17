"""Streaming message pipeline extracted from messages_core handler."""

import asyncio
import time
import uuid
from typing import Any, Callable
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from api.auth import get_supabase_admin
from api.config import CONTEXT_LOAD_TIMEOUTS, get_stream_config
from api.logging_config import logger
from api.services.messaging.message_gate import cache_get_value
from api.services.conversation.conversation_context import ConversationMessage
from api.services.conversation.conversation_intent import (
    ConversationIntent,
    classify_conversation_intent,
    build_intent_system_prompt,
)
from api.prompts import SYSTEM_PROMPT
from api.services.inference.inference_constants import TECHNICAL_MAX_TOKENS
from api.services.inference.inference_message_builder import MODE_SYSTEM_PROMPTS
from api.services.inference.llm_client import get_provider_config_state
from api.services.messaging.message_dispatcher import MessageDispatcher
from api.services.messaging.message_gate import gatekeep_message_request
from api.services.security.rate_limit import _resolve_limits
from api.services.messaging.stream_event_loop import StreamEventLoop
from api.services.messaging.stream_persistence import StreamPersistence
from api.services.messaging.stream_helpers import (
    ack_response,
    build_replay_response,
    capture_telemetry_async,
    message_cache_key,
    resolve_client_ip,
    trusted_proxies_from_settings,
)
from api.services.messaging.token_count import count_prompt_tokens
from api.utils import SOCRATIC_MODE, TECHNICAL_MODE, with_timeout


class StreamingMessagePipeline:
    """Pipeline extracted from messages_core handler to keep behavior unchanged."""

    def __init__(
        self,
        *,
        context_builder: Any,
        message_dispatcher: MessageDispatcher,
        lock_manager: Any,
        ingress_dedupe_clear: Callable[[str], Any],
    ) -> None:
        self.context_builder = context_builder
        self.message_dispatcher = message_dispatcher
        self.lock_manager = lock_manager
        self.ingress_dedupe_clear = ingress_dedupe_clear

    async def execute(
        self,
        *,
        request: Request,
        api_key: Any,
        preflight: Any,
        setup: Any,
    ) -> StreamingResponse:
        """Execute the full streaming pipeline using preflight/setup results."""
        request_received = preflight.request_received
        request_id = preflight.request_id
        req = preflight.req
        normalized_mode = preflight.normalized_mode
        content = preflight.content
        user_id = preflight.user_id
        is_pro = preflight.is_pro
        user_id_hash = preflight.user_id_hash
        content_hash = preflight.content_hash
        client_message_id = preflight.client_message_id
        assistant_client_id = preflight.assistant_client_id
        idempotency_key = preflight.idempotency_key
        idempotency_key_hash = preflight.idempotency_key_hash
        snapshot_ms = 0.0
        db_ms = 0.0
        snapshot_degraded = False

        lock_acquired = await self.lock_manager.acquire(req.conversation_id, timeout_seconds=1.0)
        if not lock_acquired:
            raise HTTPException(
                status_code=429,
                detail="Another request for this conversation is already processing. Please retry.",
                headers={"Retry-After": "2"},
            )
        lock_released = False
        response_started = False

        try:
            config_state = get_provider_config_state()
            config_settings = setup.config_settings
            is_prod = setup.is_prod
            cache_ttl_seconds = setup.cache_ttl_seconds
            stream_max_seconds = setup.stream_max_seconds
            fallback_budget_seconds = setup.fallback_budget_seconds
            fallback_timeout_seconds = setup.fallback_timeout_seconds
            close_timeout_seconds = setup.close_timeout_seconds
            heartbeat_seconds = setup.heartbeat_seconds
            stream_start_timeout_seconds = setup.stream_start_timeout_seconds
            idempotency_ttl_seconds = setup.idempotency_ttl_seconds
            snapshot_meta_raw = setup.snapshot_meta_raw
            snapshot_raw_messages = setup.snapshot_raw_messages
            snapshot_meta = setup.snapshot_meta
            snapshot_ms = setup.snapshot_ms
            snapshot_degraded = setup.snapshot_degraded
            selected_mode = setup.selected_mode
            llm_mode = setup.llm_mode
            prompt_mode = setup.prompt_mode

            trusted_proxies = trusted_proxies_from_settings(config_settings)
            history_limit = max(int(getattr(config_settings, "conversation_context_fetch_limit", 80)), 1)

            asyncio.create_task(
                capture_telemetry_async(
                    "message_send",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    mode=selected_mode,
                    prompt_mode=prompt_mode,
                    regenerate=bool(req.regenerate),
                )
            )

            logger.info(
                "messages_request_validated",
                request_id=request_id,
                user_id_hash=user_id_hash,
                normalized_mode=selected_mode,
                requested_mode=normalized_mode,
                validated_payload={
                    "conversation_id": req.conversation_id,
                    "content_length": len(content),
                    "content_hash": content_hash,
                    "client_generated_id": req.client_generated_id,
                    "assistant_client_id": req.assistant_client_id,
                    "prompt_mode": prompt_mode,
                },
            )

            # ── Conversation context & intent ──────────────────────────────
            history_messages = await self.context_builder.parse_snapshot_messages(
                snapshot_raw_messages,
                req.conversation_id,
            )
            if not history_messages:
                db_start = time.perf_counter()
                db_result = await with_timeout(
                    self.context_builder.load_conversation_from_db(
                        req.conversation_id,
                        user_id,
                        history_limit,
                        get_supabase_admin_fn=get_supabase_admin,
                    ),
                    timeout_seconds=CONTEXT_LOAD_TIMEOUTS["db_context"],
                    default=({}, []),
                    context_label="db_context_load",
                    swallow_exceptions=True,
                )
                if db_result is None:
                    db_meta, db_messages = {}, []
                else:
                    db_meta, db_messages = db_result
                db_ms = (time.perf_counter() - db_start) * 1000
                logger.info(
                    "timing_db_load",
                    request_id=request_id,
                    conversation_id=req.conversation_id,
                    db_ms=round(db_ms, 2),
                    db_messages_count=len(db_messages),
                )
                if db_meta:
                    snapshot_meta = db_meta
                if db_messages:
                    history_messages = db_messages
                    logger.info(
                        "messages_context_db_fallback",
                        request_id=request_id,
                        conversation_id=req.conversation_id,
                        history_length=len(history_messages),
                    )
                if not snapshot_meta and not history_messages and get_supabase_admin() is not None:
                    await self.ingress_dedupe_clear(client_message_id)
                    raise HTTPException(status_code=404, detail="Conversation not found")
            last_user_message, last_assistant_message = self.context_builder.extract_turns(history_messages)
            has_prior = bool(last_user_message or last_assistant_message)
            intent = await with_timeout(
                asyncio.to_thread(classify_conversation_intent, content, has_prior=has_prior),
                timeout_seconds=CONTEXT_LOAD_TIMEOUTS["intent_classify"],
                default=ConversationIntent(type="new_query", reason="intent_timeout_default"),
                context_label="intent_classification",
                swallow_exceptions=True,
            )
            if intent is None:
                intent = ConversationIntent(type="new_query", reason="intent_none_default")
            intent_system_prompt = build_intent_system_prompt(
                intent,
                correction_text=content if intent.type == "correction" else None,
                clarification_text=content if intent.type == "clarification" else None,
            )
            context_messages: list[ConversationMessage] = []
            context_signature = ""
            prompt_build_ms = 0.0
            context_materialized = False
            socratic_context = self.context_builder.build_socratic_context(history_messages)

            async def load_context_for_stream() -> tuple[list[ConversationMessage], str, float]:
                return await self.context_builder.build_context(
                    history_messages,
                    request_id=request_id,
                    conversation_id=req.conversation_id,
                    context_max_tokens=max(int(getattr(config_settings, "conversation_context_max_tokens", 1200)), 1),
                    summary_max_tokens=max(int(getattr(config_settings, "conversation_context_summary_tokens", 240)), 0),
                    max_turns=4,
                )

            context_messages_task = asyncio.create_task(load_context_for_stream())

            async def ensure_context_materialized(*, timeout_seconds: float, source: str) -> None:
                nonlocal context_messages, context_signature, prompt_build_ms, context_materialized
                if context_materialized:
                    return
                try:
                    loaded_messages, loaded_signature, loaded_prompt_build_ms = await asyncio.wait_for(
                        asyncio.shield(context_messages_task),
                        timeout=timeout_seconds,
                    )
                    context_messages = loaded_messages
                    context_signature = loaded_signature
                    prompt_build_ms = loaded_prompt_build_ms
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning(
                        "context_load_timeout",
                        request_id=request_id,
                        timeout_seconds=timeout_seconds,
                        source=source,
                    )
                    context_messages = []
                    context_signature = ""
                except Exception as exc:
                    logger.warning(
                        "context_load_error",
                        request_id=request_id,
                        source=source,
                        error=str(exc),
                    )
                    context_messages = []
                    context_signature = ""
                finally:
                    context_materialized = True

            last_three = history_messages[-3:]
            logger.info(
                "messages_context_task_started",
                request_id=request_id,
                conversation_id=req.conversation_id,
                history_length=len(history_messages),
                last_3_message_roles=[msg["role"] for msg in last_three],
                last_3_message_lengths=[len(msg["content"]) for msg in last_three],
            )

            effective_content = content
            ack_message = ack_response(selected_mode) if intent.type == "acknowledgment" else None
            intent_payload = content if intent.type in {"correction", "clarification"} else ""

            if llm_mode == TECHNICAL_MODE:
                max_output_tokens = TECHNICAL_MAX_TOKENS
            elif llm_mode == SOCRATIC_MODE:
                max_output_tokens = int(getattr(config_settings, "max_output_tokens_socratic", 1024))
            else:
                max_output_tokens = int(getattr(config_settings, "max_output_tokens_learning", 1024))

            prompt_tokens = count_prompt_tokens(effective_content)
            reserved_tokens = max(prompt_tokens + max_output_tokens, 1)
            client_ip = resolve_client_ip(request, trusted_proxies=trusted_proxies)
            identifier = f"key:{user_id}"
            daily_limit, _hourly_limit, rpm, burst_limit, sustained_window, burst_window = _resolve_limits(
                settings=config_settings,
                api_key=api_key,
            )
            if burst_limit <= 0 and rpm <= 0:
                bucket_capacity = 0
                refill_per_sec = 0.0
            else:
                bucket_capacity = burst_limit if burst_limit > 0 else max(rpm, 1)
                refill_per_sec = (
                    float(rpm) / float(sustained_window)
                    if rpm > 0 and sustained_window > 0
                    else float(bucket_capacity) / float(max(burst_window, 1))
                )
            gatekeeper = await gatekeep_message_request(
                identifier=identifier,
                reserved_tokens=reserved_tokens,
                token_bucket_capacity=bucket_capacity,
                token_bucket_refill_per_sec=refill_per_sec,
                token_bucket_cost=1,
                daily_quota_limit=daily_limit,
                daily_quota_window=max(int(getattr(config_settings, "quota_window_seconds", 86400)), 1),
                circuit_threshold=max(int(getattr(config_settings, "circuit_breaker_tokens_per_minute", 0)), 0),
                circuit_open_seconds=max(int(getattr(config_settings, "circuit_breaker_open_seconds", 60)), 1),
                idempotency_key=idempotency_key,
                timeout_seconds=0.8,
            )
            redis_degraded = gatekeeper.degraded
            redis_eval_ms = gatekeeper.redis_eval_ms
            if gatekeeper.idempotency_status == "COMPLETED" and gatekeeper.idempotency_response:
                await self.ingress_dedupe_clear(client_message_id)
                return build_replay_response(
                    content=str(gatekeeper.idempotency_response),
                    message_id=client_message_id,
                    assistant_message_id=None,
                    mode=selected_mode,
                    prompt_mode=prompt_mode,
                    message_dispatcher=self.message_dispatcher,
                )
            if not gatekeeper.allowed:
                await self.ingress_dedupe_clear(client_message_id)
                if gatekeeper.idempotency_status == "PENDING":
                    raise HTTPException(status_code=409, detail="Duplicate request already in progress.")
                if gatekeeper.idempotency_status == "CIRCUIT_OPEN":
                    raise HTTPException(
                        status_code=503,
                        detail={"type": "circuit_breaker_open", "action": "reject"},
                        headers={"Retry-After": str(max(gatekeeper.retry_after, 1))},
                    )
                raise HTTPException(
                    status_code=429,
                    detail={"type": "rate_limit_exceeded"},
                    headers={"Retry-After": str(max(gatekeeper.retry_after, 1))},
                )
            request_temperature = max(0.0, min(float(req.temperature), 1.0))
            system_prompt = SYSTEM_PROMPT.strip()
            mode_prompt = MODE_SYSTEM_PROMPTS.get(llm_mode, "").strip()
            intent_prompt = (intent_system_prompt or "").strip()
            system_prompt_bundle = "\n".join(
                [part for part in (system_prompt, mode_prompt, intent_prompt) if part]
            )
            await ensure_context_materialized(timeout_seconds=1.0, source="pre_cache")
            cache_key = message_cache_key(
                content=effective_content,
                mode=selected_mode,
                prompt_mode=prompt_mode,
                temperature=request_temperature,
                model_alias=str(config_state.get("model_alias") or selected_mode),
                system_prompt=system_prompt_bundle,
                context_signature=context_signature,
                intent_type=intent.type,
                intent_payload=intent_payload,
                conversation_id=req.conversation_id,
                user_id=api_key.id,
            )
            cached_response = None
            if not req.regenerate:
                cached_response = await cache_get_value(cache_key, timeout_seconds=0.8)
            logger.info(
                "messages_cache_lookup",
                request_id=request_id,
                user_id_hash=user_id_hash,
                cache_hit=bool(cached_response),
                cache_key_prefix=cache_key[:16],
            )

            db_degraded = get_supabase_admin() is None
            force_non_stream = bool(db_degraded)

            assistant_message_id = str(uuid.uuid4())

            async def ensure_context_for_stream() -> None:
                await ensure_context_materialized(timeout_seconds=1.0, source="stream")

            persistence = StreamPersistence(
                supabase=get_supabase_admin(),
                request_id=request_id,
                user_id_hash=user_id_hash,
                conversation_id=req.conversation_id,
                client_message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                assistant_client_id=assistant_client_id,
                selected_mode=selected_mode,
                prompt_mode=prompt_mode,
                prompt_spec=(
                    {
                        "topic": req.prompt_spec.to_prompt_spec(content).topic,
                        "depth": req.prompt_spec.to_prompt_spec(content).depth,
                        "task": req.prompt_spec.to_prompt_spec(content).task,
                        "reasoning": req.prompt_spec.to_prompt_spec(content).reasoning,
                        "style": req.prompt_spec.to_prompt_spec(content).style,
                        "capabilities": sorted(req.prompt_spec.to_prompt_spec(content).capabilities),
                    }
                    if getattr(req, "prompt_spec", None)
                    else None
                ),
                content=content,
                regenerate=bool(req.regenerate),
            )

            event_loop = StreamEventLoop(
                request=request,
                request_received=request_received,
                request_id=request_id,
                req=req,
                user_id=user_id,
                is_pro=is_pro,
                user_id_hash=user_id_hash,
                content=content,
                content_hash=content_hash,
                client_message_id=client_message_id,
                assistant_client_id=assistant_client_id,
                idempotency_key=idempotency_key,
                idempotency_key_hash=idempotency_key_hash,
                selected_mode=selected_mode,
                llm_mode=llm_mode,
                prompt_mode=prompt_mode,
                request_temperature=request_temperature,
                history_limit=history_limit,
                ack_message=ack_message,
                intent_system_prompt=intent_system_prompt,
                socratic_context=socratic_context,
                context_messages=context_messages,
                ensure_context_for_stream=ensure_context_for_stream,
                cache_key=cache_key,
                cached_response=cached_response,
                gatekeeper=gatekeeper,
                redis_eval_ms=redis_eval_ms,
                redis_degraded=redis_degraded,
                force_non_stream=force_non_stream,
                assistant_message_id=assistant_message_id,
                persistence=persistence,
                ingress_dedupe_clear=self.ingress_dedupe_clear,
                release_lock=self.lock_manager.release,
                config=get_stream_config(),
            )

            response = self.message_dispatcher.dispatch_streaming_message(event_loop.run)
            preliminary_ms = (time.perf_counter() - request_received) * 1000
            logger.info(
                "timing_preliminary_work",
                request_id=request_id,
                conversation_id=req.conversation_id,
                total_ms=round(preliminary_ms, 2),
                breakdown={
                    "snapshot_ms": round(snapshot_ms, 2),
                    "db_ms": round(db_ms, 2),
                },
            )
            response_started = True
            return response
        finally:
            if not response_started:
                await self.ingress_dedupe_clear(client_message_id)
            if not response_started and not lock_released:
                self.lock_manager.release(req.conversation_id)
                lock_released = True
