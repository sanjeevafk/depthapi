"""Retrieval context normalization, compression, and ID canonicalization."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


_DECORATIVE_MD_RE = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_REPEATED_WS_RE = re.compile(r"[ \t]{2,}")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def rough_token_count(text: str) -> int:
    return max(1, len(text or "") // 4)


def canonical_id(value: Any) -> str:
    """Normalize document/chunk IDs for exact-match retrieval metrics."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.strip().lower()
    text = re.sub(r"^doc(?:ument)?[_:\-\s]+", "", text)
    text = re.sub(r"^chunk[_:\-\s]+", "", text)
    text = re.sub(r"[\s/|:]+", "-", text)
    text = re.sub(r"[^a-z0-9#._-]+", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text


def normalize_context_text(text: str, *, max_chars: int = 1000) -> str:
    """Compress context while preserving technical meaning and citations."""
    if _HAS_DEPTH_ENGINE:
        try:
            return depth_engine.normalize_context_text(text, max_chars)
        except Exception:
            pass

    value = str(text or "")
    value = _DECORATIVE_MD_RE.sub("", value)
    value = _MD_LINK_RE.sub(r"\1 (\2)", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", value)
    value = re.sub(r"(?m)^\s*>\s?", "", value)
    value = _REPEATED_WS_RE.sub(" ", value)
    value = _MULTI_BLANK_RE.sub("\n\n", value).strip()

    seen: set[str] = set()
    deduped: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(value):
        normalized = re.sub(r"\W+", " ", part).strip().lower()
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        deduped.append(part.strip())
    value = " ".join(part for part in deduped if part)

    if len(value) <= max_chars:
        return value
    cut = value[:max_chars].rstrip()
    boundary = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind("; "), cut.rfind(" "))
    if boundary > max_chars * 0.65:
        cut = cut[: boundary + 1].rstrip()
    return f"{cut}..."


def compress_contexts(
    contexts: list[dict[str, Any]],
    *,
    max_contexts: int = 3,
    max_chars_per_context: int = 1000,
    max_total_chars: int = 3000,
) -> list[dict[str, Any]]:
    """Normalize selected contexts and enforce a total prompt budget."""
    if _HAS_DEPTH_ENGINE:
        try:
            return list(
                depth_engine.compress_contexts(
                    contexts,
                    max_contexts=max_contexts,
                    max_chars_per_context=max_chars_per_context,
                    max_total_chars=max_total_chars,
                )
            )
        except Exception:
            pass

    compressed: list[dict[str, Any]] = []
    total_chars = 0
    seen_texts: set[str] = set()
    seen_docs: set[str] = set()

    for context in contexts:
        if len(compressed) >= max_contexts or total_chars >= max_total_chars:
            break
        raw_text = str(context.get("content") or context.get("text") or "")
        remaining = max_total_chars - total_chars
        max_chars = max(250, min(max_chars_per_context, remaining))
        text = normalize_context_text(raw_text, max_chars=max_chars)
        fingerprint = re.sub(r"\W+", " ", text[:500]).strip().lower()
        doc_id = canonical_id(context.get("document_id") or context.get("doc_id") or context.get("source_name"))
        if fingerprint and fingerprint in seen_texts:
            continue
        if doc_id and doc_id in seen_docs and len(compressed) >= 1:
            continue
        item = dict(context)
        item["content"] = text
        item["token_count"] = int(item.get("token_count") or rough_token_count(text))
        compressed.append(item)
        total_chars += len(text)
        if fingerprint:
            seen_texts.add(fingerprint)
        if doc_id:
            seen_docs.add(doc_id)

    return compressed


def reorder_lost_in_the_middle(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder contexts into a U-shaped distribution to prevent lost-in-the-middle degradation.

    Places highest-scoring contexts at the beginning and end of the prompt context,
    where LLM attention weight is strongest:
    Input ranks:  [0, 1, 2, 3, 4]
    Output ranks: [0, 2, 4, 3, 1]
    """
    if len(contexts) <= 2:
        return list(contexts)

    if _HAS_DEPTH_ENGINE:
        try:
            return list(depth_engine.reorder_lost_in_the_middle(contexts))
        except Exception:
            pass

    reordered: list[dict[str, Any]] = [None] * len(contexts)  # type: ignore[list-item]
    left = 0
    right = len(contexts) - 1

    for i, ctx in enumerate(contexts):
        if i % 2 == 0:
            reordered[left] = ctx
            left += 1
        else:
            reordered[right] = ctx
            right -= 1

    return reordered
