import asyncio
import time
from typing import Any

import orjson

from auth import get_supabase_admin
from config import get_settings
from logging_config import logger
from services.cache import get_redis


def _meta_key(conversation_id: str) -> str:
    return f"knowbear:conversation:{conversation_id}:meta"


def _messages_key(conversation_id: str) -> str:
    return f"knowbear:conversation:{conversation_id}:messages"


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

        history_resp = await asyncio.to_thread(
            lambda: supabase.table("messages")
            .select("role, content, created_at, metadata")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(history_limit)
            .execute()
        )
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

    redis = await get_redis()
    if not redis:
        return
    try:
        await redis.pipeline(
            [
                ["DEL", _messages_key(conversation_id)],
                ["SETEX", _meta_key(conversation_id), 3600, orjson.dumps(meta_payload).decode("utf-8")],
            ]
        )
        if messages:
            payloads = [orjson.dumps(msg).decode("utf-8") for msg in messages]
            await redis.rpush(_messages_key(conversation_id), *payloads)
            await redis.ltrim(_messages_key(conversation_id), -history_limit, -1)
    except Exception as exc:
        logger.warning(
            "messages_snapshot_warm_redis_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
