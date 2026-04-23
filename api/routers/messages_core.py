"""Chat messages endpoint."""

import asyncio
import hashlib
import time
import uuid
from asyncio import Semaphore
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
import orjson

from api.auth import get_supabase_admin
from api.services.api_key_auth import ApiKeyRecord, verify_api_key
from api.config import CONTEXT_LOAD_TIMEOUTS, get_settings
from api.logging_config import anonymize_text, anonymize_user_id, logger, log_sampled_success
from monitoring import capture_telemetry_event
from api.services.analytics import build_llm_request_payload, record_llm_request
import api.services.cache as cache_module
from api.services.cache import cache_get, cache_set, cache_set_if_absent
from api.services.conversation_cache import warm_conversation_snapshot
from api.services.inference import (
    TECHNICAL_MAX_TOKENS,
    SYSTEM_PROMPT,
    MODE_SYSTEM_PROMPTS,
    generate_explanation,
    generate_stream_explanation,
)
from api.services.conversation_context import (
    ConversationMessage,
)
from api.services.conversation_intent import (
    ConversationIntent,
    classify_conversation_intent,
    build_intent_system_prompt,
)
from api.services.context_builder import ContextBuilder
from api.services.llm_client import get_provider_config_state
from api.services.llm_errors import LLMUnavailable
from api.services.message_gate import (
    append_conversation_message,
    cache_get_value,
    cache_set_value,
    fetch_conversation_snapshot,
    gatekeep_message_request,
)
from api.services.message_dispatcher import MessageDispatcher
from api.services.message_utils import normalize_mode, safe_number
from api.services.message_workflow import MessageWorkflow
from api.services.request_validator import RequestValidator
from api.services.redis_safe import safe_redis_call
from api.services.rate_limit import _resolve_limits, enforce_request_controls
from api.services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from api.services.token_count import count_prompt_tokens
from api.utils import (
    PROMPT_MODE_ALIASES,
    SUPPORTED_PROMPT_MODES,
    LEARNING_MODE,
    SOCRATIC_MODE,
    TECHNICAL_MODE,
    normalize_prompt_level,
    with_timeout,
)

router = APIRouter(tags=["messages"])
_request_validator = RequestValidator()
_context_builder = ContextBuilder()
_message_dispatcher = MessageDispatcher()
_message_workflow = MessageWorkflow()

_CONVERSATION_LOCKS: dict[str, tuple[Semaphore, float]] = {}
_CONVERSATION_LOCKS_LOCK = asyncio.Lock()
_CONVERSATION_LOCK_TTL_SECONDS = 600.0
_CONVERSATION_LOCK_MAX = 10000


def _prune_conversation_locks(now: float) -> None:
    if len(_CONVERSATION_LOCKS) <= _CONVERSATION_LOCK_MAX:
        cutoff = now - _CONVERSATION_LOCK_TTL_SECONDS
    else:
        cutoff = now - min(_CONVERSATION_LOCK_TTL_SECONDS, 120.0)

    stale_keys: list[str] = []
    for key, (sem, last_used) in _CONVERSATION_LOCKS.items():
        if last_used >= cutoff:
            continue
        sem_value = getattr(sem, "_value", None)
        if sem_value == 1:
            stale_keys.append(key)

    for key in stale_keys:
        _CONVERSATION_LOCKS.pop(key, None)


async def _acquire_conversation_lock(conversation_id: str, timeout_seconds: float = 1.0) -> bool:
    async with _CONVERSATION_LOCKS_LOCK:
        now = time.time()
        _prune_conversation_locks(now)
        entry = _CONVERSATION_LOCKS.get(conversation_id)
        if entry is None:
            sem = Semaphore(1)
            _CONVERSATION_LOCKS[conversation_id] = (sem, now)
        else:
            sem, _last_used = entry
            _CONVERSATION_LOCKS[conversation_id] = (sem, now)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=timeout_seconds)
        async with _CONVERSATION_LOCKS_LOCK:
            _CONVERSATION_LOCKS[conversation_id] = (sem, time.time())
        return True
    except asyncio.TimeoutError:
        return False


def _release_conversation_lock(conversation_id: str) -> None:
    entry = _CONVERSATION_LOCKS.get(conversation_id)
    if not entry:
        return
    sem, _last_used = entry
    sem.release()
    now = time.time()
    sem_value = getattr(sem, "_value", None)
    if sem_value == 1 and (now - _last_used) >= _CONVERSATION_LOCK_TTL_SECONDS:
        _CONVERSATION_LOCKS.pop(conversation_id, None)
        return
    _CONVERSATION_LOCKS[conversation_id] = (sem, now)


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


async def _ingress_dedupe_check(message_id: str, ttl_seconds: float = 3.0) -> bool:
    return await _request_validator.check_deduplication(message_id, ttl_seconds=ttl_seconds)


async def _ingress_dedupe_clear(message_id: str) -> None:
    await _request_validator.clear_deduplication(message_id)


async def _capture_telemetry_async(event: str, **payload: Any) -> None:
    await asyncio.to_thread(capture_telemetry_event, event, **payload)


class MessageRequest(BaseModel):
    """Validated payload for `/messages` requests."""

    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=8000)
    client_generated_id: Optional[str] = None
    assistant_client_id: Optional[str] = None
    mode: Optional[str] = None
    prompt_mode: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    regenerate: bool = False


def _message_cache_key(
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
    return f"knowbear:cache:{digest}"


def _ack_response(mode: str) -> str:
    if mode == TECHNICAL_MODE:
        return "Understood. Share the next technical detail or question when ready."
    if mode == SOCRATIC_MODE:
        return "Got it. Whenever you're ready, share your next thought."
    return "Got it. Let me know what you'd like to explore next."


def _idempotency_key(user_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"knowbear:idempotency:{digest}"


def _require_uuid(value: Optional[str], field_name: str) -> str:
    try:
        return _request_validator.require_uuid(value, field_name)
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"type": "bad_request", "message": detail, "retry_allowed": False},
    )


def _auth_required(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"type": "auth_required", "message": detail, "retry_allowed": False},
    )


def _validate_message_boundary(payload: Any) -> tuple[str, str | None]:
    result = _request_validator.validate_message_request(payload)
    if not result.ok:
        raise _bad_request(str(result.error_message or "Invalid request payload"))
    return result.content, result.normalized_mode


def _build_replay_response(
    *,
    content: str,
    message_id: str,
    assistant_message_id: Optional[str],
    mode: str,
    prompt_mode: str,
) -> StreamingResponse:
    return _message_dispatcher.dispatch_normal_message(
        content=content,
        message_id=message_id,
        assistant_message_id=assistant_message_id,
        mode=mode,
        prompt_mode=prompt_mode,
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


async def _send_message_handler(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    """Handle authenticated chat requests and stream SSE responses."""
    request_received = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
    snapshot_ms = 0.0
    db_ms = 0.0
    snapshot_degraded = False

    try:
        raw_payload = await request.json()
    except Exception as exc:
        logger.warning("messages_invalid_json_payload", request_id=request_id, error=str(exc))
        raise _bad_request("Invalid JSON payload")

    content, normalized_mode = _validate_message_boundary(raw_payload)
    try:
        req = MessageRequest.model_validate(raw_payload)
    except ValidationError as exc:
        logger.warning(
            "messages_request_validation_failed",
            request_id=request_id,
            error=str(exc),
        )
        raise _bad_request("Invalid request payload")

    user_id = api_key.id
    is_pro = api_key.is_pro

    config_state = get_provider_config_state()
    if not bool(config_state.get("chat_enabled", False)):
        raise LLMUnavailable(
            "Model service is temporarily unavailable. Please try again shortly."
        )

    content = content.strip()
    user_id_hash = anonymize_user_id(user_id)
    content_hash = anonymize_text(content)


    if not content:
        raise _bad_request("Content is required")

    logger.info(
        "messages_request_start",
        request_id=request_id,
        user_id_hash=user_id_hash,
        conversation_id=req.conversation_id,
        mode=normalized_mode or "default",
        content_length=len(content),
    )

    client_message_id = _require_uuid(req.client_generated_id, "client_generated_id")
    assistant_client_id = _require_uuid(req.assistant_client_id, "assistant_client_id")
    idempotency_key = _idempotency_key(user_id, client_message_id)
    idempotency_key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]

    if not await _ingress_dedupe_check(client_message_id):
        try:
            redis = await safe_redis_call(cache_module.get_redis, operation="connect")
            status = await safe_redis_call(redis.hget, idempotency_key, "status", operation="hget") if redis else None
        except Exception as exc:
            logger.warning(
                "messages_idempotency_status_read_failed",
                request_id=request_id,
                idempotency_key_hash=idempotency_key_hash,
                error=str(exc),
            )
            status = None
        if status == "COMPLETED":
            await _ingress_dedupe_clear(client_message_id)
        else:
            raise HTTPException(status_code=409, detail="Duplicate request already in progress.")

    lock_acquired = await _acquire_conversation_lock(req.conversation_id, timeout_seconds=1.0)
    if not lock_acquired:
        raise HTTPException(
            status_code=429,
            detail="Another request for this conversation is already processing. Please retry.",
            headers={"Retry-After": "2"},
        )
    lock_released = False
    response_started = False

    try:
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
        trusted_proxies = _trusted_proxies_from_settings(config_settings)

        history_limit = max(int(getattr(config_settings, "conversation_context_fetch_limit", 80)), 1)
        snapshot_result = await _context_builder.load_snapshot(
            conversation_id=req.conversation_id,
            user_id=user_id,
            history_limit=history_limit,
            request_id=request_id,
            fetch_snapshot=fetch_conversation_snapshot,
            warm_snapshot=warm_conversation_snapshot,
        )
        snapshot_meta_raw = snapshot_result.meta_raw
        snapshot_raw_messages = snapshot_result.raw_messages
        snapshot_meta = snapshot_result.meta
        if snapshot_meta and snapshot_meta.get("user_id") and str(snapshot_meta.get("user_id")) != user_id:
            await _ingress_dedupe_clear(client_message_id)
            raise HTTPException(status_code=404, detail="Conversation not found")
        snapshot_ms = snapshot_result.snapshot_ms
        snapshot_degraded = snapshot_result.snapshot_degraded

        mode_candidate = (
            normalized_mode
            or snapshot_meta.get("mode")
            or (snapshot_meta.get("settings") or {}).get("mode")
            or "chat"
        )
        try:
            selected_mode = normalize_mode(mode_candidate)
        except ValueError:
            selected_mode = normalize_mode(None)
    
        llm_mode = LEARNING_MODE if selected_mode in {"chat", "summary"} else selected_mode
    
        if llm_mode == TECHNICAL_MODE:
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
            cast(str, snapshot_meta.get("prompt_mode") or ""),
            cast(str, snapshot_meta.get("prompt_mode") or ""),
        )
        prompt_mode = normalize_prompt_level(requested_prompt_mode or stored_prompt_mode)
        if prompt_mode not in SUPPORTED_PROMPT_MODES:
            prompt_mode = normalize_prompt_level(None)
    
        asyncio.create_task(
            _capture_telemetry_async(
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
    
        if llm_mode == TECHNICAL_MODE and not is_pro:
            await _ingress_dedupe_clear(client_message_id)
            if not lock_released:
                _release_conversation_lock(req.conversation_id)
                lock_released = True
            raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")
        if llm_mode == SOCRATIC_MODE and not is_pro:
            await _ingress_dedupe_clear(client_message_id)
            if not lock_released:
                _release_conversation_lock(req.conversation_id)
                lock_released = True
            raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")
    
        # ── Conversation context & intent ──────────────────────────────────────
        history_messages = await _context_builder.parse_snapshot_messages(snapshot_raw_messages, req.conversation_id)
        if not history_messages:
            db_start = time.perf_counter()
            db_result = await with_timeout(
                _context_builder.load_conversation_from_db(
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
            # Note: with_timeout already logs timeout scenarios internally.
            # Empty db_messages for new conversations is expected behavior.
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
                await _ingress_dedupe_clear(client_message_id)
                raise HTTPException(status_code=404, detail="Conversation not found")
        last_user_message, last_assistant_message = _context_builder.extract_turns(history_messages)
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
        socratic_context = _context_builder.build_socratic_context(history_messages)

        async def load_context_for_stream() -> tuple[list[ConversationMessage], str, float]:
            return await _context_builder.build_context(
                history_messages,
                request_id=request_id,
                conversation_id=req.conversation_id,
                context_max_tokens=max(int(getattr(config_settings, "conversation_context_max_tokens", 1200)), 1),
                summary_max_tokens=max(int(getattr(config_settings, "conversation_context_summary_tokens", 240)), 0),
                max_turns=4,
            )

        context_messages_task = asyncio.create_task(load_context_for_stream())

        async def ensure_context_materialized(
            *, timeout_seconds: float, source: str
        ) -> None:
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
        ack_response = _ack_response(selected_mode) if intent.type == "acknowledgment" else None
        intent_payload = content if intent.type in {"correction", "clarification"} else ""
    
        if llm_mode == TECHNICAL_MODE:
            max_output_tokens = TECHNICAL_MAX_TOKENS
        elif llm_mode == SOCRATIC_MODE:
            max_output_tokens = int(getattr(config_settings, "max_output_tokens_socratic", 1024))
        else:
            max_output_tokens = int(getattr(config_settings, "max_output_tokens_learning", 1024))
    
        prompt_tokens = count_prompt_tokens(effective_content)
        reserved_tokens = max(prompt_tokens + max_output_tokens, 1)
        client_ip = _resolve_client_ip(request, trusted_proxies=trusted_proxies)
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
            await _ingress_dedupe_clear(client_message_id)
            return _build_replay_response(
                content=str(gatekeeper.idempotency_response),
                message_id=client_message_id,
                assistant_message_id=None,
                mode=selected_mode,
                prompt_mode=prompt_mode,
            )
        if not gatekeeper.allowed:
            await _ingress_dedupe_clear(client_message_id)
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
        cache_key = _message_cache_key(
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
        user_metadata = {
            "client_id": client_message_id,
            "mode": selected_mode,
            "prompt_mode": prompt_mode,
            "assistant_message_id": assistant_message_id,
        }
        assistant_metadata = {
            "assistant_client_id": assistant_client_id,
            "mode": selected_mode,
            "prompt_mode": prompt_mode,
        }
    
        async def _persist_user_message(sequence_id: int | None) -> None:
            supabase = get_supabase_admin()
            if not supabase:
                return
            payload = {
                "id": client_message_id,
                "conversation_id": req.conversation_id,
                "role": "user",
                "content": content,
                "metadata": user_metadata,
            }
            safe_sequence_id = safe_number(sequence_id, default=None)
            if safe_sequence_id is not None:
                payload["sequence_id"] = safe_sequence_id
            try:
                await supabase.table("messages").insert(payload).execute()
                logger.info(
                    "messages_user_inserted",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    sequence_id=safe_sequence_id,
                )
            except Exception as exc:
                logger.error(
                    "messages_user_insert_failed",
                    error=str(exc),
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    payload={
                        "role": "user",
                        "content_length": len(content),
                        "mode": selected_mode,
                        "sequence_id": safe_sequence_id,
                    },
                    retry=bool(req.regenerate),
                    sampled=False,
                )
    
        async def _persist_assistant_message(sequence_id: int | None, content_value: str) -> None:
            supabase = get_supabase_admin()
            if not supabase:
                return
            payload = {
                "id": assistant_message_id,
                "conversation_id": req.conversation_id,
                "role": "assistant",
                "content": content_value,
                "metadata": assistant_metadata,
            }
            safe_sequence_id = safe_number(sequence_id, default=None)
            if safe_sequence_id is not None:
                payload["sequence_id"] = safe_sequence_id
            try:
                await supabase.table("messages").insert(payload).execute()
                logger.info(
                    "messages_assistant_inserted",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    sequence_id=safe_sequence_id,
                )
            except Exception as exc:
                logger.error(
                    "messages_assistant_insert_failed",
                    error=str(exc),
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    payload={
                        "role": "assistant",
                        "content_length": len(content_value),
                        "mode": selected_mode,
                        "sequence_id": safe_sequence_id,
                    },
                    retry=bool(req.regenerate),
                    sampled=False,
                )
    
        async def _persist_conversation_update() -> None:
            supabase = get_supabase_admin()
            if not supabase:
                return
            now_iso = datetime.now(timezone.utc).isoformat()
            update_payload = {
                "mode": selected_mode,
                "settings": {"mode": selected_mode, "prompt_mode": prompt_mode},
                "updated_at": now_iso,
            }
            try:
                await (
                    supabase.table("conversations")
                    .update(update_payload)
                    .eq("id", req.conversation_id)
                    .execute()
                )
                logger.info(
                    "messages_conversation_updated",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    mode=selected_mode,
                )
            except Exception as exc:
                logger.warning(
                    "messages_conversation_update_failed",
                    error=str(exc),
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    conversation_id=req.conversation_id,
                    payload=update_payload,
                    retry=bool(req.regenerate),
                    sampled=False,
                )
    
        async def event_generator() -> AsyncGenerator[str, None]:
            nonlocal lock_released, force_non_stream
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
            pending_chunk_task: asyncio.Task[str] | None = None
            user_sequence_id: int | None = None
            assistant_sequence_id: int | None = None
            redis_append_failed = False

            async def ensure_context_for_stream() -> None:
                await ensure_context_materialized(timeout_seconds=1.0, source="stream")
    
            asyncio.create_task(
                _capture_telemetry_async(
                    "stream_start",
                    request_id=request_id,
                    user_id_hash=user_id_hash,
                    mode=selected_mode,
                    prompt_mode=prompt_mode,
                    regenerate=bool(req.regenerate),
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
                        # Some async iterators can block in `aclose()` and ignore cancellation.
                        # Run it in its own task and do not await after timeout so we never hang response shutdown.
                        close_task = asyncio.create_task(close_fn())
                        try:
                            await asyncio.wait_for(close_task, timeout=close_timeout_seconds)
                        except asyncio.TimeoutError:
                            close_task.cancel()
                            raise
                    except asyncio.TimeoutError:
                        logger.warning(
                            "messages_stream_close_timeout",
                            request_id=request_id,
                            user_id_hash=user_id_hash,
                            conversation_id=req.conversation_id,
                            mode=selected_mode,
                            sampled=False,
                        )
                    except Exception as exc:
                        logger.debug(
                            "messages_stream_close_failed",
                            request_id=request_id,
                            conversation_id=req.conversation_id,
                            error=str(exc),
                        )
    
            async def cancel_pending_chunk_task() -> None:
                nonlocal pending_chunk_task
                if pending_chunk_task is None:
                    return
                pending_chunk_task.cancel()
                try:
                    await asyncio.wait_for(pending_chunk_task, timeout=close_timeout_seconds)
                except asyncio.CancelledError:
                    # Expected while force-canceling pending stream chunks.
                    pass
                except Exception as exc:
                    logger.debug(
                        "messages_pending_chunk_cancel_failed",
                        request_id=request_id,
                        conversation_id=req.conversation_id,
                        error=str(exc),
                    )
                pending_chunk_task = None
    
            async def finalize_assistant_message(
                content_value: str,
                *,
                cacheable: bool = True,
                stream_completed: bool = False,
            ) -> None:
                nonlocal assistant_sequence_id, redis_append_failed
                if not content_value.strip():
                    logger.warning(
                        "messages_finalize_empty_content",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        stream_completed=stream_completed,
                    )
                    return
                completion_marker = "complete" if stream_completed else "aborted"
                assistant_payload = {
                    "role": "assistant",
                    "content": content_value,
                    "sequence_id": "__SEQ__",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "assistant_client_id": assistant_client_id,
                    "stream_status": completion_marker,
                }
                assistant_sequence_id = await append_conversation_message(
                    conversation_id=req.conversation_id,
                    message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                    max_messages=history_limit,
                    timeout_seconds=0.8,
                )
                if assistant_sequence_id is None:
                    redis_append_failed = True
                asyncio.create_task(_persist_assistant_message(assistant_sequence_id, content_value))
                if cacheable and stream_completed:
                    await cache_set_value(cache_key, content_value, cache_ttl_seconds, timeout_seconds=0.8)
                elif cacheable and not stream_completed:
                    logger.warning(
                        "messages_partial_stream_skip_cache",
                        request_id=request_id,
                        content_length=len(content_value),
                        stream_completed=stream_completed,
                    )
                logger.info(
                    "messages_response_completed",
                    request_id=request_id,
                    response_length=len(content_value),
                    stream_completed=stream_completed,
                    cached=bool(cacheable and stream_completed),
                    idempotency_key_hash=idempotency_key_hash,
                )
                if not gatekeeper.degraded:
                    try:
                        redis = await safe_redis_call(cache_module.get_redis, operation="connect")
                        if redis is None:
                            return
                        response_hash = hashlib.sha256(content_value.encode("utf-8")).hexdigest()
                        await safe_redis_call(redis.hset, idempotency_key, "status", "COMPLETED", operation="hset")
                        await safe_redis_call(redis.hset, idempotency_key, "response", content_value, operation="hset")
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
                        await safe_redis_call(redis.expire, idempotency_key, idempotency_ttl_seconds, operation="expire")
                    except Exception as exc:
                        logger.warning(
                            "messages_idempotency_update_failed",
                            request_id=request_id,
                            error=str(exc),
                        )
    
            stream = None
            try:
                pre_stream_latency = time.perf_counter() - request_received
                if pre_stream_latency >= 0.2:
                    logger.warning(
                        "messages_pre_stream_latency_high",
                        request_id=request_id,
                        conversation_id=req.conversation_id,
                        pre_stream_latency_ms=round(pre_stream_latency * 1000, 2),
                    )
                yield emit("start", {"type": "start"})
                meta_payload = {
                    "assistant_message_id": assistant_message_id,
                    "mode": selected_mode,
                    "prompt_mode": prompt_mode,
                    "message_id": client_message_id,
                }
                if cached_response:
                    meta_payload["replay"] = "true"
                yield emit("meta", meta_payload)
    
                user_payload = {
                    "role": "user",
                    "content": content,
                    "sequence_id": "__SEQ__",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "client_id": client_message_id,
                }
                user_sequence_id = await append_conversation_message(
                    conversation_id=req.conversation_id,
                    message_json=orjson.dumps(user_payload).decode("utf-8"),
                    max_messages=history_limit,
                    timeout_seconds=0.8,
                )
                if user_sequence_id is None:
                    redis_append_failed = True
                    force_non_stream = True
                asyncio.create_task(_persist_user_message(user_sequence_id))
                asyncio.create_task(_persist_conversation_update())
    
                if ack_response:
                    full_content = ack_response
                    assistant_payload = {
                        "role": "assistant",
                        "content": full_content,
                        "sequence_id": "__SEQ__",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "assistant_client_id": assistant_client_id,
                    }
                    assistant_sequence_id = await append_conversation_message(
                        conversation_id=req.conversation_id,
                        message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                        max_messages=history_limit,
                        timeout_seconds=0.8,
                    )
                    if assistant_sequence_id is None:
                        redis_append_failed = True
                    asyncio.create_task(_persist_assistant_message(assistant_sequence_id, full_content))
                    for index in range(0, len(full_content), chunk_size):
                        chunk = full_content[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                    yield emit("done", "[DONE]")
                    logger.info(
                        "messages_response_completed",
                        request_id=request_id,
                        response_length=len(full_content),
                        stream_completed=True,
                        cached=False,
                        idempotency_key_hash=idempotency_key_hash,
                    )
                    return
    
                if cached_response:
                    telemetry_sink["token_usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
                    assistant_payload = {
                        "role": "assistant",
                        "content": full_content,
                        "sequence_id": "__SEQ__",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "assistant_client_id": assistant_client_id,
                    }
                    assistant_sequence_id = await append_conversation_message(
                        conversation_id=req.conversation_id,
                        message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                        max_messages=history_limit,
                        timeout_seconds=0.8,
                    )
                    if assistant_sequence_id is None:
                        redis_append_failed = True
                    asyncio.create_task(_persist_assistant_message(assistant_sequence_id, full_content))
                    for index in range(0, len(cached_response), chunk_size):
                        chunk = cached_response[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                    yield emit("done", "[DONE]")
                    logger.info(
                        "messages_response_completed",
                        request_id=request_id,
                        response_length=len(cached_response),
                        stream_completed=True,
                        cached=True,
                        idempotency_key_hash=idempotency_key_hash,
                    )
                    return
    
                if force_non_stream:
                    await ensure_context_for_stream()
                    try:
                        fallback_content = await generate_explanation(
                            effective_content,
                            prompt_mode,
                            mode=llm_mode,
                            temperature=request_temperature,
                            regenerate=req.regenerate,
                            request_id=request_id,
                            user_id=user_id,
                            is_pro=is_pro,
                            telemetry_sink=telemetry_sink,
                            conversation_messages=context_messages,
                            conversation_context=socratic_context,
                            intent_system_prompt=intent_system_prompt,
                        )
                    except Exception as exc:
                        logger.error(
                            "messages_non_stream_fallback_failed",
                            error=str(exc),
                            request_id=request_id,
                            user_id_hash=user_id_hash,
                            conversation_id=req.conversation_id,
                            content_hash=content_hash,
                            mode=selected_mode,
                            sampled=False,
                        )
                        fallback_content = _final_fallback_message(selected_mode)
    
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        chunk = full_content[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                    yield emit("done", "[DONE]")
                    await finalize_assistant_message(
                        full_content,
                        cacheable=not req.regenerate,
                        stream_completed=True,
                    )
                    return
    
                system_parts: list[str] = []
                await ensure_context_for_stream()
                base_prompt = SYSTEM_PROMPT.strip()
                if base_prompt:
                    system_parts.append(base_prompt)
                mode_prompt = MODE_SYSTEM_PROMPTS.get(selected_mode, "").strip()
                if mode_prompt:
                    system_parts.append(mode_prompt)
                if intent_system_prompt:
                    system_parts.append(intent_system_prompt.strip())
    
                prompt_messages: list[ConversationMessage] = []
                if system_parts:
                    prompt_messages.append({"role": "system", "content": "\n".join(system_parts)})
                prompt_messages.extend(context_messages)
                prompt_messages.append({"role": "user", "content": effective_content})
    
                prompt_hash_base = "\n".join(
                    f"{msg['role']}:{msg['content']}" for msg in prompt_messages
                )
                final_prompt_hash = hashlib.sha256(prompt_hash_base.encode("utf-8")).hexdigest()
    
                logger.info(
                    "messages_prompt_assembled",
                    request_id=request_id,
                    model_alias=str(config_state.get("model_alias")),
                    prompt_token_count=count_prompt_tokens(effective_content),
                    final_prompt_hash_prefix=final_prompt_hash[:16],
                    message_chain_length=len(prompt_messages),
                    system_prompt_present=any(msg["role"] == "system" for msg in prompt_messages),
                )
    
                generation_start = time.perf_counter()
                stream = generate_stream_explanation(
                    effective_content,
                    prompt_mode,
                    mode=llm_mode,
                    temperature=request_temperature,
                    regenerate=req.regenerate,
                    request_id=request_id,
                    user_id=user_id,
                    is_pro=is_pro,
                    telemetry_sink=telemetry_sink,
                    conversation_messages=context_messages,
                    conversation_context=socratic_context,
                    intent_system_prompt=intent_system_prompt,
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
                            async def get_next_chunk() -> str:
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
                        stream_completed = True
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
                                effective_content,
                                prompt_mode,
                                mode=llm_mode,
                                temperature=request_temperature,
                                regenerate=req.regenerate,
                                request_id=request_id,
                                user_id=user_id,
                                is_pro=is_pro,
                                telemetry_sink=telemetry_sink,
                                conversation_messages=context_messages,
                                conversation_context=socratic_context,
                                intent_system_prompt=intent_system_prompt,
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
                        await finalize_assistant_message(
                            full_content,
                            cacheable=not req.regenerate,
                            stream_completed=True,
                        )
                        yield emit("done", "[DONE]")
                        return
    
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        chunk = full_content[index : index + chunk_size]
                        record_chunk()
                        yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                    yield emit("done", "[DONE]")
                    await finalize_assistant_message(
                        full_content,
                        cacheable=not req.regenerate,
                        stream_completed=True,
                    )
                    return
    
                response_truncated = bool(timed_out and not aborted)
                if response_truncated:
                    cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                    full_content += cutoff_message
                    yield emit("delta", {"delta": cutoff_message, "assistant_message_id": assistant_message_id})
    
                if full_content.strip():
                    await finalize_assistant_message(
                        full_content,
                        cacheable=not req.regenerate,
                        stream_completed=stream_completed,
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
                                effective_content,
                                prompt_mode,
                                mode=llm_mode,
                                temperature=request_temperature,
                                regenerate=req.regenerate,
                                request_id=request_id,
                                user_id=user_id,
                                is_pro=is_pro,
                                telemetry_sink=telemetry_sink,
                                conversation_messages=context_messages,
                                conversation_context=socratic_context,
                                intent_system_prompt=intent_system_prompt,
                            ),
                            timeout=fallback_timeout_seconds,
                        )
                        full_content = str(fallback_content)
                        for index in range(0, len(full_content), chunk_size):
                            chunk = full_content[index : index + chunk_size]
                            record_chunk()
                            yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                        yield emit("done", "[DONE]")
                        await finalize_assistant_message(
                            full_content,
                            cacheable=not req.regenerate,
                            stream_completed=True,
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
                        await finalize_assistant_message(
                            full_content,
                            cacheable=not req.regenerate,
                            stream_completed=True,
                        )
                        yield emit("done", "[DONE]")
                        return
                if aborted:
                    return
                if full_content.strip():
                    await finalize_assistant_message(
                        full_content,
                        cacheable=not req.regenerate and not response_truncated,
                        stream_completed=False,
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
                yield emit("error", {"error": "Streaming failed"})
                yield emit("done", "[DONE]")
            finally:
                await cancel_pending_chunk_task()
                if stream is not None:
                    await close_stream(stream)
                await _ingress_dedupe_clear(client_message_id)
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
                    _capture_telemetry_async(
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
                    conversation_id=str(req.conversation_id or "") or None,
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
                    _release_conversation_lock(req.conversation_id)
                    lock_released = True
    
        response = _message_dispatcher.dispatch_streaming_message(event_generator)
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
            await _ingress_dedupe_clear(client_message_id)
        if not response_started and not lock_released:
            _release_conversation_lock(req.conversation_id)
            lock_released = True


@router.post("/messages")
async def send_message(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    return await _message_workflow.process_message(
        request=request,
        api_key=api_key,
        handler=_send_message_handler,
    )
