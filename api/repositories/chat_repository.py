"""Chat repository for wrapping Supabase database operations."""

from typing import Any, Dict, List, Optional, cast
from api.logging_config import logger, anonymize_user_id, anonymize_text
from api.auth import ensure_user_exists, get_supabase_admin
from api.repositories.history_repository import HistoryRepository
from api.utils import normalize_mode


class ChatRepository:
    """Repository for managing chat history, conversations, and messages."""

    @staticmethod
    async def save_to_history(user: Any, topic: str, levels: list[str], mode: str) -> None:
        """Persist a query to the user's history."""
        user_id_hash = anonymize_user_id(str(getattr(user, "id", "") or ""))
        topic_hash = anonymize_text(topic)
        normalized_mode = normalize_mode(mode)

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
            return

        upserted = await HistoryRepository.upsert_history(user, topic, levels, mode)
        if upserted:
            return

        supabase = get_supabase_admin()
        if not supabase:
            logger.error("save_to_history_no_supabase_admin", user_id_hash=user_id_hash, sampled=False)
            return

        try:
            existing = await (
                supabase.table("history")
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
                await (
                    supabase.table("history")
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
                await (
                    supabase.table("history")
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

    @staticmethod
    async def get_conversation(conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a conversation."""
        supabase = get_supabase_admin()
        if not supabase:
            raise RuntimeError("Database connection error")
        resp = await (
            supabase.table("conversations")
            .select("id, user_id, mode, settings")
            .eq("id", conversation_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return cast(Dict[str, Any], resp.data) if getattr(resp, "data", None) else None

    @staticmethod
    def batch_insert_message_setup(
        conversation_id: str,
        content: str,
        user_metadata: dict,
        assistant_metadata: dict,
        update_payload: dict,
    ):
        """Returns the lambdas for batch insertion via gather."""
        supabase = get_supabase_admin()
        if not supabase:
            raise RuntimeError("Database connection error")

        def _insert_user():
            return supabase.table("messages").insert({
                "conversation_id": conversation_id,
                "role": "user",
                "content": content,
                "metadata": user_metadata,
            }).execute()

        def _update_conv():
            return supabase.table("conversations").update(update_payload).eq("id", conversation_id).execute()

        def _insert_assistant():
            return supabase.table("messages").insert({
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": "",
                "metadata": assistant_metadata,
            }).execute()
        
        return _insert_user, _update_conv, _insert_assistant

    @staticmethod
    async def insert_message_bundle_rpc(
        conversation_id: str,
        content: str,
        user_metadata: dict,
        assistant_metadata: dict,
        update_payload: dict,
    ) -> str | None:
        """Insert user + assistant messages and update conversation via RPC."""
        supabase = get_supabase_admin()
        if not supabase:
            return None

        payload = {
            "p_conversation_id": conversation_id,
            "p_user_content": content,
            "p_user_metadata": user_metadata,
            "p_assistant_metadata": assistant_metadata,
            "p_update_payload": update_payload,
        }
        try:
            response = await supabase.rpc("insert_message_bundle", payload).execute()
            data = getattr(response, "data", None)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                candidate = data.get("insert_message_bundle") or data.get("id")
                return str(candidate) if candidate else None
            if data:
                return str(data)
        except Exception as exc:
            logger.warning("messages_bundle_rpc_failed", error=str(exc), conversation_id=conversation_id)
        return None

    @staticmethod
    def update_assistant_message(assistant_message_id: str, full_content: str):
        """Update the content of an assistant message."""
        supabase = get_supabase_admin()
        if not supabase:
            raise RuntimeError("Database connection error")
        return supabase.table("messages").update({"content": full_content}).eq("id", assistant_message_id).execute()
