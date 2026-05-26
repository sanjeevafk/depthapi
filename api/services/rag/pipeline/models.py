"""
models.py — Immutable Pydantic data contracts for the RAG ingestion pipeline.

Data flows through strict, versioned contracts:
    Document → ParsedDocument → Chunk → (embedding stage, separate)

All models are frozen (immutable). Middleware must return new instances via
    doc.model_copy(update={...})
instead of mutating fields.

Schema version: 1.0.0
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ─── Version constants ────────────────────────────────────────────────────────
SCHEMA_VERSION = "1.0.0"


# ─── Source fingerprint (incremental ingestion) ───────────────────────────────
class SourceFingerprint(BaseModel):
    """Tracks source state for incremental change detection."""

    source_uri: str
    last_fetch_timestamp: datetime
    etag: str | None = None  # HTTP ETag for web sources
    content_hash: str | None = None  # SHA-256 for filesystem sources
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ─── Raw document from source ─────────────────────────────────────────────────
class Document(BaseModel):
    """
    Raw document fetched from a source plugin.

    Immutable; the source layer produces Documents, the parser layer consumes them.
    """

    doc_id: str  # SHA-256 of source_uri for deterministic identity
    source_uri: str
    raw_content: bytes
    mime_type: str  # e.g. "text/markdown", "application/pdf"

    # Provenance
    source_content_hash: str  # SHA-256(raw_content) — change detection key
    source_last_modified: datetime | None = None
    ingestion_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}

    @classmethod
    def from_bytes(
        cls,
        source_uri: str,
        raw_content: bytes,
        mime_type: str,
        source_last_modified: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Document":
        """Factory: auto-compute doc_id and content hash."""
        source_content_hash = hashlib.sha256(raw_content).hexdigest()
        doc_id = hashlib.sha256(source_uri.encode()).hexdigest()
        return cls(
            doc_id=doc_id,
            source_uri=source_uri,
            raw_content=raw_content,
            mime_type=mime_type,
            source_content_hash=source_content_hash,
            source_last_modified=source_last_modified,
            metadata=metadata or {},
        )


# ─── Parsed & middleware-enriched document ────────────────────────────────────
class ParsedDocument(BaseModel):
    """
    Document after parsing and middleware processing.

    Produced by the parser layer; consumed by the chunker layer.
    Middleware returns a new instance (copy-on-update) for each transform.
    """

    doc_id: str
    source_uri: str
    markdown_content: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)

    # Versioning (required for lineage)
    schema_version: str = SCHEMA_VERSION
    parser_version: str  # e.g. "markdown-parser@1.0.0"
    middleware_versions: dict[str, str] = Field(default_factory=dict)  # {name: ver}

    # Lineage
    applied_middleware: list[str] = Field(default_factory=list)  # ordered names
    middleware_config_hash: str = "none"  # SHA-256 of all middleware configs
    parsing_duration_ms: float = 0.0

    # Provenance (carried from Document)
    source_content_hash: str
    source_last_modified: datetime | None = None
    ingestion_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}

    def with_middleware_applied(
        self,
        middleware_name: str,
        middleware_version: str,
        new_content: str,
        config: dict[str, Any] | None = None,
    ) -> "ParsedDocument":
        """
        Return a new ParsedDocument with the named middleware applied.
        Tracks lineage and recomputes config hash.
        """
        new_versions = dict(self.middleware_versions)
        new_versions[middleware_name] = middleware_version

        new_applied = list(self.applied_middleware) + [middleware_name]

        all_configs = json.dumps(
            {k: config or {} for k in new_applied}, sort_keys=True
        ).encode()
        config_hash = hashlib.sha256(all_configs).hexdigest()

        return self.model_copy(
            update={
                "markdown_content": new_content,
                "middleware_versions": new_versions,
                "applied_middleware": new_applied,
                "middleware_config_hash": config_hash,
            }
        )


# ─── Quality scoring inputs ───────────────────────────────────────────────────
class QualityScoreInputs(BaseModel):
    """
    Deterministic inputs for quality score calculation.

    All inputs explicitly captured so quality_score is reproducible:
    same QualityScoreInputs → identical quality_score.
    """

    extraction_confidence: float = Field(ge=0.0, le=1.0)
    markdown_cleanliness: float = Field(ge=0.0, le=1.0)
    header_continuity: float = Field(ge=0.0, le=1.0)
    ocr_corruption_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    code_block_preservation: float = Field(ge=0.0, le=1.0, default=1.0)
    token_validity: float = Field(ge=0.0, le=1.0, default=1.0)
    layout_retention: float = Field(ge=0.0, le=1.0, default=1.0)
    table_extraction_success: float = Field(ge=0.0, le=1.0, default=1.0)

    model_config = {"frozen": True}

    def compute_score(self) -> float:
        """
        Deterministic quality score.
        Same inputs → identical score across all runs.
        """
        weights = {
            "extraction_confidence": 0.25,
            "markdown_cleanliness": 0.15,
            "header_continuity": 0.10,
            "code_block_preservation": 0.15,
            "token_validity": 0.15,
            "layout_retention": 0.10,
            "table_extraction_success": 0.10,
        }
        raw_score = sum(
            getattr(self, key) * weight for key, weight in weights.items()
        ) / sum(weights.values())

        ocr_penalty = self.ocr_corruption_rate * 0.1
        return round(min(1.0, max(0.0, raw_score - ocr_penalty)), 4)


# ─── Validated chunk ──────────────────────────────────────────────────────────
class Chunk(BaseModel):
    """
    Final validated chunk ready for storage.

    Carries full lineage + quality metadata for auditability.
    The embedding field is always NULL at this stage — embedding is
    a separate, independent downstream stage.
    """

    chunk_id: str  # SHA-256(doc_id + chunk_order + content_hash)
    doc_id: str
    content: str
    token_count: int
    chunk_order: int

    # Versioning
    schema_version: str = SCHEMA_VERSION
    parser_version: str
    chunker_version: str  # e.g. "semantic-chunker@1.0.0"
    middleware_versions: dict[str, str] = Field(default_factory=dict)

    # Lineage
    source_name: str
    source_url: str | None = None
    dataset_version: str  # e.g. "system-design-primer-v1"
    dataset_namespace: str | None = None  # e.g. "ai_ref_knowledge"

    # Provenance
    source_content_hash: str
    content_hash: str  # SHA-256(content) for deduplication
    ingestion_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Quality (deterministic)
    quality_inputs: QualityScoreInputs | None = None
    quality_score: float = Field(ge=0.0, le=1.0, default=0.5)
    duplicate_score: float = Field(ge=0.0, le=1.0, default=0.0)
    structural_confidence: float = Field(ge=0.0, le=1.0, default=1.0)

    # Parser info
    extraction_method: str = "direct_parse"  # "direct_parse" | "ocr_fallback"
    is_fallback_result: bool = False
    parser_name: str = "unknown"

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"frozen": True}

    @classmethod
    def build_chunk_id(cls, doc_id: str, chunk_order: int, content_hash: str) -> str:
        """Deterministic chunk ID from its three identity fields."""
        key = f"{doc_id}:{chunk_order}:{content_hash}"
        return hashlib.sha256(key.encode()).hexdigest()

    @model_validator(mode="after")
    def compute_quality_score_from_inputs(self) -> "Chunk":
        """If quality_inputs provided, recompute quality_score for consistency."""
        if self.quality_inputs is not None:
            computed = self.quality_inputs.compute_score()
            # Pydantic frozen: bypass via __dict__ only on construction
            if abs(computed - self.quality_score) > 0.0001:
                object.__setattr__(self, "quality_score", computed)
        return self


# ─── Dead Letter Queue entry ──────────────────────────────────────────────────
class ErrorRecord(BaseModel):
    """
    DLQ entry. Immutable record of an ingestion failure.

    Written to data/dlq/<date>_errors.jsonl for inspection and replay.
    """

    error_id: str
    severity: str  # "INFO" | "WARN" | "ERROR" | "FATAL"
    classification: str  # e.g. "token_count_too_low", "extraction_failed"
    action: str  # e.g. "skip_chunk", "skip_document", "retry"
    retryable: bool
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    max_retries: int = 3
    retry_count: int = 0

    # Context
    source_uri: str
    doc_id: str | None = None
    error_message: str
    traceback: str | None = None
    raw_content_preview: str | None = None  # First 200 chars for diagnostics

    model_config = {"frozen": True}


# ─── Ingestion result summary ─────────────────────────────────────────────────
class IngestionResult(BaseModel):
    """Summary returned by the orchestrator after a full ingestion run."""

    dataset_name: str
    mode: str  # "full" | "incremental" | "resume"
    started_at: datetime
    completed_at: datetime | None = None

    documents_processed: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0
    chunks_skipped_duplicate: int = 0
    chunks_skipped_too_short: int = 0

    dlq_path: str | None = None
    error_rate: float = 0.0  # documents_failed / documents_processed

    model_config = {"frozen": True}
