"""Local ingestion compatibility helpers.

The database-backed ingestion worker was removed during the local PostgreSQL
refactor.  This small adapter preserves the chunking helper used by offline
callers while delegating to the canonical hierarchical chunker.
"""

from __future__ import annotations

from typing import Any

from api.services.rag.pipeline.chunkers.legacy.semantic_chunker import HierarchicalSemanticChunker



class IngestionWorker:
    """Compatibility facade for local text chunking."""

    def __init__(self, worker_id: str = "default-worker") -> None:
        self.worker_id = worker_id
        self._chunker = HierarchicalSemanticChunker(
            max_tokens=512,
            version="v2",
            source_type="markdown",
        )

    def chunk_text_with_metadata(
        self,
        text: str,
        *,
        doc_id: str,
        source_name: str,
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Chunk Markdown and return the legacy dictionary representation."""
        chunks = self._chunker.chunk_document(
            text=text,
            doc_id=doc_id,
            source_name=source_name,
            source_url=source_url,
        )
        return [
            {
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "chunk_order": chunk.chunk_order,
                "source_name": chunk.source_name,
                "source_url": chunk.source_url,
                "section_title": (chunk.metadata or {}).get("hierarchy", [""])[-1],
                "metadata": chunk.metadata or {},
            }
            for chunk in chunks
        ]
