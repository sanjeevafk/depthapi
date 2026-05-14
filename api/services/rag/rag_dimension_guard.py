"""Runtime guardrails for embedding dimension consistency."""

from __future__ import annotations

import structlog

from api.auth import get_supabase_admin
from api.config import get_settings

logger = structlog.get_logger(__name__)


async def get_pgvector_dimension() -> int | None:
    """Return remote knowledge_chunks.embedding dimension, or None if unavailable."""
    client = get_supabase_admin()
    if client is None:
        return None
    try:
        res = await client.rpc("get_embedding_dimension", {}).execute()
        rows = res.data if res and hasattr(res, "data") else []
        if not rows:
            return None
        value = (rows[0] or {}).get("dimension")
        if value is None:
            return None
        return int(value)
    except Exception as exc:
        logger.warning("rag_dimension_query_failed", error=str(exc))
        return None


async def validate_embedding_dimension_or_raise() -> None:
    settings = get_settings()
    configured = int(getattr(settings, "embedding_dimension", 768))
    remote = await get_pgvector_dimension()
    if remote is None:
        logger.warning("rag_dimension_remote_unknown", configured=configured)
        return
    if configured != remote:
        raise RuntimeError(
            f"Embedding dimension mismatch: app={configured}, knowledge_chunks.embedding={remote}. "
            "Align config or database vector dimension before serving traffic."
        )


async def get_dimension_guard_status() -> dict:
    settings = get_settings()
    configured = int(getattr(settings, "embedding_dimension", 768))
    remote = await get_pgvector_dimension()
    if remote is None:
        return {
            "status": "unknown",
            "configured_dimension": configured,
            "database_dimension": None,
        }
    return {
        "status": "ok" if configured == remote else "mismatch",
        "configured_dimension": configured,
        "database_dimension": remote,
    }

