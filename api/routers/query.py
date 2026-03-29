"""Query endpoint for learning ensembles and direct-mode explanations."""

import asyncio
import hashlib
import time
import uuid
from typing import Any
from collections.abc import AsyncIterable, AsyncIterator, Iterable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import check_is_pro, ensure_user_exists, get_supabase_admin, verify_token_optional
from config import get_settings
from logging_config import anonymize_text, anonymize_user_id, logger, log_sampled_success
from services.cache import cache_get, cache_set, cache_set_if_absent
from services.inference import TECHNICAL_MAX_TOKENS, generate_explanation, generate_stream_explanation
from services.llm_client import get_provider_config_state
from services.llm_errors import LLMError, LLMUnavailable
from services.rate_limit import enforce_request_controls, refund_tokens
from services.streaming import SseEventBuilder, SSE_RESPONSE_HEADERS
from services.token_count import count_prompt_tokens
from utils import (
    DEFAULT_CHAT_MODE,
    FREE_LEVELS,
    LEARNING_MODE,
    SOCRATIC_MODE,
    TECHNICAL_MODE,
    PROMPT_MODE_ALIASES,
    SUPPORTED_CHAT_MODES,
    normalize_mode,
    sanitize_topic,
    topic_cache_key,
)

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    levels: list[str] = Field(default=FREE_LEVELS)
    premium: bool = False
    mode: str = DEFAULT_CHAT_MODE
    bypass_cache: bool = False
    temperature: float = 0.7
    regenerate: bool = False
    message_id: str | None = None


class QueryResponse(BaseModel):
    topic: str
    explanations: dict[str, str]
    cached: bool = False


def _normalize_levels(levels: list[str]) -> list[str]:
    normalized = []
    for level in levels or []:
        normalized.append(PROMPT_MODE_ALIASES.get(level, level))
    return normalized


def _cache_key(topic: str, level: str, mode: str) -> str:
    return topic_cache_key(topic, level, mode=normalize_mode(mode))


def _query_stream_idempotency_key(scope: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{scope}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"knowbear:query_stream:idempotency:{digest}"


async def _persist_history_safely(user, topic: str, levels: list[str], mode: str) -> None:
    """Persist history within a bounded timeout so request lifecycles remain responsive."""
    timeout_seconds = max(float(get_settings().stream_heartbeat_seconds or 1.0), 1.0)
    try:
        await asyncio.wait_for(
            save_to_history(user, topic, levels, mode),
            timeout=min(timeout_seconds, 3.0),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "save_to_history_timeout",
            user_id_hash=anonymize_user_id(str(getattr(user, "id", "") or "") or None),
            topic_hash=anonymize_text(topic),
            mode=normalize_mode(mode),
            sampled=False,
        )
    except Exception as exc:
        logger.error(
            "save_to_history_unhandled",
            error=str(exc),
            user_id_hash=anonymize_user_id(str(getattr(user, "id", "") or "") or None),
            topic_hash=anonymize_text(topic),
            mode=normalize_mode(mode),
            sampled=False,
        )


def _build_stream_replay_response(
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


def _build_stream_wait_response(
    *,
    topic: str,
    level: str,
    mode: str,
    message_id: str,
) -> StreamingResponse:
    async def wait_generator():
        builder = SseEventBuilder()
        yield builder.emit_json(
            "status",
            {
                "status": "waiting",
                "reason": "duplicate_in_progress",
                "topic": topic,
                "level": level,
                "mode": mode,
                "message_id": message_id,
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
            await asyncio.sleep(0)  # yield control to event loop


@router.post("/query", response_model=QueryResponse)
async def query_topic(
    req: QueryRequest,
    request: Request,
    auth_data: dict = Depends(verify_token_optional),
) -> QueryResponse:
    request_started = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
    topic_hash = anonymize_text(req.topic)
    config_state = get_provider_config_state()
    if not bool(config_state.get("chat_enabled", False)):
        raise LLMUnavailable(
            "Model service is temporarily unavailable. Please try again shortly."
        )

    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    mode = normalize_mode(req.mode)
    if mode not in SUPPORTED_CHAT_MODES:
        mode = DEFAULT_CHAT_MODE

    is_verified_pro = bool(auth_data and await check_is_pro(auth_data["user"].id))
    req.premium = is_verified_pro
    if mode == TECHNICAL_MODE:
        if not auth_data:
            raise HTTPException(status_code=401, detail="Authentication required for technical mode")
        if not is_verified_pro:
            raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")
    if mode == SOCRATIC_MODE:
        if not auth_data:
            raise HTTPException(status_code=401, detail="Authentication required for socratic mode")
        if not is_verified_pro:
            raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")
    if mode == SOCRATIC_MODE:
        if not auth_data:
            raise HTTPException(status_code=401, detail="Authentication required for socratic mode")
        if not is_verified_pro:
            raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")

    allowed_levels = FREE_LEVELS
    levels = [level for level in _normalize_levels(req.levels) if level in allowed_levels]
    if not levels:
        levels = ["eli15"]

    effective_user_id = auth_data["user"].id if auth_data else None
    user_id_raw = str(effective_user_id) if effective_user_id else None
    user_id_hash = anonymize_user_id(user_id_raw)

    if mode == TECHNICAL_MODE:
        max_output_tokens = TECHNICAL_MAX_TOKENS
    elif mode == SOCRATIC_MODE:
        max_output_tokens = int(getattr(get_settings(), "max_output_tokens_socratic", 1024))
    else:
        max_output_tokens = int(getattr(get_settings(), "max_output_tokens_learning", 1024))

    prompt_tokens = count_prompt_tokens(topic)
    level_count = max(len(levels), 1)
    reserved_tokens = max(prompt_tokens + (max_output_tokens * level_count), 1)
    quota_reservation = await enforce_request_controls(
        user_id=str(effective_user_id) if effective_user_id else None,
        client_ip=request.client.host if request.client else "unknown",
        reserved_tokens=reserved_tokens,
        mode=mode,
        is_pro=is_verified_pro,
    )

    explanations: dict[str, str] = {}
    missing_levels: list[str] = []

    if not req.bypass_cache:
        for level in levels:
            cached = await cache_get(_cache_key(topic, level, mode))
            if cached:
                explanations[level] = cached.get("text", "")
            else:
                missing_levels.append(level)
    else:
        missing_levels = levels

    if not missing_levels and not req.bypass_cache:
        if auth_data:
            await _persist_history_safely(auth_data["user"], topic, levels, mode)
        return QueryResponse(topic=topic, explanations=explanations, cached=True)

    level_telemetry = {level: {} for level in missing_levels}
    tasks = {
        level: generate_explanation(
            topic,
            level,
            mode=mode,
            temperature=req.temperature,
            regenerate=req.regenerate,
            request_id=request_id,
            user_id=user_id_raw,
            is_pro=is_verified_pro,
            telemetry_sink=level_telemetry[level],
        )
        for level in missing_levels
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for level, result in zip(tasks.keys(), results):
        if isinstance(result, str):
            explanations[level] = result
            await cache_set(_cache_key(topic, level, mode), {"text": result})
        else:
            if isinstance(result, LLMError):
                raise result
            explanations[level] = f"Error generating {level}: Please try again."
            logger.error(
                "query_generation_failed",
                request_id=request_id,
                user_id_hash=user_id_hash,
                level=level,
                topic_hash=topic_hash,
                error=str(result),
                mode=mode,
                retry=bool(req.regenerate),
                sampled=False,
            )

    if auth_data:
        await _persist_history_safely(auth_data["user"], topic, levels, mode)

    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    estimated_cost_usd = 0.0
    has_cost = False
    model_inference_values: list[float] = []
    model_alias = None
    for telemetry in level_telemetry.values():
        usage = telemetry.get("token_usage")
        if isinstance(usage, dict):
            token_usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            token_usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            token_usage["total_tokens"] += int(usage.get("total_tokens") or 0)
        cost_value = telemetry.get("estimated_cost_usd")
        if isinstance(cost_value, (int, float)):
            estimated_cost_usd += float(cost_value)
            has_cost = True
        model_ms = telemetry.get("model_inference_ms")
        if isinstance(model_ms, (int, float)):
            model_inference_values.append(float(model_ms))
        if not model_alias and isinstance(telemetry.get("model_alias"), str):
            model_alias = str(telemetry.get("model_alias"))

    queue_time_ms = round((time.perf_counter() - request_started) * 1000, 2)
    model_inference_ms = round(max(model_inference_values), 2) if model_inference_values else None
    log_sampled_success(
        "query_observed",
        request_id=request_id,
        user_id_hash=user_id_hash,
        model_alias=model_alias or mode,
        latency_ms=queue_time_ms,
        queue_time_ms=queue_time_ms,
        model_inference_ms=model_inference_ms,
        stream_duration_ms=None,
        token_usage=token_usage,
        estimated_cost_usd=round(estimated_cost_usd, 8) if has_cost else None,
        retry=bool(req.regenerate),
        sampled=True,
    )

    actual_tokens = int(token_usage.get("prompt_tokens") or 0) + int(token_usage.get("completion_tokens") or 0)
    if actual_tokens > 0:
        try:
            await refund_tokens(quota_reservation, actual_tokens)
        except Exception as exc:
            logger.warning(
                "query_quota_refund_failed",
                error=str(exc),
                request_id=request_id,
                user_id_hash=user_id_hash,
            )

    return QueryResponse(topic=topic, explanations=explanations, cached=False)


@router.post("/query/stream")
async def query_topic_stream(
    req: QueryRequest,
    request: Request,
    auth_data: dict = Depends(verify_token_optional),
):
    """Stream the final judged response in chunks."""
    request_received = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
    topic_hash = anonymize_text(req.topic)
    config_state = get_provider_config_state()
    if not bool(config_state.get("chat_enabled", False)):
        raise LLMUnavailable(
            "Model service is temporarily unavailable. Please try again shortly."
        )

    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    mode = normalize_mode(req.mode)
    if mode not in SUPPORTED_CHAT_MODES:
        mode = DEFAULT_CHAT_MODE

    is_verified_pro = bool(auth_data and await check_is_pro(auth_data["user"].id))
    req.premium = is_verified_pro
    if mode == TECHNICAL_MODE:
        if not auth_data:
            raise HTTPException(status_code=401, detail="Authentication required for technical mode")
        if not is_verified_pro:
            raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")

    allowed_levels = FREE_LEVELS
    normalized_levels = [level for level in _normalize_levels(req.levels) if level in allowed_levels]
    level = normalized_levels[0] if normalized_levels else "eli15"

    effective_user_id = auth_data["user"].id if auth_data else None
    user_id_raw = str(effective_user_id) if effective_user_id else None
    user_id_hash = anonymize_user_id(user_id_raw)

    if mode == TECHNICAL_MODE:
        max_output_tokens = TECHNICAL_MAX_TOKENS
    elif mode == SOCRATIC_MODE:
        max_output_tokens = int(getattr(get_settings(), "max_output_tokens_socratic", 1024))
    else:
        max_output_tokens = int(getattr(get_settings(), "max_output_tokens_learning", 1024))

    prompt_tokens = count_prompt_tokens(topic)
    reserved_tokens = max(prompt_tokens + max_output_tokens, 1)
    quota_reservation = await enforce_request_controls(
        user_id=str(effective_user_id) if effective_user_id else None,
        client_ip=request.client.host if request.client else "unknown",
        reserved_tokens=reserved_tokens,
        mode=mode,
        is_pro=is_verified_pro,
    )

    message_id = None
    if req.message_id:
        try:
            message_id = str(uuid.UUID(req.message_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="message_id must be a UUID") from exc

    settings = get_settings()
    environment = str(getattr(settings, "environment", "") or "").strip().lower()
    is_prod = environment == "production"
    stream_max_seconds = max(int(getattr(settings, "stream_max_seconds", 25)), 1)
    if not is_prod:
        stream_max_seconds = max(stream_max_seconds, 60)
    function_duration_cap: int | None = None
    if is_prod:
        function_duration_cap = max(
            5,
            int(getattr(settings, "vercel_function_max_duration_seconds", 25)) - 2,
        )
        stream_max_seconds = min(stream_max_seconds, function_duration_cap)
    fallback_budget_seconds = max(
        1.0,
        min(float(getattr(settings, "stream_fallback_budget_seconds", 6)), float(stream_max_seconds)),
    )
    if is_prod:
        fallback_budget_seconds = max(fallback_budget_seconds, 8.0)
    fallback_timeout_seconds = max(fallback_budget_seconds, 3.0)
    close_timeout_seconds = 0.25
    heartbeat_seconds = min(
        max(float(getattr(settings, "stream_heartbeat_seconds", 2)), 0.1),
        2,
    )
    raw_start_timeout = float(getattr(settings, "stream_start_timeout_seconds", 2))
    idempotency_ttl_seconds = min(
        max(int(getattr(settings, "stream_idempotency_ttl_seconds", 90)), 60),
        120,
    )
    idempotency_stale_seconds = max(
        5,
        min(int(getattr(settings, "stream_idempotency_stale_seconds", 20)), idempotency_ttl_seconds),
    )
    if mode == LEARNING_MODE and not is_prod:
        stream_start_timeout_seconds = max(raw_start_timeout, float(stream_max_seconds))
    elif mode == TECHNICAL_MODE:
        stream_max_seconds = max(stream_max_seconds, int(getattr(settings, "technical_stream_max_seconds", 45)))
        if function_duration_cap is not None:
            stream_max_seconds = min(stream_max_seconds, function_duration_cap)
        technical_start_timeout = float(
            getattr(settings, "technical_stream_start_timeout_seconds", max(raw_start_timeout, 6.0))
        )
        technical_cap = max(4.0, min(float(stream_max_seconds) * 0.75, 20.0))
        stream_start_timeout_seconds = min(max(technical_start_timeout, 2.0), technical_cap)
        fallback_budget_seconds = max(fallback_budget_seconds, 4.0)
        fallback_timeout_seconds = max(fallback_budget_seconds, 4.0)
    else:
        cap = 10.0 if is_prod else 15.0
        stream_start_timeout_seconds = min(max(raw_start_timeout, 0.1), cap)

    idempotency_key: str | None = None
    idempotency_claimed = False
    if message_id:
        scope = user_id_raw or (request.client.host if request.client else "anonymous")
        idempotency_key = _query_stream_idempotency_key(str(scope), message_id)
        idempotency_payload = await cache_get(idempotency_key)
        if idempotency_payload:
            status = idempotency_payload.get("status")
            if status == "completed" and idempotency_payload.get("response"):
                return _build_stream_replay_response(
                    topic=topic,
                    level=level,
                    mode=mode,
                    message_id=message_id or "",
                    content=str(idempotency_payload.get("response")),
                )
            if status == "in_progress":
                now_ts = int(time.time())
                started_at = idempotency_payload.get("started_at")
                started_ts = int(started_at) if isinstance(started_at, (int, float)) else now_ts
                age_seconds = max(now_ts - started_ts, 0)
                if age_seconds < idempotency_stale_seconds:
                    return _build_stream_wait_response(
                        topic=topic,
                        level=level,
                        mode=mode,
                        message_id=message_id or "",
                    )
                reclaimed = await cache_set(
                    idempotency_key,
                    {
                        "status": "reclaimed",
                        "reclaimed_at": now_ts,
                        "message_id": message_id,
                        "mode": mode,
                    },
                    ttl=idempotency_ttl_seconds,
                )
                if not reclaimed:
                    return _build_stream_wait_response(
                        topic=topic,
                        level=level,
                        mode=mode,
                        message_id=message_id or "",
                    )
                idempotency_claimed = True

    if idempotency_key:
        idempotency_record = {
            "status": "in_progress",
            "started_at": int(time.time()),
            "message_id": message_id,
            "mode": mode,
            "level": level,
        }
        if idempotency_claimed:
            reserved = await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)
        else:
            reserved = await cache_set_if_absent(idempotency_key, idempotency_record, idempotency_ttl_seconds)
        if not reserved:
            existing = await cache_get(idempotency_key)
            if existing:
                status = existing.get("status")
                if status == "completed" and existing.get("response"):
                    return _build_stream_replay_response(
                        topic=topic,
                        level=level,
                        mode=mode,
                        message_id=message_id or "",
                        content=str(existing.get("response")),
                    )
                if status == "in_progress":
                    now_ts = int(time.time())
                    started_at = existing.get("started_at")
                    started_ts = int(started_at) if isinstance(started_at, (int, float)) else now_ts
                    age_seconds = max(now_ts - started_ts, 0)
                    if age_seconds < idempotency_stale_seconds:
                        return _build_stream_wait_response(
                            topic=topic,
                            level=level,
                            mode=mode,
                            message_id=message_id or "",
                        )
                    current = await cache_get(idempotency_key)
                    if not current:
                        await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)
                    else:
                        current_status = current.get("status")
                        current_started_at = current.get("started_at")
                        current_started_ts = (
                            int(current_started_at)
                            if isinstance(current_started_at, (int, float))
                            else None
                        )
                        if current_status == "completed" and current.get("response"):
                            return _build_stream_replay_response(
                                topic=topic,
                                level=level,
                                mode=mode,
                                message_id=message_id or "",
                                content=str(current.get("response")),
                            )
                        if current_status == "in_progress" and current_started_ts == started_ts:
                            await cache_set(idempotency_key, idempotency_record, ttl=idempotency_ttl_seconds)
                        else:
                            return _build_stream_wait_response(
                                topic=topic,
                                level=level,
                                mode=mode,
                                message_id=message_id or "",
                            )

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
        telemetry_sink: dict[str, Any] = {}
        model_alias: str | None = None
        done_emitted = False
        pending_chunk_task: asyncio.Task[str] | None = None

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

        def emit_done_once() -> str | None:
            nonlocal done_emitted
            if done_emitted:
                return None
            done_emitted = True
            return emit("done", "[DONE]")

        async def close_stream(stream):
            close_fn = getattr(stream, "aclose", None)
            if close_fn:
                try:
                    await asyncio.wait_for(close_fn(), timeout=close_timeout_seconds)
                except asyncio.TimeoutError:
                    logger.warning(
                        "query_stream_close_timeout",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        topic_hash=topic_hash,
                        mode=mode,
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

        try:
            yield emit(
                "meta",
                {"topic": topic, "level": level, "mode": mode, "message_id": message_id},
            )

            if not req.bypass_cache:
                cached = await cache_get(_cache_key(topic, level, mode))
                if cached and cached.get("text"):
                    content = cached["text"]
                    for index in range(0, len(content), chunk_size):
                        yield emit("chunk", {"chunk": content[index : index + chunk_size]})
                    done_event = emit_done_once()
                    if done_event:
                        yield done_event
                    if auth_data:
                        await _persist_history_safely(auth_data["user"], topic, [level], mode)
                    return

            stream = generate_stream_explanation(
                topic,
                level,
                mode=mode,
                temperature=req.temperature,
                regenerate=req.regenerate,
                request_id=request_id,
                user_id=user_id_raw,
                is_pro=is_verified_pro,
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
                            return await stream_iter.__anext__()
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
                yield emit("chunk", {"chunk": chunk})

            no_chunks = chunk_count == 0 and not full_content.strip()
            if (start_timeout or timed_out or no_chunks) and not full_content.strip():
                fallback_used = True
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
                            is_pro=is_verified_pro,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        "streaming_fallback_failed",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        topic_hash=topic_hash,
                        error=str(exc),
                        mode=mode,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
                    yield emit("error", {"error": "Streaming timed out. Please retry."})
                    done_event = emit_done_once()
                    if done_event:
                        yield done_event
                    return

                full_content = str(fallback_content)
                for index in range(0, len(full_content), chunk_size):
                    yield emit("chunk", {"chunk": full_content[index : index + chunk_size]})
                done_event = emit_done_once()
                if done_event:
                    yield done_event
                if full_content.strip():
                    await cache_set(_cache_key(topic, level, mode), {"text": full_content})
                if auth_data:
                    await _persist_history_safely(auth_data["user"], topic, [level], mode)
                return

            if timed_out:
                cutoff_message = "\n\n[Response truncated to stay within serverless limits. Retry to continue.]"
                full_content += cutoff_message
                yield emit("chunk", {"chunk": cutoff_message})

            if full_content.strip():
                await cache_set(_cache_key(topic, level, mode), {"text": full_content})
            if auth_data:
                await _persist_history_safely(auth_data["user"], topic, [level], mode)

            done_event = emit_done_once()
            if done_event:
                yield done_event
        except Exception as exc:
            logger.error(
                "streaming_failed",
                request_id=request_id,
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                error=str(exc),
                mode=mode,
                retry=bool(req.regenerate),
                sampled=False,
            )
            if not full_content.strip():
                fallback_used = True
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
                            is_pro=is_verified_pro,
                            telemetry_sink=telemetry_sink,
                        ),
                        timeout=fallback_timeout_seconds,
                    )
                    full_content = str(fallback_content)
                    for index in range(0, len(full_content), chunk_size):
                        record_chunk()
                        yield emit("chunk", {"chunk": full_content[index : index + chunk_size]})
                    done_event = emit_done_once()
                    if done_event:
                        yield done_event
                    if full_content.strip():
                        await cache_set(_cache_key(topic, level, mode), {"text": full_content})
                    if auth_data:
                        await _persist_history_safely(auth_data["user"], topic, [level], mode)
                    return
                except Exception as fallback_exc:
                    logger.error(
                        "streaming_exception_fallback_failed",
                        request_id=request_id,
                        user_id_hash=user_id_hash,
                        topic_hash=topic_hash,
                        error=str(fallback_exc),
                        original_error=str(exc),
                        mode=mode,
                        retry=bool(req.regenerate),
                        sampled=False,
                    )
            if full_content.strip():
                mode_label = "technical " if mode == TECHNICAL_MODE else ""
                yield emit("chunk", {"chunk": f"\n\n[Connection interrupted. Partial {mode_label}response delivered.]"})
                done_event = emit_done_once()
                if done_event:
                    yield done_event
                if full_content.strip():
                    await cache_set(_cache_key(topic, level, mode), {"text": full_content})
                if auth_data:
                    await _persist_history_safely(auth_data["user"], topic, [level], mode)
                return
            yield emit("error", {"error": "An error occurred while streaming. Please try again."})
            done_event = emit_done_once()
            if done_event:
                yield done_event
        finally:
            await cancel_pending_chunk_task()
            if idempotency_key and message_id:
                if full_content.strip():
                    await cache_set(
                        idempotency_key,
                        {
                            "status": "completed",
                            "response": full_content,
                            "message_id": message_id,
                            "mode": mode,
                            "level": level,
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
                stream_max_seconds=stream_max_seconds,
                sampled=True,
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
                            "query_stream_quota_refund_failed",
                            error=str(exc),
                            request_id=request_id,
                            user_id_hash=user_id_hash,
                        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


async def save_to_history(user, topic: str, levels: list[str], mode: str) -> None:
    """
    Persist a query to the user's history.

    Failures are logged as errors with full context but do not propagate —
    history loss is preferable to crashing the response task.
    Typically called via _persist_history_safely() for bounded execution.
    """
    user_id_hash = anonymize_user_id(str(getattr(user, "id", "") or ""))
    topic_hash = anonymize_text(topic)

    try:
        await ensure_user_exists(user)
    except Exception as exc:
        logger.error(
            "save_to_history_ensure_user_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            user_id_hash=user_id_hash,
            sampled=False,
        )
        return  # cannot proceed without a valid user row

    supabase = get_supabase_admin()
    if not supabase:
        logger.error(
            "save_to_history_no_supabase_admin",
            user_id_hash=user_id_hash,
            sampled=False,
        )
        return

    normalized_mode = normalize_mode(mode)

    try:
        existing = await asyncio.to_thread(
            lambda: supabase.table("history")
            .select("id, levels")
            .eq("user_id", user.id)
            .eq("topic", topic)
            .eq("mode", normalized_mode)
            .execute()
        )
    except Exception as exc:
        logger.error(
            "save_to_history_fetch_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            user_id_hash=user_id_hash,
            topic_hash=topic_hash,
            sampled=False,
        )
        return

    try:
        data = getattr(existing, "data", None)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            item_id = data[0].get("id")
            existing_levels = set(data[0].get("levels") or [])
            new_levels = list(existing_levels.union(set(levels)))
            await asyncio.to_thread(
                lambda: supabase.table("history")
                .update({"levels": new_levels, "mode": normalized_mode})
                .eq("id", item_id)
                .execute()
            )
            logger.debug(
                "save_to_history_updated",
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                mode=normalized_mode,
            )
        else:
            await asyncio.to_thread(
                lambda: supabase.table("history")
                .insert({
                    "user_id": user.id,
                    "topic": topic,
                    "levels": levels,
                    "mode": normalized_mode,
                })
                .execute()
            )
            logger.debug(
                "save_to_history_inserted",
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                mode=normalized_mode,
            )
    except Exception as exc:
        logger.error(
            "save_to_history_write_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            user_id_hash=user_id_hash,
            topic_hash=topic_hash,
            mode=normalized_mode,
            sampled=False,
        )
