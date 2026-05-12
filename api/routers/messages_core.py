"""Chat messages endpoint."""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from api.auth import get_supabase_admin
from api.config import CONTEXT_LOAD_TIMEOUTS, get_settings
from api.logging_config import anonymize_text, anonymize_user_id, logger
import api.services.cache as cache_module
from api.services.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.conversation_cache import warm_conversation_snapshot
from api.services.context_builder import ContextBuilder
from api.services.llm_client import get_provider_config_state
from api.services.llm_errors import LLMUnavailable
from api.services.message_workflow import MessageWorkflow
from api.services.request_validator import RequestValidator
from api.services.redis_safe import safe_redis_call
from api.services.message_utils import normalize_mode
from api.services.message_dispatcher import MessageDispatcher
from api.services.message_gate import fetch_conversation_snapshot
from api.services.streaming_message_pipeline import StreamingMessagePipeline
from api.services.conversation_lock_manager import ConversationLockManager
from api.utils import (
    PROMPT_MODE_ALIASES,
    SUPPORTED_PROMPT_MODES,
    LEARNING_MODE,
    SOCRATIC_MODE,
    TECHNICAL_MODE,
    normalize_prompt_level,
)

router = APIRouter(tags=["messages"])
_request_validator = RequestValidator()
_context_builder = ContextBuilder()
_message_dispatcher = MessageDispatcher()
_message_workflow = MessageWorkflow()
_lock_manager = ConversationLockManager(max_locks=10000, ttl_seconds=600)
# Public aliases used by facade modules/tests without touching private names.
message_workflow = _message_workflow


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
    collection_id: Optional[str] = None
    use_trusted_corpus: bool = True


def _idempotency_key(user_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{message_id}".encode("utf-8")).hexdigest()
    return f"depthapi:idempotency:{digest}"


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


def _validate_message_boundary(payload: Any) -> tuple[str, str | None]:
    result = _request_validator.validate_message_request(payload)
    if not result.ok:
        raise _bad_request(str(result.error_message or "Invalid request payload"))
    return result.content, result.normalized_mode


async def _ingress_dedupe_check(message_id: str, ttl_seconds: float = 3.0) -> bool:
    return await _request_validator.check_deduplication(message_id, ttl_seconds=ttl_seconds)


async def _ingress_dedupe_clear(message_id: str) -> None:
    await _request_validator.clear_deduplication(message_id)


@dataclass
class _MessagePreflightResult:
    request_received: float
    request_id: str
    req: MessageRequest
    normalized_mode: str | None
    content: str
    user_id: str
    is_pro: bool
    user_id_hash: str
    content_hash: str
    client_message_id: str
    assistant_client_id: str
    idempotency_key: str
    idempotency_key_hash: str


@dataclass
class _MessageSetupResult:
    config_settings: Any
    is_prod: bool
    cache_ttl_seconds: int
    stream_max_seconds: int
    fallback_budget_seconds: float
    fallback_timeout_seconds: float
    close_timeout_seconds: float
    heartbeat_seconds: float
    stream_start_timeout_seconds: float
    idempotency_ttl_seconds: int
    snapshot_meta_raw: dict[str, Any]
    snapshot_raw_messages: list[dict[str, Any]]
    snapshot_meta: dict[str, Any]
    snapshot_ms: float
    snapshot_degraded: bool
    selected_mode: str
    llm_mode: str
    prompt_mode: str


async def _run_message_preflight(
    request: Request,
    api_key: ApiKeyRecord,
) -> _MessagePreflightResult:
    request_received = time.perf_counter()
    request_id = str(getattr(request.state, "request_id", "") or "")
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

    return _MessagePreflightResult(
        request_received=request_received,
        request_id=request_id,
        req=req,
        normalized_mode=normalized_mode,
        content=content,
        user_id=user_id,
        is_pro=is_pro,
        user_id_hash=user_id_hash,
        content_hash=content_hash,
        client_message_id=client_message_id,
        assistant_client_id=assistant_client_id,
        idempotency_key=idempotency_key,
        idempotency_key_hash=idempotency_key_hash,
    )


async def _resolve_message_setup(
    *,
    preflight: _MessagePreflightResult,
    api_key: ApiKeyRecord,
) -> _MessageSetupResult:
    req = preflight.req
    normalized_mode = preflight.normalized_mode
    user_id = preflight.user_id
    is_pro = preflight.is_pro
    request_id = preflight.request_id
    client_message_id = preflight.client_message_id

    config_settings = get_settings()
    environment = str(getattr(config_settings, "environment", "") or "").strip().lower()
    is_prod = environment == "production"
    cache_ttl_seconds = max(int(getattr(config_settings, "message_cache_ttl_seconds", 3600)), 1)
    stream_max_seconds = max(int(getattr(config_settings, "stream_max_seconds", 24)), 1)
    if not is_prod:
        stream_max_seconds = max(stream_max_seconds, 60)
    function_duration_cap: int | None = None
    if is_prod:
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

    if llm_mode == TECHNICAL_MODE and not is_pro:
        await _ingress_dedupe_clear(client_message_id)
        raise HTTPException(status_code=403, detail="Technical mode is a Pro feature")
    if llm_mode == SOCRATIC_MODE and not is_pro:
        await _ingress_dedupe_clear(client_message_id)
        raise HTTPException(status_code=403, detail="Socratic mode is a Pro feature")

    return _MessageSetupResult(
        config_settings=config_settings,
        is_prod=is_prod,
        cache_ttl_seconds=cache_ttl_seconds,
        stream_max_seconds=stream_max_seconds,
        fallback_budget_seconds=fallback_budget_seconds,
        fallback_timeout_seconds=fallback_timeout_seconds,
        close_timeout_seconds=close_timeout_seconds,
        heartbeat_seconds=heartbeat_seconds,
        stream_start_timeout_seconds=stream_start_timeout_seconds,
        idempotency_ttl_seconds=idempotency_ttl_seconds,
        snapshot_meta_raw=snapshot_meta_raw,
        snapshot_raw_messages=snapshot_raw_messages,
        snapshot_meta=snapshot_meta,
        snapshot_ms=snapshot_ms,
        snapshot_degraded=snapshot_degraded,
        selected_mode=selected_mode,
        llm_mode=llm_mode,
        prompt_mode=prompt_mode,
    )


_pipeline: StreamingMessagePipeline | None = None


def _get_pipeline() -> StreamingMessagePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = StreamingMessagePipeline(
            context_builder=_context_builder,
            message_dispatcher=_message_dispatcher,
            lock_manager=_lock_manager,
            ingress_dedupe_clear=_ingress_dedupe_clear,
        )
    return _pipeline


async def _send_message_handler(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    preflight = await _run_message_preflight(request, api_key)
    setup = await _resolve_message_setup(preflight=preflight, api_key=api_key)
    pipeline = _get_pipeline()
    return await pipeline.execute(
        request=request,
        api_key=api_key,
        preflight=preflight,
        setup=setup,
    )


async def send_message_handler(
    request: Request,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> StreamingResponse:
    """Public wrapper to avoid private-usage warnings in facades."""
    return await _send_message_handler(request=request, api_key=api_key)


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
