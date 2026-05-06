"""
semantic_chunker.py — Hierarchical, block-aware chunking for technical literature.

Architecture
------------
1. StructuralBlockClassifier  breaks markdown into typed MarkdownBlock objects.
2. HierarchicalSemanticChunker walks the block stream and groups them into Chunk
   objects, respecting:
   - Heading hierarchy → updates breadcrumb context
   - Atomic blocks (code, table, exercise) → never split mid-block
   - AST-aware code splitting (ast_splitter) for oversized code blocks
   - Token budget (max_tokens) → flush current group when full
   - [continued] markers for oversized code that must be split

Public interface
----------------
    chunker = HierarchicalSemanticChunker(max_tokens=512, version="v2")
    chunks  = chunker.chunk_document(
        text        = md_text,
        doc_id      = "...",
        source_name = "Python Notes For Professionals",
        source_url  = "https://goalkicker.com/...",
        tags        = ["python", "P0"],
        breadcrumbs = ["Python Notes For Professionals"],   # book title as root
    )
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict
from typing import Any

from scripts.ingest_corpus.ast_splitter import split_fenced_block
from scripts.ingest_corpus.block_classifier import MarkdownBlock, StructuralBlockClassifier
from scripts.ingest_corpus.base_ingestor import (
    Chunk,
    clean_text,
    content_hash as make_content_hash,
    make_doc_id,
    rough_token_count,
)

log = logging.getLogger("ingest")

# Characters per "token" estimate (GPT-style, ~4 chars/tok)
_CHARS_PER_TOKEN = 4
# Code blocks longer than this (in tokens) will be AST-split
_CODE_SPLIT_THRESHOLD_TOKENS = 120   # ≈ 480 chars ≈ ~30 lines


class HierarchicalSemanticChunker:
    """
    Groups MarkdownBlocks into Chunk objects with:
    - Hierarchical breadcrumb context (last 2 levels, lightweight)
    - Atomic preservation of code / table / exercise blocks
    - AST-aware splitting for oversized code blocks
    - Deterministic chunk IDs for incremental indexing
    """

    def __init__(
        self,
        max_tokens: int = 512,
        version: str = "v2",
        source_type: str = "pdf",
    ):
        self.max_tokens  = max_tokens
        self.version     = version
        self.source_type = source_type
        self._classifier = StructuralBlockClassifier()

    # ── Public entry point ────────────────────────────────────────────────────
    def chunk_document(
        self,
        text: str,
        doc_id: str,
        source_name: str,
        source_url: str | None = None,
        tags: list[str] | None = None,
        breadcrumbs: list[str] | None = None,
    ) -> list[Chunk]:
        """
        Parse `text` into Chunk objects.

        Args:
            text:         Full markdown content of one document.
            doc_id:       Stable document identifier (e.g. sha256[:24] of path).
            source_name:  Human-readable name (book title, URL, etc.).
            source_url:   Canonical source URL for metadata.
            tags:         e.g. ["python", "P0"].
            breadcrumbs:  Initial hierarchy context (usually [book_title]).

        Returns:
            Ordered list of Chunk objects ready for embedding / storage.
        """
        raw_blocks = self._classifier.classify(text)
        # Expand oversized code blocks with AST-aware splitting
        blocks = self._expand_code_blocks(raw_blocks)

        chunks: list[Chunk] = []
        hierarchy: list[str] = list(breadcrumbs or [])
        pending: list[MarkdownBlock] = []  # blocks accumulating into next chunk
        pending_tokens = 0
        chunk_order = 0

        def flush(h: list[str]) -> None:
            nonlocal chunk_order, pending, pending_tokens
            if not pending:
                return
            c = self._build_chunk(
                blocks      = pending,
                doc_id      = doc_id,
                source_name = source_name,
                source_url  = source_url,
                tags        = tags or [],
                hierarchy   = h,
                order       = chunk_order,
            )
            if c is not None:
                chunks.append(c)
                chunk_order += 1
            pending = []
            pending_tokens = 0

        for block in blocks:
            if block.type == "heading":
                # Headings close the current accumulation window
                flush(hierarchy)
                # Update hierarchy
                level = block.metadata.get("level", 2)
                title = block.metadata.get("title", "")
                # Trim to parent level
                hierarchy = hierarchy[: level - 1]
                hierarchy.append(title)
                # Include the heading itself in the next chunk as a title line
                pending.append(block)
                pending_tokens += rough_token_count(block.content)
                continue

            block_tokens = rough_token_count(block.content)

            # An atomic block that alone exceeds budget → own chunk
            if block.is_atomic and block_tokens > self.max_tokens:
                flush(hierarchy)
                pending = [block]
                pending_tokens = block_tokens
                flush(hierarchy)
                continue

            # Normal budget check
            if pending_tokens + block_tokens > self.max_tokens and pending:
                # Don't split in the middle of an atomic block — append it anyway
                # if there's nothing else pending, otherwise flush first.
                if block.is_atomic:
                    flush(hierarchy)
                else:
                    flush(hierarchy)

            pending.append(block)
            pending_tokens += block_tokens

        flush(hierarchy)
        return chunks

    # ── Code block expansion ─────────────────────────────────────────────────
    def _expand_code_blocks(
        self, blocks: list[MarkdownBlock]
    ) -> list[MarkdownBlock]:
        """
        Replace oversized code blocks with multiple smaller MarkdownBlock
        objects (each still typed "code") using the AST splitter.
        """
        expanded: list[MarkdownBlock] = []
        for block in blocks:
            if block.type != "code":
                expanded.append(block)
                continue
            tok_count = rough_token_count(block.content)
            if tok_count <= _CODE_SPLIT_THRESHOLD_TOKENS:
                expanded.append(block)
                continue
            lang = block.metadata.get("language", "")
            parts = split_fenced_block(block.content, max_lines=80)
            if len(parts) == 1:
                expanded.append(block)
                continue
            for part in parts:
                expanded.append(MarkdownBlock(
                    type="code",
                    content=part,
                    metadata={"language": lang, "split": True},
                ))
        return expanded

    # ── Chunk construction ────────────────────────────────────────────────────
    def _build_chunk(
        self,
        blocks: list[MarkdownBlock],
        doc_id: str,
        source_name: str,
        source_url: str | None,
        tags: list[str],
        hierarchy: list[str],
        order: int,
    ) -> Chunk | None:
        raw_text = "\n\n".join(b.content for b in blocks).strip()
        if not raw_text or len(raw_text) < 50:
            return None

        # Lightweight breadcrumb: last 2 hierarchy levels only
        # Full canonical path goes into metadata for hybrid filtering
        breadcrumb_parts = [p for p in hierarchy[-2:] if p]
        if breadcrumb_parts:
            breadcrumb_prefix = "[" + " > ".join(breadcrumb_parts) + "]\n\n"
        else:
            breadcrumb_prefix = ""

        cleaned_text = clean_text(breadcrumb_prefix + raw_text)
        if not cleaned_text or len(cleaned_text) < 50:
            return None

        chash = make_content_hash(cleaned_text)
        cid   = chash[:16]            # backward-compat short ID

        return Chunk(
            id           = cid,
            doc_id       = doc_id,
            chunk_id     = f"{doc_id}#c{order:04d}",
            content_hash = chash,
            version      = self.version,
            content      = cleaned_text,
            raw_text     = raw_text,
            cleaned_text = cleaned_text,
            source_name  = source_name,
            source_url   = source_url,
            chunk_order  = order,
            token_count  = rough_token_count(cleaned_text),
            source_type  = self.source_type,
            tags         = tags,
            metadata     = {
                "doc_id"      : doc_id,
                "hierarchy"   : hierarchy,          # full canonical path
                "block_types" : [b.type for b in blocks],
                "version"     : self.version,
            },
        )
