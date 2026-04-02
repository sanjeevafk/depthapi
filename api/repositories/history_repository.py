"""History repository for optimized upsert operations."""

import asyncio
from typing import Any

from auth import get_supabase_admin
from logging_config import logger, anonymize_user_id, anonymize_text
from utils import normalize_mode


class HistoryRepository:
    """Repository for managing history entries with DB-side upsert helpers."""

    @staticmethod
    async def upsert_history(user: Any, topic: str, levels: list[str], mode: str) -> bool:
        """Upsert history using a DB function if available; returns True on success."""
        supabase = get_supabase_admin()
        if not supabase:
            return False

        user_id_hash = anonymize_user_id(str(getattr(user, "id", "") or ""))
        topic_hash = anonymize_text(topic)
        normalized_mode = normalize_mode(mode)

        payload = {
            "p_user_id": str(getattr(user, "id", "") or ""),
            "p_topic": topic,
            "p_mode": normalized_mode,
            "p_levels": levels,
        }
        try:
            await asyncio.to_thread(lambda: supabase.rpc("upsert_history", payload).execute())
            logger.debug(
                "history_upsert_rpc_ok",
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                mode=normalized_mode,
            )
            return True
        except Exception as exc:
            logger.warning(
                "history_upsert_rpc_failed",
                error=str(exc),
                user_id_hash=user_id_hash,
                topic_hash=topic_hash,
                mode=normalized_mode,
            )
            return False
