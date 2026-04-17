import asyncio
import time
from typing import Any

import orjson

from auth import get_supabase_admin
from config import get_settings
from logging_config import logger
from services.cache import get_redis
from services.redis_safe import safe_redis_call


def _meta_key(conversation_id: str) -> str:
    return f"knowbear:conversation:{conversation_id}:meta"


def _messages_key(conversation_id: str) -> str:
    return f"knowbear:conversation:{conversation_id}:messages"


WARM_SNAPSHOT_LUA = """
-- KEYS: [meta_key, messages_key]
-- ARGV: [meta_json, ttl_seconds, max_messages, message_json...]
local meta_key = KEYS[1]
local list_key = KEYS[2]
local meta_json = ARGV[1]
local ttl = tonumber(ARGV[2]) or 0
local max_messages = tonumber(ARGV[3]) or 0

redis.call('DEL', list_key)
if ttl > 0 then
    redis.call('SETEX', meta_key, ttl, meta_json)
else
    redis.call('SET', meta_key, meta_json)
end

if #ARGV > 3 then
    for i = 4, #ARGV do
        redis.call('RPUSH', list_key, ARGV[i])
    end
    if max_messages > 0 then
        redis.call('LTRIM', list_key, -max_messages, -1)
    end
    if ttl > 0 then
        redis.call('EXPIRE', list_key, ttl)
    end
end

return 1
"""


async def warm_conversation_snapshot(conversation_id: str, user_id: str | None) -> None:
    supabase = get_supabase_admin()
    if not supabase:
        return

    settings = get_settings()
    history_limit = max(int(getattr(settings, "conversation_context_fetch_limit", 80)), 1)
    try:
        conversation_resp = await asyncio.to_thread(
            lambda: supabase.table("conversations")
            .select("id, user_id, mode, settings, updated_at")
            .eq("id", conversation_id)
            .single()
            .execute()
        )
        conversation = getattr(conversation_resp, "data", None)
        if not isinstance(conversation, dict):
            return
        if user_id and str(conversation.get("user_id") or "") != user_id:
            return

        def _history_query():
            base = (
                supabase.table("messages")
                .select("id, role, content, created_at, metadata, sequence_id")
                .eq("conversation_id", conversation_id)
            )
            # Some test doubles and SDK adapters don't support nullsfirst.
            try:
                ordered = base.order("sequence_id", desc=True, nullsfirst=False)
            except TypeError:
                ordered = base.order("sequence_id", desc=True)
            return ordered.limit(history_limit).execute()

        history_resp = await asyncio.to_thread(_history_query)
        rows = getattr(history_resp, "data", None)
        messages = list(reversed(rows)) if isinstance(rows, list) else []
    except Exception as exc:
        logger.warning(
            "messages_snapshot_warm_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
        return

    meta_payload = {
        "conversation_id": conversation_id,
        "user_id": conversation.get("user_id"),
        "mode": conversation.get("mode"),
        "prompt_mode": (conversation.get("settings") or {}).get("prompt_mode"),
        "updated_at": conversation.get("updated_at"),
        "refreshed_at": time.time(),
    }

    redis = await safe_redis_call(get_redis, operation="connect")
    if redis is None:
        return
    try:
        payloads = [orjson.dumps(msg).decode("utf-8") for msg in messages]
        await safe_redis_call(
            redis.eval,
            WARM_SNAPSHOT_LUA,
            2,
            _meta_key(conversation_id),
            _messages_key(conversation_id),
            orjson.dumps(meta_payload).decode("utf-8"),
            3600,
            history_limit,
            *payloads,
            operation="eval",
        )
    except Exception as exc:
        logger.warning(
            "messages_snapshot_warm_redis_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
