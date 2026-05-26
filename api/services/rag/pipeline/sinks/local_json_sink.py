"""
local_json_sink.py — Sink plugin: Write chunks to a local JSON file.

Compatible with the existing chunks.json format used by BaseIngestor.
Upserts on content_hash for idempotency.

Config keys:
    output_path: str — Path to output JSON file (default: "data/rag/trusted/chunks.json")
    indent:      int — JSON indentation (default: 2)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from api.services.rag.pipeline.interfaces import BaseSink
from api.services.rag.pipeline.models import Chunk

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[6]
_DEFAULT_OUTPUT = _REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"

_SINK_NAME = "LocalJsonSink"


class LocalJsonSink(BaseSink):
    """
    Sink: Append chunks to a local JSON file with upsert deduplication.

    Reads existing chunks, deduplicates on content_hash, appends new chunks,
    and writes the merged result. Safe for incremental runs.

    The output format matches the legacy BaseIngestor chunks.json schema
    for backward compatibility.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        output_str = cfg.get("output_path", str(_DEFAULT_OUTPUT))
        self._output_path = Path(output_str)
        if not self._output_path.is_absolute():
            self._output_path = _REPO_ROOT / self._output_path
        self._indent = int(cfg.get("indent", 2))

    @property
    def name(self) -> str:
        return _SINK_NAME

    def validate_chunk(self, chunk: Chunk) -> bool:
        """Validate chunk has required fields and meets quality threshold."""
        if not chunk.content or len(chunk.content.strip()) < 10:
            return False
        if chunk.token_count <= 0:
            return False
        if not chunk.content_hash:
            return False
        return True

    def write(self, chunks: list[Chunk]) -> int:
        """
        Write chunks to JSON file with deduplication on content_hash.

        Returns number of new chunks written (existing duplicates skipped).
        """
        if not chunks:
            return 0

        # Load existing chunks
        existing_by_hash: dict[str, dict] = {}
        if self._output_path.exists():
            try:
                with self._output_path.open("r", encoding="utf-8") as f:
                    existing_list = json.load(f)
                existing_by_hash = {
                    c.get("content_hash", c.get("id", "")): c
                    for c in existing_list
                    if isinstance(c, dict)
                }
            except (json.JSONDecodeError, OSError) as exc:
                log.warning(f"Could not read existing chunks from {self._output_path}: {exc}")

        written = 0
        for chunk in chunks:
            if not self.validate_chunk(chunk):
                log.debug(f"Chunk failed validation: {chunk.chunk_id}")
                continue

            if chunk.content_hash in existing_by_hash:
                log.debug(f"Skipping duplicate chunk: {chunk.content_hash[:16]}")
                continue

            # Convert to legacy-compatible dict
            chunk_dict = self._to_legacy_dict(chunk)
            existing_by_hash[chunk.content_hash] = chunk_dict
            written += 1

        # Write merged result
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("w", encoding="utf-8") as f:
            json.dump(list(existing_by_hash.values()), f, ensure_ascii=False, indent=self._indent, default=str)

        log.info(
            f"LocalJsonSink: wrote {written} new chunks to {self._output_path} "
            f"(total: {len(existing_by_hash)})"
        )
        return written

    def _to_legacy_dict(self, chunk: Chunk) -> dict:
        """
        Convert Pydantic Chunk to legacy BaseIngestor-compatible dict.

        Preserves the id/content/source_name/token_count fields that
        existing tooling (e.g. backfill scripts) depends on.
        """
        return {
            # Legacy fields (backward compat)
            "id": chunk.content_hash[:16],
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "content_hash": chunk.content_hash,
            "source_content_hash": chunk.source_content_hash,
            "version": chunk.dataset_version,
            "content": chunk.content,
            "raw_text": chunk.content,
            "cleaned_text": chunk.content,
            "source_name": chunk.source_name,
            "source_url": chunk.source_url,
            "chunk_order": chunk.chunk_order,
            "token_count": chunk.token_count,
            "source_type": "markdown",
            "tags": [],
            # New lineage fields
            "schema_version": chunk.schema_version,
            "parser_version": chunk.parser_version,
            "chunker_version": chunk.chunker_version,
            "middleware_versions": chunk.middleware_versions,
            "dataset_version": chunk.dataset_version,
            "dataset_namespace": chunk.dataset_namespace,
            "quality_score": chunk.quality_score,
            "duplicate_score": chunk.duplicate_score,
            "structural_confidence": chunk.structural_confidence,
            "ingestion_timestamp": chunk.ingestion_timestamp.isoformat() if chunk.ingestion_timestamp else None,
            "metadata": chunk.metadata or {},
        }
