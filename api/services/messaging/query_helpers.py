"""Query route helper functions."""

from __future__ import annotations

import asyncio
import time

from api.repositories.chat_repository import ChatRepository
from api.config import get_settings
from api.logging_config import anonymize_text, anonymize_user_id, logger
from api.utils import PROMPT_MODE_ALIASES, normalize_mode, topic_cache_key


def normalize_levels(levels: list[str]) -> list[str]:
    normalized = []
    for level in levels or []:
        normalized.append(PROMPT_MODE_ALIASES.get(level, level))
    return normalized


def cache_key(topic: str, level: str, mode: str) -> str:
    return topic_cache_key(topic, level, mode=normalize_mode(mode))


async def persist_history_safely(
    user,
    topic: str,
    prompt_specs: list[dict],
    mode: str,
) -> None:
    """Persist history within a bounded timeout so request lifecycles remain responsive."""
    timeout_seconds = max(float(get_settings().stream_heartbeat_seconds or 1.0), 1.0)
    try:
        await asyncio.wait_for(
            ChatRepository.save_to_history(user, topic, prompt_specs),
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
