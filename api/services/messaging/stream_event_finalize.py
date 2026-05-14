"""Finalize assistant message handling for streaming flow."""

import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import orjson

import api.services.infra.cache as cache_module
from api.logging_config import logger
from api.services.messaging.message_gate import cache_set_value, append_conversation_message
from api.services.infra.redis_safe import safe_redis_call
from api.services.messaging.stream_persistence import StreamPersistence


async def finalize_assistant_message(
    *,
    content_value: str,
    cacheable: bool,
    stream_completed: bool,
    request_id: str,
    user_id_hash: str,
    conversation_id: str,
    assistant_client_id: str,
    assistant_message_id: str,
    history_limit: int,
    cache_key: str,
    cache_ttl_seconds: int,
    idempotency_key: str,
    idempotency_ttl_seconds: int,
    idempotency_key_hash: str,
    gatekeeper: Any,
    persistence: StreamPersistence,
    schedule_task: Any,
    redis_append_failed_ref: dict[str, bool],
) -> int | None:
    if not content_value.strip():
        logger.warning(
            "messages_finalize_empty_content",
            request_id=request_id,
            user_id_hash=user_id_hash,
            stream_completed=stream_completed,
        )
        return None

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
        conversation_id=conversation_id,
        message_json=orjson.dumps(assistant_payload).decode("utf-8"),
        max_messages=history_limit,
        timeout_seconds=0.8,
    )
    if assistant_sequence_id is None:
        redis_append_failed_ref["value"] = True
    schedule_task(persistence.persist_assistant_message(assistant_sequence_id, content_value))

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
            if redis is not None:
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

    return assistant_sequence_id
