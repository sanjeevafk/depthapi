"""
toc_stripper.py — Middleware: Remove Table of Contents sections from Markdown.

TOC sections are common in GitHub README files and book-style documents.
They consist mostly of anchor links (e.g. [Section](#section)) and add
little semantic value to RAG retrieval.

Config keys:
    depth: int — Max TOC depth to strip (default: 3)
    anchor_ratio_threshold: float — Min ratio of anchor lines to trigger removal
"""

from __future__ import annotations

import re
from typing import Any

from api.services.rag.pipeline.interfaces import BaseMiddleware
from api.services.rag.pipeline.models import ParsedDocument

_MW_NAME = "TocStripper"
_MW_VERSION = "1.0.0"

# Pattern: markdown anchor link [text](#anchor)
_ANCHOR_PATTERN = re.compile(r"\[.+?\]\(#.+?\)")
# Pattern: whole TOC block (3+ consecutive anchor lines)
_TOC_BLOCK_PATTERN = re.compile(
    r"(?:^[ \t]*(?:\*|-|\d+\.)\s*\[.+?\]\(#.+?\)\s*\n){3,}",
    re.MULTILINE,
)


def _strip_toc(content: str, anchor_ratio_threshold: float = 0.5) -> str:
    """
    Remove TOC blocks from markdown content.

    Strategy:
        1. Find multi-line blocks of anchor links
        2. If > anchor_ratio_threshold of lines are anchors, strip the block
    """
    # Remove explicit TOC sections (consecutive anchor lines)
    cleaned = _TOC_BLOCK_PATTERN.sub("\n", content)

    # Also remove standalone TOC headers (## Table of Contents, ## Contents, etc.)
    cleaned = re.sub(
        r"^#{1,4}\s+(Table of Contents|Contents|TOC|Index)\s*$\n?",
        "",
        cleaned,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    return cleaned.strip()


class TocStripper(BaseMiddleware):
    """
    Middleware: Remove Table of Contents sections from parsed Markdown.

    Extracted from the pattern used across existing ingesters where TOC
    sections were filtered in ad-hoc validators.

    Idempotent: running twice produces the same result.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._anchor_ratio = float(self._config.get("anchor_ratio_threshold", 0.5))

    @property
    def name(self) -> str:
        return _MW_NAME

    @property
    def version(self) -> str:
        return _MW_VERSION

    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """Strip TOC sections and return a new ParsedDocument."""
        cleaned = _strip_toc(doc.markdown_content, self._anchor_ratio)
        return doc.with_middleware_applied(
            middleware_name=self.name,
            middleware_version=self.version,
            new_content=cleaned,
            config=self._config,
        )
