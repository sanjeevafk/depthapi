"""Conversation context materialization for message routing.

Responsibilities:
- Load and validate cached conversation snapshots.
- Fallback to DB conversation history when cache is missing/degraded.
- Build bounded context windows/signatures for cache keys and prompts.
- Derive turn-level context for socratic and mode-specific prompting.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from api.logging_config import anonymize_user_id, logger
from api.services.cache import get_redis
from api.services.conversation_context import (
    ConversationMessage,
    build_context_messages,
    build_socratic_context,
    extract_last_turns,
)
from api.services.message_utils import safe_json_parse
from api.services.redis_safe import safe_redis_call


FetchSnapshotFn = Callable[..., Awaitable[tuple[str | None, list[str]]]]
WarmSnapshotFn = Callable[..., Awaitable[None]]
GetSupabaseAdminFn = Callable[[], Any]


@dataclass(frozen=True)
class SnapshotLoadResult:
    meta_raw: str | None
    raw_messages: list[str]
    meta: dict[str, Any]
    snapshot_ms: float
    snapshot_degraded: bool


class ContextBuilder:
    async def parse_snapshot_meta(self, raw: str | None, conversation_id: str) -> dict[str, Any]:
        if not raw:
            return {}
        loaded = safe_json_parse(raw)
        if isinstance(loaded, dict):
            return loaded
        try:
            redis = await safe_redis_call(get_redis, operation="connect")
            if redis is not None:
                await safe_redis_call(
                    redis.delete,
                    f"depthapi:conversation:{conversation_id}:meta",
                    operation="delete",
                )
        except Exception as exc:
            logger.warning(
                "messages_snapshot_meta_cleanup_failed",
                conversation_id=conversation_id,
                error=str(exc),
            )
        return {}

    async def parse_snapshot_messages(
        self,
        raw_messages: list[str],
        conversation_id: str,
    ) -> list[ConversationMessage]:
        messages: list[ConversationMessage] = []
        corrupted = False
        for raw in raw_messages:
            payload = safe_json_parse(raw)
            if payload is None:
                corrupted = True
                continue
            if isinstance(payload, dict):
                role = str(payload.get("role") or "")
                content = str(payload.get("content") or "")
                if role and content is not None:
                    messages.append({"role": role, "content": content})
        if corrupted:
            try:
                redis = await safe_redis_call(get_redis, operation="connect")
                if redis is not None:
                    await safe_redis_call(
                        redis.delete,
                        f"depthapi:conversation:{conversation_id}:messages",
                        operation="delete",
                    )
            except Exception as exc:
                logger.warning(
                    "messages_snapshot_messages_cleanup_failed",
                    conversation_id=conversation_id,
                    error=str(exc),
                )
        return messages

    async def warm_cache(
        self,
        *,
        conversation_id: str,
        user_id: str,
        warm_snapshot: WarmSnapshotFn,
        timeout_seconds: float = 0.8,
    ) -> None:
        try:
            await asyncio.wait_for(warm_snapshot(conversation_id, user_id), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "messages_snapshot_warm_timeout",
                conversation_id=conversation_id,
                user_id_hash=anonymize_user_id(user_id),
            )
        except Exception as exc:
            logger.exception(
                "messages_snapshot_warm_exception",
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def load_snapshot(
        self,
        *,
        conversation_id: str,
        user_id: str,
        history_limit: int,
        request_id: str,
        fetch_snapshot: FetchSnapshotFn,
        warm_snapshot: WarmSnapshotFn,
    ) -> SnapshotLoadResult:
        snapshot_start = time.perf_counter()
        snapshot_meta_raw, snapshot_raw_messages = await fetch_snapshot(
            conversation_id=conversation_id,
            max_messages=history_limit,
            timeout_seconds=0.8,
        )
        snapshot_meta = await self.parse_snapshot_meta(snapshot_meta_raw, conversation_id)

        if not snapshot_meta_raw:
            await self.warm_cache(
                conversation_id=conversation_id,
                user_id=user_id,
                warm_snapshot=warm_snapshot,
                timeout_seconds=0.8,
            )
            snapshot_meta_raw, snapshot_raw_messages = await fetch_snapshot(
                conversation_id=conversation_id,
                max_messages=history_limit,
                timeout_seconds=0.8,
            )
            if snapshot_meta_raw:
                snapshot_meta = await self.parse_snapshot_meta(snapshot_meta_raw, conversation_id)

        snapshot_ms = (time.perf_counter() - snapshot_start) * 1000
        snapshot_degraded = not bool(snapshot_meta_raw)
        logger.info(
            "timing_snapshot_load",
            request_id=request_id,
            conversation_id=conversation_id,
            snapshot_ms=round(snapshot_ms, 2),
            snapshot_degraded=snapshot_degraded,
        )
        return SnapshotLoadResult(
            meta_raw=snapshot_meta_raw,
            raw_messages=snapshot_raw_messages,
            meta=snapshot_meta,
            snapshot_ms=snapshot_ms,
            snapshot_degraded=snapshot_degraded,
        )

    async def load_conversation_from_db(
        self,
        conversation_id: str,
        user_id: str,
        history_limit: int,
        *,
        get_supabase_admin_fn: GetSupabaseAdminFn,
    ) -> tuple[dict[str, Any], list[ConversationMessage]]:
        supabase = get_supabase_admin_fn()
        if not supabase:
            return {}, []

        try:
            conversation_resp = await (
                supabase.table("conversations")
                .select("id, user_id, mode, settings, updated_at")
                .eq("id", conversation_id)
                .single()
                .execute()
            )
            conversation = getattr(conversation_resp, "data", None)
            if not isinstance(conversation, dict):
                return {}, []
            if str(conversation.get("user_id") or "") != user_id:
                return {}, []

            messages_resp = await (
                supabase.table("messages")
                .select("role, content, created_at, sequence_id")
                .eq("conversation_id", conversation_id)
                .order("sequence_id", desc=True, nullsfirst=False)
                .order("created_at", desc=True)
                .limit(history_limit)
                .execute()
            )
            rows = getattr(messages_resp, "data", None)
            raw_messages = list(reversed(rows)) if isinstance(rows, list) else []
        except Exception as exc:
            logger.warning(
                "messages_db_snapshot_failed",
                conversation_id=conversation_id,
                error=str(exc),
            )
            return {}, []

        history_messages: list[ConversationMessage] = []
        for row in raw_messages:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "").strip()
            content = str(row.get("content") or "").strip()
            if role and content:
                history_messages.append({"role": role, "content": content})

        return conversation, history_messages

    def extract_turns(self, messages: list[ConversationMessage]) -> tuple[str | None, str | None]:
        return extract_last_turns(messages)

    def build_socratic_context(self, messages: list[ConversationMessage]) -> str:
        return build_socratic_context(messages)

    async def build_context(
        self,
        history_messages: list[ConversationMessage],
        *,
        request_id: str,
        conversation_id: str,
        context_max_tokens: int,
        summary_max_tokens: int,
        max_turns: int = 4,
    ) -> tuple[list[ConversationMessage], str, float]:
        local_prompt_build_start = time.perf_counter()
        loaded_messages, loaded_signature = build_context_messages(
            history_messages,
            max_tokens=max(context_max_tokens, 1),
            summary_max_tokens=max(summary_max_tokens, 0),
            max_turns=max_turns,
        )
        local_prompt_build_ms = (time.perf_counter() - local_prompt_build_start) * 1000
        logger.info(
            "context_messages_ready",
            request_id=request_id,
            conversation_id=conversation_id,
            context_messages_count=len(loaded_messages),
            context_signature_prefix=loaded_signature[:16],
            context_build_ms=round(local_prompt_build_ms, 2),
        )
        return loaded_messages, loaded_signature, local_prompt_build_ms
