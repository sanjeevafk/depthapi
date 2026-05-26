"""
tests/unit/test_models.py

Phase 0: Unit tests for Pydantic models and config schema.
All tests must be green before proceeding to Phase 1.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from api.services.rag.pipeline.models import (
    Chunk,
    Document,
    ErrorRecord,
    IngestionResult,
    ParsedDocument,
    QualityScoreInputs,
    SourceFingerprint,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_MARKDOWN = """\
# System Design Primer

## Scalability

Scalability is the ability of a system to handle growing load.

### Load Balancing

A load balancer distributes incoming requests across multiple servers.

```python
class LoadBalancer:
    def route(self, request):
        return self.servers[hash(request) % len(self.servers)]
```
"""


@pytest.fixture
def sample_document() -> Document:
    content = SAMPLE_MARKDOWN.encode("utf-8")
    return Document.from_bytes(
        source_uri="file:///datasets/system-design-primer/README.md",
        raw_content=content,
        mime_type="text/markdown",
    )


@pytest.fixture
def sample_parsed_doc(sample_document: Document) -> ParsedDocument:
    return ParsedDocument(
        doc_id=sample_document.doc_id,
        source_uri=sample_document.source_uri,
        markdown_content=SAMPLE_MARKDOWN,
        extraction_confidence=0.95,
        parser_version="markdown-parser@1.0.0",
        source_content_hash=sample_document.source_content_hash,
        ingestion_timestamp=datetime.now(UTC),
    )


@pytest.fixture
def sample_quality_inputs() -> QualityScoreInputs:
    return QualityScoreInputs(
        extraction_confidence=0.95,
        markdown_cleanliness=0.90,
        header_continuity=0.80,
        ocr_corruption_rate=0.0,
        code_block_preservation=1.0,
        token_validity=1.0,
        layout_retention=0.95,
        table_extraction_success=1.0,
    )


# ─── Document tests ───────────────────────────────────────────────────────────

class TestDocument:
    def test_from_bytes_computes_hashes(self):
        content = b"hello world"
        doc = Document.from_bytes(
            source_uri="file:///test.md",
            raw_content=content,
            mime_type="text/markdown",
        )
        assert doc.source_content_hash == hashlib.sha256(content).hexdigest()
        assert doc.doc_id == hashlib.sha256(b"file:///test.md").hexdigest()

    def test_document_is_immutable(self, sample_document: Document):
        with pytest.raises(Exception):
            sample_document.mime_type = "application/pdf"  # type: ignore[misc]

    def test_document_has_ingestion_timestamp(self, sample_document: Document):
        assert isinstance(sample_document.ingestion_timestamp, datetime)
        assert sample_document.ingestion_timestamp.tzinfo is UTC

    def test_two_docs_same_uri_same_hash(self):
        content = b"same content"
        doc1 = Document.from_bytes("file:///test.md", content, "text/markdown")
        doc2 = Document.from_bytes("file:///test.md", content, "text/markdown")
        assert doc1.doc_id == doc2.doc_id
        assert doc1.source_content_hash == doc2.source_content_hash

    def test_different_content_different_hash(self):
        doc1 = Document.from_bytes("file:///test.md", b"content A", "text/markdown")
        doc2 = Document.from_bytes("file:///test.md", b"content B", "text/markdown")
        assert doc1.source_content_hash != doc2.source_content_hash


# ─── ParsedDocument tests ─────────────────────────────────────────────────────

class TestParsedDocument:
    def test_parsed_doc_is_immutable(self, sample_parsed_doc: ParsedDocument):
        with pytest.raises(Exception):
            sample_parsed_doc.markdown_content = "mutated"  # type: ignore[misc]

    def test_with_middleware_applied_returns_new_instance(
        self, sample_parsed_doc: ParsedDocument
    ):
        cleaned = "# Cleaned Content\n\nNo TOC here."
        updated = sample_parsed_doc.with_middleware_applied(
            middleware_name="TocStripper",
            middleware_version="1.0.0",
            new_content=cleaned,
        )
        assert updated is not sample_parsed_doc
        assert updated.markdown_content == cleaned
        assert "TocStripper" in updated.applied_middleware
        assert "TocStripper" in updated.middleware_versions

    def test_middleware_lineage_accumulates(self, sample_parsed_doc: ParsedDocument):
        doc1 = sample_parsed_doc.with_middleware_applied("TocStripper", "1.0.0", "a")
        doc2 = doc1.with_middleware_applied("AsciiDiagramPreserver", "1.0.0", "b")
        assert doc2.applied_middleware == ["TocStripper", "AsciiDiagramPreserver"]
        assert "AsciiDiagramPreserver" in doc2.middleware_versions

    def test_config_hash_changes_with_different_middleware(
        self, sample_parsed_doc: ParsedDocument
    ):
        doc1 = sample_parsed_doc.with_middleware_applied("MW1", "1.0.0", "content")
        doc2 = sample_parsed_doc.with_middleware_applied("MW2", "1.0.0", "content")
        assert doc1.middleware_config_hash != doc2.middleware_config_hash

    def test_original_doc_unchanged_after_middleware(
        self, sample_parsed_doc: ParsedDocument
    ):
        original_content = sample_parsed_doc.markdown_content
        sample_parsed_doc.with_middleware_applied("TocStripper", "1.0.0", "new content")
        assert sample_parsed_doc.markdown_content == original_content


# ─── QualityScoreInputs tests ─────────────────────────────────────────────────

class TestQualityScoreInputs:
    def test_compute_score_deterministic(self, sample_quality_inputs: QualityScoreInputs):
        score1 = sample_quality_inputs.compute_score()
        score2 = sample_quality_inputs.compute_score()
        assert score1 == score2

    def test_perfect_inputs_near_1(self):
        inputs = QualityScoreInputs(
            extraction_confidence=1.0,
            markdown_cleanliness=1.0,
            header_continuity=1.0,
            ocr_corruption_rate=0.0,
            code_block_preservation=1.0,
            token_validity=1.0,
            layout_retention=1.0,
            table_extraction_success=1.0,
        )
        assert inputs.compute_score() == pytest.approx(1.0, abs=0.01)

    def test_zero_inputs_produces_zero_score(self):
        inputs = QualityScoreInputs(
            extraction_confidence=0.0,
            markdown_cleanliness=0.0,
            header_continuity=0.0,
            ocr_corruption_rate=0.0,
            code_block_preservation=0.0,
            token_validity=0.0,
            layout_retention=0.0,
            table_extraction_success=0.0,
        )
        assert inputs.compute_score() == pytest.approx(0.0, abs=0.01)

    def test_ocr_penalty_reduces_score(self):
        base_inputs = QualityScoreInputs(
            extraction_confidence=1.0,
            markdown_cleanliness=1.0,
            header_continuity=1.0,
            ocr_corruption_rate=0.0,
        )
        ocr_inputs = base_inputs.model_copy(update={"ocr_corruption_rate": 0.5})
        assert ocr_inputs.compute_score() < base_inputs.compute_score()

    def test_score_bounded_between_0_and_1(self):
        inputs = QualityScoreInputs(
            extraction_confidence=0.5,
            markdown_cleanliness=0.5,
            header_continuity=0.5,
            ocr_corruption_rate=0.8,  # high OCR corruption
        )
        score = inputs.compute_score()
        assert 0.0 <= score <= 1.0

    def test_quality_inputs_immutable(self, sample_quality_inputs: QualityScoreInputs):
        with pytest.raises(Exception):
            sample_quality_inputs.extraction_confidence = 0.1  # type: ignore[misc]


# ─── Chunk tests ──────────────────────────────────────────────────────────────

class TestChunk:
    def _make_chunk(self, content: str = "Sample chunk content here.") -> Chunk:
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        doc_id = hashlib.sha256(b"test-doc").hexdigest()
        chunk_id = Chunk.build_chunk_id(doc_id, 0, content_hash)
        return Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            content=content,
            token_count=len(content) // 4,
            chunk_order=0,
            parser_version="markdown-parser@1.0.0",
            chunker_version="semantic-chunker@1.0.0",
            source_name="Test Dataset",
            dataset_version="test-v1",
            source_content_hash=hashlib.sha256(b"source").hexdigest(),
            content_hash=content_hash,
        )

    def test_chunk_is_immutable(self):
        chunk = self._make_chunk()
        with pytest.raises(Exception):
            chunk.content = "mutated"  # type: ignore[misc]

    def test_chunk_id_deterministic(self):
        content = "test content"
        ch = hashlib.sha256(content.encode()).hexdigest()
        doc_id = hashlib.sha256(b"doc").hexdigest()
        id1 = Chunk.build_chunk_id(doc_id, 0, ch)
        id2 = Chunk.build_chunk_id(doc_id, 0, ch)
        assert id1 == id2

    def test_chunk_id_unique_per_order(self):
        content = "test content"
        ch = hashlib.sha256(content.encode()).hexdigest()
        doc_id = hashlib.sha256(b"doc").hexdigest()
        id1 = Chunk.build_chunk_id(doc_id, 0, ch)
        id2 = Chunk.build_chunk_id(doc_id, 1, ch)
        assert id1 != id2

    def test_quality_score_default(self):
        chunk = self._make_chunk()
        assert chunk.quality_score == 0.5  # default

    def test_quality_score_from_inputs(self):
        inputs = QualityScoreInputs(
            extraction_confidence=1.0,
            markdown_cleanliness=1.0,
            header_continuity=1.0,
        )
        content = "test chunk"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        doc_id = hashlib.sha256(b"doc").hexdigest()
        chunk = Chunk(
            chunk_id=Chunk.build_chunk_id(doc_id, 0, content_hash),
            doc_id=doc_id,
            content=content,
            token_count=2,
            chunk_order=0,
            parser_version="mp@1.0",
            chunker_version="sc@1.0",
            source_name="Test",
            dataset_version="v1",
            source_content_hash=content_hash,
            content_hash=content_hash,
            quality_inputs=inputs,
            quality_score=0.0,  # will be overridden by validator
        )
        assert chunk.quality_score == pytest.approx(inputs.compute_score(), abs=0.001)


# ─── ErrorRecord tests ────────────────────────────────────────────────────────

class TestErrorRecord:
    def test_error_record_immutable(self):
        err = ErrorRecord(
            error_id="err-001",
            severity="ERROR",
            classification="extraction_failed",
            action="skip_document",
            retryable=True,
            source_uri="file:///test.md",
            error_message="Parser failed",
        )
        with pytest.raises(Exception):
            err.severity = "WARN"  # type: ignore[misc]

    def test_error_record_defaults(self):
        err = ErrorRecord(
            error_id="err-001",
            severity="WARN",
            classification="token_count_too_low",
            action="skip_chunk",
            retryable=False,
            source_uri="file:///test.md",
            error_message="Too short",
        )
        assert err.retry_count == 0
        assert err.max_retries == 3
        assert err.attempted_at.tzinfo is UTC


# ─── SourceFingerprint tests ──────────────────────────────────────────────────

class TestSourceFingerprint:
    def test_fingerprint_immutable(self):
        fp = SourceFingerprint(
            source_uri="file:///test.md",
            last_fetch_timestamp=datetime.now(UTC),
            content_hash="abc123",
        )
        with pytest.raises(Exception):
            fp.content_hash = "xyz"  # type: ignore[misc]
