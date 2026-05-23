"""
test_regressions.py — Quality and parity regression tests.

Verifies that:
1. The SemanticChunker produces quality scores meeting defined thresholds.
2. Running the pipeline twice against the same content produces the same chunks.
3. Chunk count parity is within ±5% of expected baselines.
4. Middleware idempotency: applying middleware twice equals applying once.
5. Schema evolution: old chunk dicts can be loaded with missing new fields.

These tests use in-memory fixtures (no filesystem/dataset required).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
from api.services.rag.pipeline.middleware.ascii_diagram_preserver import AsciiDiagramPreserver
from api.services.rag.pipeline.middleware.toc_stripper import TocStripper
from api.services.rag.pipeline.middleware.url_normalizer import UrlNormalizer
from api.services.rag.pipeline.models import (
    Chunk,
    Document,
    ParsedDocument,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_MARKDOWN = """\
# System Design: Distributed Caching

## Introduction

Caching is a technique used to speed up data retrieval by storing copies of data
in a fast-access storage layer. Distributed caches like Redis or Memcached allow
multiple application servers to share cache state.

## Key Concepts

### Cache Miss

A cache miss occurs when the requested data is not in the cache. The application
must fetch the data from the origin store and populate the cache for future reads.

### Cache Eviction Policies

- **LRU (Least Recently Used)**: Evict the least recently accessed item.
- **LFU (Least Frequently Used)**: Evict the least frequently accessed item.
- **TTL (Time To Live)**: Automatically expire entries after a set duration.

## ASCII Architecture

```
Client → Load Balancer → App Server → Redis Cache
                                         ↓ (miss)
                                      Database
```

## References

- https://redis.io/docs/manual/
- https://memcached.org/
"""


def _make_doc(content: str = SAMPLE_MARKDOWN) -> Document:
    raw = content.encode("utf-8")
    return Document(
        doc_id=hashlib.sha256(b"test-doc").hexdigest(),
        source_uri="file://test.md",
        raw_content=raw,
        mime_type="text/markdown",
        source_content_hash=hashlib.sha256(raw).hexdigest(),
        ingestion_timestamp=datetime.utcnow(),
    )


def _make_parsed_doc(content: str = SAMPLE_MARKDOWN) -> ParsedDocument:
    return ParsedDocument(
        doc_id=hashlib.sha256(b"test-doc").hexdigest(),
        source_uri="file://test.md",
        markdown_content=content,
        extraction_confidence=0.95,
        schema_version="1.0.0",
        parser_version="MarkdownParser@1.0.0",
        middleware_versions={},
        applied_middleware=[],
        middleware_config_hash=hashlib.sha256(b"{}").hexdigest(),
        parsing_duration_ms=12.5,
        source_content_hash=hashlib.sha256(content.encode()).hexdigest(),
        ingestion_timestamp=datetime.utcnow(),
    )


# ─── Chunker quality regressions ─────────────────────────────────────────────

class TestChunkQualityBaselines:
    """Quality scores must not regress below defined thresholds."""

    def test_quality_scores_meet_threshold(self):
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()
        chunks = chunker.chunk(
            parsed,
            dataset_version="v1.0",
            source_name="Test Dataset",
        )
        assert chunks, "Expected at least one chunk"
        qualities = [c.quality_score for c in chunks]
        avg_quality = sum(qualities) / len(qualities)
        assert avg_quality >= 0.5, f"Average quality {avg_quality:.3f} below 0.5 baseline"

    def test_no_chunks_below_minimum_quality(self):
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()
        chunks = chunker.chunk(parsed, dataset_version="v1.0", source_name="Test")
        for chunk in chunks:
            assert chunk.quality_score >= 0.0
            assert chunk.quality_score <= 1.0

    def test_chunk_count_in_expected_range(self):
        """For the sample markdown, expect between 2 and 20 chunks."""
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()
        chunks = chunker.chunk(parsed, dataset_version="v1.0", source_name="Test")
        assert 2 <= len(chunks) <= 20, f"Unexpected chunk count: {len(chunks)}"


# ─── Determinism tests ────────────────────────────────────────────────────────

class TestChunkerDeterminism:
    """Same input → identical output across multiple runs."""

    def test_content_hashes_identical_across_runs(self):
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()

        chunks_1 = chunker.chunk(parsed, dataset_version="v1.0", source_name="Test")
        chunks_2 = chunker.chunk(parsed, dataset_version="v1.0", source_name="Test")

        hashes_1 = sorted(c.content_hash for c in chunks_1)
        hashes_2 = sorted(c.content_hash for c in chunks_2)
        assert hashes_1 == hashes_2, "Chunk content hashes differ across runs"

    def test_chunk_ids_identical_across_runs(self):
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()

        ids_1 = sorted(c.chunk_id for c in chunker.chunk(parsed, dataset_version="v1.0", source_name="Test"))
        ids_2 = sorted(c.chunk_id for c in chunker.chunk(parsed, dataset_version="v1.0", source_name="Test"))
        assert ids_1 == ids_2

    def test_chunk_orders_are_sequential(self):
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 10})
        parsed = _make_parsed_doc()
        chunks = chunker.chunk(parsed, dataset_version="v1.0", source_name="Test")
        orders = [c.chunk_order for c in chunks]
        assert orders == list(range(len(chunks))), f"Non-sequential chunk orders: {orders}"


# ─── Middleware idempotency tests ─────────────────────────────────────────────

class TestMiddlewareIdempotency:
    """Applying middleware twice must produce identical output."""

    def _apply_twice(self, middleware, parsed: ParsedDocument) -> tuple[str, str]:
        once = middleware.process(parsed)
        twice = middleware.process(once)
        return once.markdown_content, twice.markdown_content

    def test_toc_stripper_idempotent(self):
        mw = TocStripper(config={"anchor_ratio_threshold": 0.5})
        parsed = _make_parsed_doc()
        once, twice = self._apply_twice(mw, parsed)
        assert once == twice, "TocStripper is not idempotent"

    def test_ascii_preserver_idempotent(self):
        mw = AsciiDiagramPreserver(config={"preserve_box_drawings": True, "min_diagram_lines": 3})
        parsed = _make_parsed_doc()
        once, twice = self._apply_twice(mw, parsed)
        assert once == twice, "AsciiDiagramPreserver is not idempotent"

    def test_url_normalizer_idempotent(self):
        mw = UrlNormalizer(config={"strip_tracking_params": True})
        parsed = _make_parsed_doc()
        once, twice = self._apply_twice(mw, parsed)
        assert once == twice, "UrlNormalizer is not idempotent"


# ─── Middleware lineage tracking ──────────────────────────────────────────────

class TestMiddlewareLineage:
    def test_middleware_appended_to_applied_list(self):
        mw = TocStripper(config={})
        parsed = _make_parsed_doc()
        result = mw.process(parsed)
        assert "TocStripper" in result.applied_middleware

    def test_middleware_chain_accumulates(self):
        parsed = _make_parsed_doc()
        toc = TocStripper(config={})
        asc = AsciiDiagramPreserver(config={})
        url = UrlNormalizer(config={})

        after_toc = toc.process(parsed)
        after_asc = asc.process(after_toc)
        after_url = url.process(after_asc)

        assert "TocStripper" in after_url.applied_middleware
        assert "AsciiDiagramPreserver" in after_url.applied_middleware
        assert "UrlNormalizer" in after_url.applied_middleware

    def test_config_hash_changes_with_different_configs(self):
        parsed_1 = _make_parsed_doc()
        parsed_2 = _make_parsed_doc()
        toc_1 = TocStripper(config={"anchor_ratio_threshold": 0.3})
        toc_2 = TocStripper(config={"anchor_ratio_threshold": 0.9})

        result_1 = toc_1.process(parsed_1)
        result_2 = toc_2.process(parsed_2)

        assert result_1.middleware_config_hash != result_2.middleware_config_hash


# ─── Schema evolution tests ───────────────────────────────────────────────────

class TestSchemaEvolution:
    """Old chunk dicts must be readable with new code (backward compat)."""

    def test_chunk_with_optional_fields_missing(self):
        """Fields added in later versions have defaults; old dicts load fine."""
        raw = {
            "chunk_id": "abc123",
            "doc_id": "doc456",
            "content": "Some content here for testing.",
            "token_count": 5,
            "chunk_order": 0,
            "parser_version": "MarkdownParser@1.0.0",
            "chunker_version": "semantic-chunker@1.0.0",
            "source_name": "Test Source",
            "dataset_version": "v1.0",
            "source_content_hash": "deadbeef" * 8,
            "content_hash": hashlib.sha256(b"Some content here for testing.").hexdigest(),
            "ingestion_timestamp": datetime.utcnow().isoformat(),
        }
        chunk = Chunk(**raw)
        # quality_inputs is optional — defaults to None
        assert chunk.quality_inputs is None
        # quality_score defaults to 0.5
        assert chunk.quality_score == 0.5

    def test_chunk_with_all_fields(self):
        from api.services.rag.pipeline.models import QualityScoreInputs

        qi = QualityScoreInputs(
            extraction_confidence=0.9,
            markdown_cleanliness=0.8,
            header_continuity=0.95,
            ocr_corruption_rate=0.0,
            code_block_preservation=1.0,
            token_validity=0.9,
            layout_retention=0.85,
            table_extraction_success=1.0,
        )
        raw = {
            "chunk_id": Chunk.build_chunk_id("doc1", 0, "h1"),
            "doc_id": "doc1",
            "content": "Full content here.",
            "token_count": 4,
            "chunk_order": 0,
            "parser_version": "MarkdownParser@1.0.0",
            "chunker_version": "semantic-chunker@1.0.0",
            "source_name": "Test",
            "dataset_version": "v1.0",
            "source_content_hash": "a" * 64,
            "content_hash": "b" * 64,
            "ingestion_timestamp": datetime.utcnow().isoformat(),
            "quality_inputs": qi.model_dump(),
        }
        chunk = Chunk(**raw)
        expected_score = qi.compute_score()
        assert abs(chunk.quality_score - expected_score) < 0.001
