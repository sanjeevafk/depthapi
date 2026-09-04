"""
depth_engine_adapter.py — Python adapter for the compiled Rust depth_engine core.

Provides zero-copy / sub-millisecond parsing and chunking with seamless
fallback to pure Python when depth_engine is not compiled or installed.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from api.services.rag.pipeline.models import (
    SCHEMA_VERSION,
    Chunk,
    Document,
    ParsedDocument,
    QualityScoreInputs,
)

log = logging.getLogger(__name__)

try:
    import depth_engine

    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


def has_depth_engine() -> bool:
    """Return True if compiled depth_engine native library is loaded."""
    return _HAS_DEPTH_ENGINE


def get_engine_version() -> str | None:
    """Return depth_engine version string if loaded, else None."""
    if _HAS_DEPTH_ENGINE and depth_engine is not None:
        return depth_engine.engine_version()
    return None


def run_depth_engine_pipeline(
    raw_text: str | bytes,
    document_id: UUID | str,
    filename: str | None = None,
    source_url: str | None = None,
    collection_name: str | None = None,
    user_metadata: dict[str, Any] | None = None,
    max_tokens: int = 480,
    min_tokens: int = 1,
) -> tuple[Document, list[Chunk]]:
    """
    Run ingestion using the compiled Rust depth_engine core.

    Returns:
        tuple[Document, list[Chunk]] conforming to immutable Pydantic models.
    """
    if not _HAS_DEPTH_ENGINE or depth_engine is None:
        raise RuntimeError("depth_engine native extension is not available")

    raw_bytes = raw_text.encode("utf-8") if isinstance(raw_text, str) else raw_text
    doc_id_str = str(document_id)
    source_uri = source_url or filename or f"direct://upload/{doc_id_str}"
    meta = user_metadata or {}

    doc = Document.from_bytes(
        source_uri=source_uri,
        raw_content=raw_bytes,
        mime_type="application/octet-stream" if filename else "text/markdown",
        metadata=meta,
    )

    native_res = depth_engine.parse_and_chunk(
        doc_id=doc_id_str,
        raw_bytes=raw_bytes,
        filename_or_ext=filename,
        source_url=source_url,
        source_name=filename or "api_upload",
        dataset_version="api-v1",
        dataset_namespace=collection_name or "default",
        max_tokens=max_tokens,
        min_tokens=min_tokens,
    )

    p_doc_raw = native_res["parsed_doc"]
    chunks_raw = native_res["chunks"]

    _parsed_doc = ParsedDocument(
        doc_id=p_doc_raw["doc_id"],
        source_uri=p_doc_raw["source_uri"],
        markdown_content=p_doc_raw["markdown_content"],
        extraction_confidence=p_doc_raw["extraction_confidence"],
        schema_version=SCHEMA_VERSION,
        parser_version=p_doc_raw["parser_version"],
        middleware_versions={},
        applied_middleware=[],
        source_content_hash=p_doc_raw["source_content_hash"],
        metadata={
            "format": p_doc_raw.get("format", "unknown"),
            "warnings": p_doc_raw.get("warnings", []),
            **meta,
        },
    )

    chunks: list[Chunk] = []
    for c in chunks_raw:
        q_in = None
        if c.get("quality_inputs"):
            qi = c["quality_inputs"]
            q_in = QualityScoreInputs(
                extraction_confidence=qi.get("extraction_confidence", 1.0),
                markdown_cleanliness=qi.get("markdown_cleanliness", 1.0),
                header_continuity=qi.get("header_continuity", 0.9),
                ocr_corruption_rate=qi.get("ocr_corruption_rate", 0.0),
                code_block_preservation=qi.get("code_block_preservation", 1.0),
                token_validity=qi.get("token_validity", 1.0),
                layout_retention=qi.get("layout_retention", 1.0),
                table_extraction_success=qi.get("table_extraction_success", 1.0),
            )

        chunks.append(
            Chunk(
                chunk_id=c["chunk_id"],
                doc_id=c["doc_id"],
                content=c["content"],
                token_count=c["token_count"],
                chunk_order=c["chunk_order"],
                schema_version=c.get("schema_version", SCHEMA_VERSION),
                parser_version=c["parser_version"],
                chunker_version=c["chunker_version"],
                middleware_versions=c.get("middleware_versions", {}),
                source_name=c["source_name"],
                source_url=c.get("source_url"),
                dataset_version=c.get("dataset_version", "api-v1"),
                dataset_namespace=c.get("dataset_namespace"),
                source_content_hash=c["source_content_hash"],
                content_hash=c["content_hash"],
                quality_inputs=q_in,
                quality_score=c.get("quality_score", 0.5),
                duplicate_score=c.get("duplicate_score", 0.0),
                structural_confidence=c.get("structural_confidence", 1.0),
                extraction_method=c.get("extraction_method", "direct_parse"),
                is_fallback_result=c.get("is_fallback_result", False),
                parser_name=c.get("parser_name", "depth-engine"),
                metadata={
                    **(c.get("metadata") or {}),
                    "warnings": p_doc_raw.get("warnings", []),
                    **meta,
                },
            )
        )

    return doc, chunks
