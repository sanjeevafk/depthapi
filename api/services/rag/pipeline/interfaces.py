"""
interfaces.py — Abstract base classes for all pipeline plugins.

Every stage of the ingestion pipeline must implement one of these interfaces.
Plugins are discovered via the registry and instantiated by the orchestrator.

Interface hierarchy:
    BaseSource      → Fetches raw Documents from external sources
    BaseParser      → Converts raw bytes into ParsedDocument (Markdown)
    BaseMiddleware  → Applies deterministic transforms to ParsedDocument
    BaseChunker     → Splits ParsedDocument into List[Chunk]
    BaseSink        → Persists List[Chunk] to storage

Design invariants (all enforced by these interfaces):
    - Stateless except for config; no shared mutable state between calls
    - Deterministic: same input + same version → same output
    - Immutable: never mutate input models; return new instances
    - Typed: all parameters and return values are fully type-hinted
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from api.services.rag.pipeline.models import (
    Chunk,
    Document,
    ParsedDocument,
    SourceFingerprint,
)

# ─── Source ───────────────────────────────────────────────────────────────────

class BaseSource(ABC):
    """
    Fetch raw documents from an external source with incremental support.

    Sources emit (Document, SourceFingerprint) tuples. The fingerprint
    tracks the state of each document so the orchestrator can detect changes
    on subsequent runs (incremental mode).
    """

    @abstractmethod
    def fetch(
        self,
        since: dict[str, SourceFingerprint] | None = None,
    ) -> Iterator[tuple[Document, SourceFingerprint]]:
        """
        Yield (Document, Fingerprint) for each discovered document.

        Args:
            since: Map of {source_uri → SourceFingerprint} from last run.
                   If provided, skip documents whose fingerprint is unchanged.
                   Pass None to fetch all documents (full mode).

        Yields:
            Tuple of (Document, SourceFingerprint) for each new/changed doc.
        """

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> bool:
        """
        Validate source-specific configuration.

        Returns True if config is valid; raises ValueError with details if not.
        """

    def supports_incremental(self) -> bool:
        """
        Return True if this source supports change detection.

        Sources that cannot detect changes (e.g. streaming) return False.
        Override in subclasses that do not support incremental fetching.
        """
        return True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique source type identifier, e.g. 'LocalDirSource'."""


# ─── Parser ───────────────────────────────────────────────────────────────────

class ParserCapabilities:
    """Metadata enabling smart parser selection and fallback routing."""

    def __init__(
        self,
        supports_tables: bool = False,
        supports_images: bool = False,
        supports_multicolumn: bool = False,
        supports_code_blocks: bool = False,
        supports_ocr: bool = False,
        supports_math: bool = False,
        max_page_count: int | None = None,
        typical_latency_ms: float | None = None,
    ):
        self.supports_tables = supports_tables
        self.supports_images = supports_images
        self.supports_multicolumn = supports_multicolumn
        self.supports_code_blocks = supports_code_blocks
        self.supports_ocr = supports_ocr
        self.supports_math = supports_math
        self.max_page_count = max_page_count
        self.typical_latency_ms = typical_latency_ms


class BaseParser(ABC):
    """
    Convert raw document bytes into standard ParsedDocument (Markdown content).

    Parsers are stateless. The same Document always produces the same output
    for a given parser version.
    """

    @abstractmethod
    def parse(self, doc: Document) -> ParsedDocument:
        """
        Parse raw document bytes into a ParsedDocument.

        Args:
            doc: Immutable Document with raw_content bytes.

        Returns:
            ParsedDocument with markdown_content and extraction_confidence.
        """

    @abstractmethod
    def supports_mime_type(self, mime_type: str) -> bool:
        """Return True if this parser can handle the given MIME type."""

    @abstractmethod
    def capabilities(self) -> ParserCapabilities:
        """Return capability metadata for smart parser selection."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique parser identifier, e.g. 'MarkdownParser'."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version, e.g. '1.0.0'."""


# ─── Middleware ───────────────────────────────────────────────────────────────

class BaseMiddleware(ABC):
    """
    Apply a deterministic, idempotent transformation to a ParsedDocument.

    Contract:
        - Deterministic: same input + same version → same output
        - Idempotent: applying twice ≈ applying once
        - Immutable: return a NEW ParsedDocument instance (never mutate)
        - Composable: middleware is applied in sequence by the orchestrator

    Use ParsedDocument.with_middleware_applied() to produce the new instance
    with lineage tracking.
    """

    @abstractmethod
    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """
        Transform a ParsedDocument.

        Must return a NEW instance. Use:
            return doc.with_middleware_applied(
                middleware_name=self.name,
                middleware_version=self.version,
                new_content=transformed_content,
            )

        Args:
            doc: Input ParsedDocument (immutable; do not mutate).

        Returns:
            New ParsedDocument with transformation applied and lineage updated.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique middleware identifier for lineage tracking, e.g. 'TocStripper'."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version of this middleware, e.g. '1.0.0'."""


# ─── Chunker ──────────────────────────────────────────────────────────────────

class BaseChunker(ABC):
    """
    Split a ParsedDocument into a list of Chunk objects.

    Chunkers must produce deterministic output for the same input and version.
    Token counting should use the same tokenizer consistently across runs.
    """

    @abstractmethod
    def chunk(
        self,
        doc: ParsedDocument,
        dataset_version: str,
        source_name: str,
        source_url: str | None = None,
        dataset_namespace: str | None = None,
    ) -> list[Chunk]:
        """
        Split a ParsedDocument into Chunk objects.

        Args:
            doc: Fully parsed and middleware-processed document.
            dataset_version: Version tag for lineage, e.g. "system-design-primer-v1".
            source_name: Human-readable name, e.g. "System Design Primer".
            source_url: Optional canonical URL for the source.
            dataset_namespace: Optional namespace grouping, e.g. "ai_ref_knowledge".

        Returns:
            Ordered list of Chunk objects. Empty list if no valid chunks.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique chunker identifier, e.g. 'SemanticChunker'."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version, e.g. '1.0.0'."""


# ─── Sink ─────────────────────────────────────────────────────────────────────

class BaseSink(ABC):
    """
    Persist validated chunks to storage.

    Sinks must be idempotent: writing the same chunk twice should not create
    duplicates. Implement upsert semantics on (doc_id, chunk_order, content_hash).
    """

    @abstractmethod
    def write(self, chunks: list[Chunk]) -> int:
        """
        Write chunks to storage.

        Must be idempotent (safe to call with duplicates).

        Args:
            chunks: List of validated Chunk objects.

        Returns:
            Number of chunks actually written (new/updated, not skipped).
        """

    @abstractmethod
    def validate_chunk(self, chunk: Chunk) -> bool:
        """
        Validate a chunk before writing.

        Args:
            chunk: Chunk to validate.

        Returns:
            True if valid; False if chunk should be skipped.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique sink identifier, e.g. 'LocalJsonSink'."""
