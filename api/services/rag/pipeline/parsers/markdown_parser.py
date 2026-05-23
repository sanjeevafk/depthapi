"""
markdown_parser.py — Parser plugin for Markdown documents.

Converts raw Markdown bytes into a ParsedDocument with:
    - UTF-8 decoding
    - Extraction confidence based on content quality heuristics
    - No lossy transformation (raw markdown preserved as-is)

Config keys: (none required)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from api.services.rag.pipeline.interfaces import BaseParser, ParserCapabilities
from api.services.rag.pipeline.models import Document, ParsedDocument

log = logging.getLogger(__name__)

_PARSER_NAME = "MarkdownParser"
_PARSER_VERSION = "1.0.0"


def _estimate_confidence(content: str) -> float:
    """
    Heuristic extraction confidence for Markdown content.

    Returns a value in [0.5, 1.0]:
        - 1.0: well-structured markdown with headers and paragraphs
        - 0.5: empty or near-empty content
    """
    if not content or len(content.strip()) < 50:
        return 0.5

    lines = content.splitlines()
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return 0.5

    has_headers = any(l.startswith("#") for l in non_empty)
    has_paragraphs = len(non_empty) > 5
    has_code_blocks = "```" in content

    score = 0.7  # baseline
    if has_headers:
        score += 0.1
    if has_paragraphs:
        score += 0.1
    if has_code_blocks:
        score += 0.1

    return min(1.0, round(score, 2))


class MarkdownParser(BaseParser):
    """
    Parser for text/markdown MIME type.

    No transformation applied — raw markdown is preserved exactly.
    Extraction confidence is estimated from structural heuristics.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    @property
    def name(self) -> str:
        return _PARSER_NAME

    @property
    def version(self) -> str:
        return _PARSER_VERSION

    def supports_mime_type(self, mime_type: str) -> bool:
        return mime_type in ("text/markdown", "text/x-rst", "text/plain")

    def capabilities(self) -> ParserCapabilities:
        return ParserCapabilities(
            supports_code_blocks=True,
            supports_tables=True,
            typical_latency_ms=1.0,
        )

    def parse(self, doc: Document) -> ParsedDocument:
        """Decode raw bytes to UTF-8 markdown string."""
        t0 = time.perf_counter()

        try:
            markdown_content = doc.raw_content.decode("utf-8")
        except UnicodeDecodeError:
            # Fallback: latin-1 never fails
            markdown_content = doc.raw_content.decode("latin-1")
            log.warning(
                f"UTF-8 decode failed for {doc.source_uri}, fell back to latin-1"
            )

        confidence = _estimate_confidence(markdown_content)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        return ParsedDocument(
            doc_id=doc.doc_id,
            source_uri=doc.source_uri,
            markdown_content=markdown_content,
            extraction_confidence=confidence,
            parser_version=f"{_PARSER_NAME}@{_PARSER_VERSION}",
            source_content_hash=doc.source_content_hash,
            source_last_modified=doc.source_last_modified,
            ingestion_timestamp=doc.ingestion_timestamp,
            parsing_duration_ms=elapsed_ms,
            metadata=dict(doc.metadata),
        )
