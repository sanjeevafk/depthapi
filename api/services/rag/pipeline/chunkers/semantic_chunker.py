"""
semantic_chunker.py — Chunker plugin: Hierarchical semantic chunking for Markdown.

Uses native depth_engine when available, with a clean lightweight Python fallback.
Produces Chunk objects with full lineage and quality metadata.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from api.services.rag.pipeline.interfaces import BaseChunker
from api.services.rag.pipeline.models import (
    SCHEMA_VERSION,
    Chunk,
    ParsedDocument,
    QualityScoreInputs,
)

log = logging.getLogger(__name__)

_CHUNKER_NAME = "SemanticChunker"
_CHUNKER_VERSION = "1.0.0"

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


def make_doc_id(source_name: str, source_url: str | None) -> str:
    """Deterministic document-level ID based on name + URL."""
    key = f"{source_name}::{source_url or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


class SemanticChunker(BaseChunker):
    """
    Chunker: Hierarchical semantic chunking for Markdown content.
    Uses native compiled depth_engine core with pure-Python fallback.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._max_tokens = int(self._config.get("max_tokens", 480))
        self._min_tokens = int(self._config.get("min_tokens", 50))
        self._version = str(self._config.get("version", "v2"))

    @property
    def name(self) -> str:
        return _CHUNKER_NAME

    @property
    def version(self) -> str:
        return _CHUNKER_VERSION

    def chunk(
        self,
        doc: ParsedDocument,
        dataset_version: str,
        source_name: str,
        source_url: str | None = None,
        dataset_namespace: str | None = None,
    ) -> list[Chunk]:
        """Split a ParsedDocument into Chunk objects."""
        doc_id = doc.doc_id

        if _HAS_DEPTH_ENGINE:
            try:
                native_chunks = depth_engine.chunk_markdown(
                    markdown=doc.markdown_content,
                    doc_id=doc_id,
                    source_name=source_name,
                    source_url=source_url,
                    dataset_version=dataset_version,
                    dataset_namespace=dataset_namespace,
                    source_content_hash=doc.source_content_hash,
                    parser_version=doc.parser_version,
                    parser_name=doc.parser_version.split("@")[0] if "@" in doc.parser_version else doc.parser_version,
                    extraction_confidence=doc.extraction_confidence,
                    max_tokens=self._max_tokens,
                    min_tokens=self._min_tokens,
                )
                pipeline_chunks: list[Chunk] = []
                for c in native_chunks:
                    if c["token_count"] < self._min_tokens:
                        continue
                    q_inputs = QualityScoreInputs(
                        extraction_confidence=c.get("quality_inputs", {}).get("extraction_confidence", doc.extraction_confidence),
                        markdown_cleanliness=c.get("quality_inputs", {}).get("markdown_cleanliness", 1.0),
                        header_continuity=c.get("quality_inputs", {}).get("header_continuity", 0.9),
                        code_block_preservation=c.get("quality_inputs", {}).get("code_block_preservation", 1.0 if "```" in c["content"] else 0.9),
                        token_validity=c.get("quality_inputs", {}).get("token_validity", 1.0),
                    )
                    content_hash = c.get("content_hash") or hashlib.sha256(c["content"].encode()).hexdigest()
                    pipeline_chunks.append(Chunk(
                        chunk_id=c.get("chunk_id") or Chunk.build_chunk_id(doc_id, c["chunk_order"], content_hash),
                        doc_id=doc_id,
                        content=c["content"],
                        token_count=c["token_count"],
                        chunk_order=c["chunk_order"],
                        schema_version=SCHEMA_VERSION,
                        parser_version=doc.parser_version,
                        chunker_version=f"{_CHUNKER_NAME}@{_CHUNKER_VERSION}",
                        middleware_versions=dict(doc.middleware_versions),
                        source_name=source_name,
                        source_url=source_url,
                        dataset_version=dataset_version,
                        dataset_namespace=dataset_namespace,
                        source_content_hash=doc.source_content_hash,
                        content_hash=content_hash,
                        ingestion_timestamp=doc.ingestion_timestamp,
                        quality_inputs=q_inputs,
                        quality_score=float(c.get("quality_score") or q_inputs.compute_score()),
                        extraction_method="direct_parse",
                        is_fallback_result=False,
                        parser_name=doc.parser_version.split("@")[0] if "@" in doc.parser_version else doc.parser_version,
                        metadata={
                            **(c.get("metadata") or {}),
                            "applied_middleware": doc.applied_middleware,
                            "middleware_config_hash": doc.middleware_config_hash,
                        },
                    ))
                return pipeline_chunks
            except Exception as exc:
                log.warning(f"depth_engine chunking failed, falling back to Python: {exc}")

        # Fallback pure-Python chunking
        lines = doc.markdown_content.splitlines(keepends=True)
        raw_sections: list[str] = []
        cur: list[str] = []
        cur_tokens = 0
        for line in lines:
            if line.startswith("# ") and cur:
                raw_sections.append("".join(cur).strip())
                cur = [line]
                cur_tokens = len(line) // 4
            else:
                cur.append(line)
                cur_tokens += len(line) // 4
                if cur_tokens >= self._max_tokens:
                    raw_sections.append("".join(cur).strip())
                    cur = []
                    cur_tokens = 0
        if cur:
            raw_sections.append("".join(cur).strip())

        fallback_chunks: list[Chunk] = []
        for i, text in enumerate(raw_sections):
            tokens = max(1, len(text) // 4)
            if tokens < self._min_tokens and len(raw_sections) > 1:
                continue
            chash = hashlib.sha256(text.encode()).hexdigest()
            cid = Chunk.build_chunk_id(doc_id, i, chash)
            qinputs = QualityScoreInputs(
                extraction_confidence=doc.extraction_confidence,
                markdown_cleanliness=1.0,
                header_continuity=0.9,
                code_block_preservation=1.0 if "```" in text else 0.9,
                token_validity=1.0,
            )
            fallback_chunks.append(Chunk(
                chunk_id=cid,
                doc_id=doc_id,
                content=text,
                token_count=tokens,
                chunk_order=i,
                schema_version=SCHEMA_VERSION,
                parser_version=doc.parser_version,
                chunker_version=f"{_CHUNKER_NAME}@{_CHUNKER_VERSION}",
                middleware_versions=dict(doc.middleware_versions),
                source_name=source_name,
                source_url=source_url,
                dataset_version=dataset_version,
                dataset_namespace=dataset_namespace,
                source_content_hash=doc.source_content_hash,
                content_hash=chash,
                ingestion_timestamp=doc.ingestion_timestamp,
                quality_inputs=qinputs,
                quality_score=qinputs.compute_score(),
                extraction_method="direct_parse",
                is_fallback_result=True,
                parser_name=doc.parser_version.split("@")[0] if "@" in doc.parser_version else doc.parser_version,
                metadata={
                    "applied_middleware": doc.applied_middleware,
                    "middleware_config_hash": doc.middleware_config_hash,
                },
            ))

        return fallback_chunks
