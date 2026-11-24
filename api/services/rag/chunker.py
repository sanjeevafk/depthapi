"""Markdown normalization and heading-aware chunking."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TOC_BLOCK_RE = re.compile(
    r"(?im)^#{1,3}\s+(?:table\s+of\s+contents|contents)\s*\n(?:[ \t]*[-*+\d.].*\n|[ \t]*\n)*"
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class ChunkData:
    content: str
    token_count: int
    chunk_order: int
    content_hash: str
    section_title: str | None = None


def strip_toc(text: str) -> str:
    """Remove leading or standalone Markdown Table of Contents sections."""
    return _TOC_BLOCK_RE.sub("", text).strip()


def normalize_urls(text: str, base_url: str | None = None) -> str:
    """Resolve relative Markdown links when a base_url is provided."""
    if not base_url:
        return text
    from urllib.parse import urljoin

    def _replace(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2).strip()
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        return f"[{label}]({urljoin(base_url, href)})"

    return _MD_LINK_RE.sub(_replace, text)


def chunk_markdown(
    text: str,
    *,
    max_tokens: int = 480,
    min_tokens: int = 1,
) -> list[ChunkData]:
    """Split Markdown into heading-bounded chunks respecting token limits."""
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str | None, str]] = []
    cur: list[str] = []
    cur_tokens = 0
    cur_title: str | None = None

    for line in lines:
        heading_match = _HEADING_RE.match(line.strip())
        if heading_match and cur:
            sections.append((cur_title, "".join(cur).strip()))
            cur = [line]
            cur_tokens = len(line) // 4
            cur_title = heading_match.group(2).strip()
        else:
            if heading_match and cur_title is None:
                cur_title = heading_match.group(2).strip()
            cur.append(line)
            cur_tokens += len(line) // 4
            if cur_tokens >= max_tokens:
                sections.append((cur_title, "".join(cur).strip()))
                cur = []
                cur_tokens = 0

    if cur:
        sections.append((cur_title, "".join(cur).strip()))

    chunks: list[ChunkData] = []
    for title, body in sections:
        if not body:
            continue
        tokens = max(1, len(body) // 4)
        if tokens < min_tokens:
            continue
        chunks.append(
            ChunkData(
                content=body,
                token_count=tokens,
                chunk_order=len(chunks),
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                section_title=title,
            )
        )
    return chunks
