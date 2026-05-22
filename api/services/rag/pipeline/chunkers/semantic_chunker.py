"""
semantic_chunker.py — Chunker plugin: Hierarchical semantic chunking for Markdown.

Wraps the existing HierarchicalSemanticChunker from scripts/ingest_corpus/ and
adapts it to the BaseChunker interface. Produces Chunk objects with full lineage.

Config keys:
    max_tokens: int — Max tokens per chunk (default: 480)
    min_tokens: int — Min tokens per chunk (default: 50, smaller are skipped)
    version:    str — Chunker version tag (default: "v2")
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from api.services.rag.pipeline.interfaces import BaseChunker
from api.services.rag.pipeline.models import (
    Chunk,
    ParsedDocument,
    QualityScoreInputs,
    SCHEMA_VERSION,
)

log = logging.getLogger(__name__)

_CHUNKER_NAME = "SemanticChunker"
_CHUNKER_VERSION = "1.0.0"


class SemanticChunker(BaseChunker):
    """
    Chunker: Hierarchical semantic chunking for Markdown content.

    Delegates to the existing HierarchicalSemanticChunker for block-aware
    splitting, then converts legacy Chunk dataclasses to the new Pydantic
    Chunk model with full lineage and quality metadata.

    Semantic parity with the legacy chunker is enforced by Phase 5 tests.
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
        """
        Split a ParsedDocument into Chunk objects using HierarchicalSemanticChunker.

        Args:
            doc: ParsedDocument with markdown_content.
            dataset_version: e.g. "system-design-primer-v1"
            source_name: e.g. "System Design Primer"
            source_url: Original source URI.
            dataset_namespace: Optional grouping namespace.

        Returns:
            List of Chunk objects with full lineage metadata.
        """
        # Import existing chunker (lazy, to allow optional dependency)
        from scripts.ingest_corpus.semantic_chunker import HierarchicalSemanticChunker
        from scripts.ingest_corpus.base_ingestor import make_doc_id

        legacy_chunker = HierarchicalSemanticChunker(
            max_tokens=self._max_tokens,
            version=self._version,
            source_type="markdown",
        )

        doc_id = doc.doc_id
        legacy_chunks = legacy_chunker.chunk_document(
            text=doc.markdown_content,
            doc_id=doc_id,
            source_name=source_name,
            source_url=source_url,
            tags=[],
        )

        pipeline_chunks: list[Chunk] = []
        for legacy in legacy_chunks:
            # Skip chunks below minimum token threshold
            if legacy.token_count < self._min_tokens:
                log.debug(
                    f"Skipping chunk: token_count={legacy.token_count} < min={self._min_tokens}"
                )
                continue

            content_hash = hashlib.sha256(legacy.content.encode()).hexdigest()
            chunk_id = Chunk.build_chunk_id(doc_id, legacy.chunk_order, content_hash)

            # Build quality inputs from parser confidence
            quality_inputs = QualityScoreInputs(
                extraction_confidence=doc.extraction_confidence,
                markdown_cleanliness=min(1.0, len(legacy.content.strip()) / max(1, len(legacy.content))),
                header_continuity=0.9,  # Hierarchical chunker preserves headers
                code_block_preservation=1.0 if "```" in legacy.content else 0.9,
                token_validity=1.0 if self._min_tokens <= legacy.token_count <= self._max_tokens else 0.7,
            )

            pipeline_chunks.append(Chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                content=legacy.content,
                token_count=legacy.token_count,
                chunk_order=legacy.chunk_order,
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
                quality_inputs=quality_inputs,
                quality_score=quality_inputs.compute_score(),
                extraction_method="direct_parse",
                is_fallback_result=False,
                parser_name=doc.parser_version.split("@")[0] if "@" in doc.parser_version else doc.parser_version,
                metadata={
                    **(legacy.metadata or {}),
                    "applied_middleware": doc.applied_middleware,
                    "middleware_config_hash": doc.middleware_config_hash,
                },
            ))

        log.debug(
            f"SemanticChunker: {len(pipeline_chunks)} chunks from {doc.source_uri}"
        )
        return pipeline_chunks
