"""Chat messages endpoint."""

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import orjson

from auth import check_is_pro, get_supabase_admin, verify_token
from config import get_settings
from logging_config import anonymize_text, anonymize_user_id, logger, log_sampled_success
from monitoring import capture_telemetry_event
from services.analytics import build_llm_request_payload, record_llm_request
import services.cache as cache_module
from services.cache import cache_get, cache_set, cache_set_if_absent
from services.conversation_cache import warm_conversation_snapshot
from services.inference import (
    TECHNICAL_MAX_TOKENS,
    SYSTEM_PROMPT,
    MODE_SYSTEM_PROMPTS,
    generate_explanation,
    generate_stream_explanation,
)
from services.conversation_context import (
    ConversationMessage,
    build_context_messages,
    build_socratic_context,
    extract_last_turns,
)
from services.conversation_intent import (
    classify_conversation_intent,
    build_intent_system_prompt,
)
from services.llm_client import get_provider_config_state
from services.llm_errors import LLMUnavailable
from services.message_gate import (
    append_conversation_message,
    cache_get_value,
    cache_set_value,
    fetch_conversation_snapshot,
    gatekeep_message_request,
)
from services.rate_limit import _resolve_limits, enforce_request_controls
from services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from services.token_count import count_prompt_tokens
from services.user_cache import refresh_is_pro_cache
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

_INGRESS_DEDUP: dict[str, float] = {}
_INGRESS_DEDUP_LOCK = asyncio.Lock()


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
    now = time.time()
    async with _INGRESS_DEDUP_LOCK:
        expired = [key for key, ts in _INGRESS_DEDUP.items() if (now - ts) > ttl_seconds]
        for key in expired:
            _INGRESS_DEDUP.pop(key, None)
        if message_id in _INGRESS_DEDUP:
            return False
        _INGRESS_DEDUP[message_id] = now
        return True


async def _ingress_dedupe_clear(message_id: str) -> None:
    async with _INGRESS_DEDUP_LOCK:
        _INGRESS_DEDUP.pop(message_id, None)


def _parse_snapshot_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        meta = orjson.loads(raw)
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _parse_snapshot_messages(raw_messages: list[str]) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for raw in raw_messages:
        try:
            payload = orjson.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            role = str(payload.get("role") or "")
            content = str(payload.get("content") or "")
            if role and content is not None:
                messages.append({"role": role, "content": content})
    return messages


async def _capture_telemetry_async(event: str, **payload: Any) -> None:
    await asyncio.to_thread(capture_telemetry_event, event, **payload)


class MessageRequest(BaseModel):
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
) -> str:
    digest = hashlib.sha256(
        f"{system_prompt}\x00{context_signature}\x00{content}\x00{temperature:.2f}\x00{model_alias}\x00{mode}\x00{prompt_mode}\x00{intent_type}\x00{intent_payload}".encode(
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
    is_pro = bool(auth_data.get("is_pro"))
    exp = auth_data.get("exp")
    if isinstance(exp, (int, float)):
        exp_delta = float(exp) - time.time()
        if exp_delta < 900:
            asyncio.create_task(refresh_is_pro_cache(user_id))
        if exp_delta < 120:
            is_pro = False

    content = (req.content or "").strip()
    user_id_hash = anonymize_user_id(user_id)
    content_hash = anonymize_text(content)

    asyncio.create_task(
        _capture_telemetry_async(
            "message_send",
            request_id=request_id,
            user_id_hash=user_id_hash,
            mode=req.mode,
            prompt_mode=req.prompt_mode,
            regenerate=bool(req.regenerate),
        )
    )

    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    client_message_id = _require_uuid(req.client_generated_id, "client_generated_id")
    assistant_client_id = _require_uuid(req.assistant_client_id, "assistant_client_id")
    idempotency_key = _idempotency_key(user_id, client_message_id)

    if not await _ingress_dedupe_check(client_message_id):
        try:
            redis = await cache_module.get_redis()
            status = await redis.hget(idempotency_key, "status")
        except Exception:
            status = None
        if status == "COMPLETED":
            await _ingress_dedupe_clear(client_message_id)
        else:
            raise HTTPException(status_code=409, detail="Duplicate request already in progress.")

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
    snapshot_meta_raw, snapshot_raw_messages = await fetch_conversation_snapshot(
        conversation_id=req.conversation_id,
        max_messages=history_limit,
        timeout_seconds=0.08,
    )
    snapshot_degraded = not snapshot_meta_raw and not snapshot_raw_messages
    snapshot_meta = _parse_snapshot_meta(snapshot_meta_raw)
    if snapshot_meta and snapshot_meta.get("user_id") and str(snapshot_meta.get("user_id")) != user_id:
        await _ingress_dedupe_clear(client_message_id)
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not snapshot_meta_raw:
        asyncio.create_task(warm_conversation_snapshot(req.conversation_id, user_id))

    selected_mode = normalize_mode(
        req.mode
        or snapshot_meta.get("mode")
        or (snapshot_meta.get("settings") or {}).get("mode")
        or DEFAULT_CHAT_MODE
    )
    if selected_mode not in {LEARNING_MODE, TECHNICAL_MODE, SOCRATIC_MODE}:
        selected_mode = DEFAULT_CHAT_MODE
    if selected_mode == TECHNICAL_MODE:
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

    if selected_mode == TECHNICAL_MODE and not is_pro:
        await _ingress_dedupe_clear(client_message_id)
        raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")
    if selected_mode == SOCRATIC_MODE and not is_pro:
        await _ingress_dedupe_clear(client_message_id)
        raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")

    # ── Conversation context & intent ──────────────────────────────────────
    history_messages = _parse_snapshot_messages(snapshot_raw_messages)
    last_user_message, last_assistant_message = extract_last_turns(history_messages)
    has_prior = bool(last_user_message or last_assistant_message)
    intent = classify_conversation_intent(content, has_prior=has_prior)
    intent_system_prompt = build_intent_system_prompt(
        intent,
        correction_text=content if intent.type == "correction" else None,
        clarification_text=content if intent.type == "clarification" else None,
    )
    prompt_build_start = time.perf_counter()
    context_messages, context_signature = build_context_messages(
        history_messages,
        max_tokens=max(int(getattr(config_settings, "conversation_context_max_tokens", 1200)), 1),
        summary_max_tokens=max(int(getattr(config_settings, "conversation_context_summary_tokens", 240)), 0),
    )
    socratic_context = build_socratic_context(history_messages)
    prompt_build_ms = (time.perf_counter() - prompt_build_start) * 1000

    effective_content = content
    ack_response = _ack_response(selected_mode) if intent.type == "acknowledgment" else None
    intent_payload = content if intent.type in {"correction", "clarification"} else ""

    if selected_mode == TECHNICAL_MODE:
        max_output_tokens = TECHNICAL_MAX_TOKENS
    elif selected_mode == SOCRATIC_MODE:
        max_output_tokens = int(getattr(config_settings, "max_output_tokens_socratic", 1024))
    else:
        max_output_tokens = int(getattr(config_settings, "max_output_tokens_learning", 1024))

    prompt_tokens = count_prompt_tokens(effective_content)
    reserved_tokens = max(prompt_tokens + max_output_tokens, 1)
    client_ip = _resolve_client_ip(request, trusted_proxies=trusted_proxies)
    identifier = f"user:{user_id}" if user_id else f"ip:{client_ip}"
    daily_limit, _hourly_limit, rpm, burst_limit, sustained_window, burst_window = _resolve_limits(
        settings=config_settings,
        is_authenticated=True,
        is_pro=is_pro,
        mode=selected_mode,
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
        timeout_seconds=0.05,
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
    mode_prompt = MODE_SYSTEM_PROMPTS.get(selected_mode, "").strip()
    intent_prompt = (intent_system_prompt or "").strip()
    system_prompt_bundle = "\n".join(
        [part for part in (system_prompt, mode_prompt, intent_prompt) if part]
    )
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
    )
    cached_response = None
    if not req.regenerate:
        cached_response = await cache_get_value(cache_key, timeout_seconds=0.05)

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
        if sequence_id is not None:
            payload["sequence_id"] = sequence_id
        try:
            await asyncio.to_thread(lambda: supabase.table("messages").insert(payload).execute())
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
        if sequence_id is not None:
            payload["sequence_id"] = sequence_id
        try:
            await asyncio.to_thread(lambda: supabase.table("messages").insert(payload).execute())
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
            await asyncio.to_thread(
                lambda: supabase.table("conversations")
                .update(update_payload)
                .eq("id", req.conversation_id)
                .execute()
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
        user_sequence_id: int | None = None
        assistant_sequence_id: int | None = None
        redis_append_failed = False

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
                except Exception:
                    pass

        async def cancel_pending_chunk_task() -> None:
            nonlocal pending_chunk_task
            if pending_chunk_task is None:
                return
            pending_chunk_task.cancel()
            try:
                await asyncio.wait_for(pending_chunk_task, timeout=close_timeout_seconds)
            except BaseException:
                pass
            pending_chunk_task = None

        async def finalize_assistant_message(content_value: str, *, cacheable: bool = True) -> None:
            nonlocal assistant_sequence_id, redis_append_failed
            if not content_value.strip():
                return
            assistant_payload = {
                "role": "assistant",
                "content": content_value,
                "sequence_id": "__SEQ__",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "assistant_client_id": assistant_client_id,
            }
            assistant_sequence_id = await append_conversation_message(
                conversation_id=req.conversation_id,
                message_json=orjson.dumps(assistant_payload).decode("utf-8"),
                max_messages=history_limit,
                timeout_seconds=0.05,
            )
            if assistant_sequence_id is None:
                redis_append_failed = True
            asyncio.create_task(_persist_assistant_message(assistant_sequence_id, content_value))
            if cacheable:
                await cache_set_value(cache_key, content_value, cache_ttl_seconds, timeout_seconds=0.05)
            if not gatekeeper.degraded:
                try:
                    redis = await cache_module.get_redis()
                    response_hash = hashlib.sha256(content_value.encode("utf-8")).hexdigest()
                    await redis.hset(idempotency_key, "status", "COMPLETED")
                    await redis.hset(idempotency_key, "response", content_value)
                    await redis.hset(idempotency_key, "response_hash", response_hash)
                    await redis.hset(idempotency_key, "assistant_message_id", assistant_message_id)
                    await redis.hset(idempotency_key, "completed_at", int(time.time()))
                    await redis.expire(idempotency_key, idempotency_ttl_seconds)
                except Exception as exc:
                    logger.warning(
                        "messages_idempotency_update_failed",
                        request_id=request_id,
                        error=str(exc),
                    )

        stream = None
        try:
            assert (time.perf_counter() - request_received) < 0.2
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
                timeout_seconds=0.05,
            )
            if user_sequence_id is None:
                redis_append_failed = True
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
                    timeout_seconds=0.05,
                )
                if assistant_sequence_id is None:
                    redis_append_failed = True
                asyncio.create_task(_persist_assistant_message(assistant_sequence_id, full_content))
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                yield emit("done", "[DONE]")
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
                    timeout_seconds=0.05,
                )
                if assistant_sequence_id is None:
                    redis_append_failed = True
                asyncio.create_task(_persist_assistant_message(assistant_sequence_id, full_content))
                for index in range(0, len(cached_response), chunk_size):
                    chunk = cached_response[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                yield emit("done", "[DONE]")
                return

            generation_start = time.perf_counter()
            stream = generate_stream_explanation(
                effective_content,
                prompt_mode,
                mode=selected_mode,
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
                            effective_content,
                            prompt_mode,
                            mode=selected_mode,
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
                    await finalize_assistant_message(full_content, cacheable=not req.regenerate)
                    yield emit("done", "[DONE]")
                    return

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    chunk = full_content[index : index + chunk_size]
                    record_chunk()
                    yield emit("delta", {"delta": chunk, "assistant_message_id": assistant_message_id})
                yield emit("done", "[DONE]")
                await finalize_assistant_message(full_content, cacheable=not req.regenerate)
                return

            response_truncated = bool(timed_out and not aborted)
            if response_truncated:
                cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                full_content += cutoff_message
                yield emit("delta", {"delta": cutoff_message, "assistant_message_id": assistant_message_id})

            if full_content.strip():
                await finalize_assistant_message(full_content, cacheable=not req.regenerate)

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
                            mode=selected_mode,
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
                    await finalize_assistant_message(full_content, cacheable=not req.regenerate)
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
                    await finalize_assistant_message(full_content, cacheable=not req.regenerate)
                    yield emit("done", "[DONE]")
                    return
            if aborted:
                return
            if full_content.strip():
                await finalize_assistant_message(full_content, cacheable=not req.regenerate and not response_truncated)
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
                    redis = await cache_module.get_redis()
                    if full_content.strip():
                        response_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
                        await redis.hset(idempotency_key, "status", "COMPLETED")
                        await redis.hset(idempotency_key, "response", full_content)
                        await redis.hset(idempotency_key, "response_hash", response_hash)
                        await redis.hset(idempotency_key, "assistant_message_id", assistant_message_id)
                        await redis.hset(idempotency_key, "completed_at", int(time.time()))
                    else:
                        await redis.hset(idempotency_key, "status", "EXPIRED")
                        await redis.hset(idempotency_key, "expired_at", int(time.time()))
                    await redis.expire(idempotency_key, idempotency_ttl_seconds)
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
            payload = build_llm_request_payload(
                request_id=request_id,
                user_id=user_id,
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )
