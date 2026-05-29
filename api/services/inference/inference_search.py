"""Search context loading helpers for inference."""

from __future__ import annotations

import hashlib
import asyncio
import time

from api.services.rag.context_processing import compress_contexts
from api.services.rag.search import search_service
from api.services.inference.inference_constants import SEARCH_CONTEXT_MAX_CHARS, SEARCH_CONTEXT_TIMEOUT_SECONDS
from api.logging_config import logger


def _hash_topic(topic: str) -> str:
    return hashlib.sha256((topic or "").strip().lower().encode("utf-8")).hexdigest()


def _truncate_search_context(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= SEARCH_CONTEXT_MAX_CHARS:
        return text
    return f"{text[:SEARCH_CONTEXT_MAX_CHARS].rstrip()}..."


def _append_search_context(prompt: str, context: str) -> str:
    if not context:
        return prompt
    return (
        f"{prompt}\n\n"
        "External web context (supplemental, may be incomplete):\n"
        f"{context}\n\n"
        "Synthesize rather than copy. Combine overlapping information, only make supported claims, "
        "and cite supporting contexts where appropriate."
    )


def _append_rag_context(prompt: str, context: str) -> str:
    if not context:
        return prompt
    return (
        f"{prompt}\n\n"
        "--- RETRIEVED DEVELOPER KNOWLEDGE ---\n"
        "Synthesize a concise technical explanation using the retrieved contexts.\n"
        "Do not copy passages verbatim. Combine overlapping information.\n"
        "Prefer abstraction and explanation over repetition.\n"
        "Use grounded technical reasoning and only include claims supported by retrieved context.\n"
        "Cite supporting contexts where appropriate. If the content does not answer the question, say so.\n"
        "---\n"
        f"{context}\n"
        "--- END RETRIEVED KNOWLEDGE ---"
    )


def format_rag_context(results: list[dict]) -> str:
    if not results:
        return ""
    
    formatted = []
    for i, res in enumerate(compress_contexts(results)):
        content = res.get("content", "").strip()
        source = res.get("citation", {}).get("source_url") or res.get("citation", {}).get("source_tier") or "Unknown"
        section = (res.get("metadata") or {}).get("section_title") or res.get("section_title")
        label = f"[{i+1}] Source: {source}"
        if section:
            label += f" | Section: {section}"
        formatted.append(f"{label}\n{content}")
    
    return "\n\n".join(formatted)


async def _load_search_context(topic: str, *, mode: str) -> str:
    normalized_topic = " ".join((topic or "").strip().split())
    if not normalized_topic:
        return ""

    search_start = time.perf_counter()
    try:
        context = await asyncio.wait_for(
            search_service.get_search_context(normalized_topic),
            timeout=SEARCH_CONTEXT_TIMEOUT_SECONDS,
        )
        search_ms = (time.perf_counter() - search_start) * 1000
        logger.info(
            "timing_search_context_success",
            mode=mode,
            topic_hash=_hash_topic(normalized_topic),
            search_ms=round(search_ms, 2),
        )
    except asyncio.TimeoutError:
        search_ms = (time.perf_counter() - search_start) * 1000
        logger.warning(
            "timing_search_context_timeout",
            mode=mode,
            topic_hash=_hash_topic(normalized_topic),
            search_ms=round(search_ms, 2),
            timeout_seconds=SEARCH_CONTEXT_TIMEOUT_SECONDS,
        )
        return ""
    except Exception as exc:
        logger.warning(
            "search_context_unavailable",
            mode=mode,
            topic_hash=_hash_topic(normalized_topic),
            error=str(exc),
        )
        return ""

    return _truncate_search_context(str(context or ""))
