"""
tests/unit/test_chunkers.py

Phase 2+5: Tests for chunking plugins and chunking invariants.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from api.services.rag.pipeline.models import ParsedDocument, Chunk


SAMPLE_MARKDOWN = """\
# System Design

## Load Balancing

Load balancing distributes incoming requests across multiple backend servers.
It improves reliability and reduces single points of failure in distributed systems.

### Types of Load Balancers

- **Layer 4 (Transport)**: Routes based on IP and TCP/UDP port
- **Layer 7 (Application)**: Routes based on HTTP headers, URLs, cookies

```
Client → Load Balancer → [Server A]
                       → [Server B]
                       → [Server C]
```

## Caching

Caching stores copies of frequently accessed data in fast storage layers.
Common caching strategies include LRU, LFU, and write-through patterns.

### Cache Invalidation

Cache invalidation is one of the hardest problems in computer science.
Strategies: TTL expiry, event-driven invalidation, cache-aside pattern.

## Database Design

### Replication

Database replication keeps copies of data across multiple nodes.
Primary-replica replication is the most common pattern for read scaling.

### Sharding

Horizontal partitioning of data across multiple database instances.
Shard key selection is critical to avoid hotspot queries.
"""


@pytest.fixture
def sample_parsed_doc() -> ParsedDocument:
    content_hash = hashlib.sha256(SAMPLE_MARKDOWN.encode()).hexdigest()
    doc_id = hashlib.sha256(b"test-doc").hexdigest()
    return ParsedDocument(
        doc_id=doc_id,
        source_uri="file:///test/system-design.md",
        markdown_content=SAMPLE_MARKDOWN,
        extraction_confidence=0.95,
        parser_version="MarkdownParser@1.0.0",
        source_content_hash=content_hash,
        ingestion_timestamp=datetime.utcnow(),
    )


class TestSemanticChunker:
    def test_produces_chunks(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 50})
        chunks = chunker.chunk(
            doc=sample_parsed_doc,
            dataset_version="test-v1",
            source_name="Test Dataset",
        )
        assert len(chunks) > 0

    def test_chunks_are_pydantic_chunk_objects(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 50})
        chunks = chunker.chunk(
            doc=sample_parsed_doc,
            dataset_version="test-v1",
            source_name="Test",
        )
        for chunk in chunks:
            assert isinstance(chunk, Chunk)

    def test_chunks_have_lineage_fields(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 50})
        chunks = chunker.chunk(
            doc=sample_parsed_doc,
            dataset_version="test-v1",
            source_name="Test",
            dataset_namespace="test-ns",
        )
        for chunk in chunks:
            assert chunk.parser_version == sample_parsed_doc.parser_version
            assert chunk.chunker_version.startswith("SemanticChunker@")
            assert chunk.dataset_version == "test-v1"
            assert chunk.dataset_namespace == "test-ns"

    def test_chunks_have_valid_quality_score(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 50})
        chunks = chunker.chunk(
            doc=sample_parsed_doc,
            dataset_version="test-v1",
            source_name="Test",
        )
        for chunk in chunks:
            assert 0.0 <= chunk.quality_score <= 1.0

    def test_chunk_ids_are_deterministic(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 50})
        chunks1 = chunker.chunk(
            doc=sample_parsed_doc, dataset_version="v1", source_name="Test"
        )
        chunks2 = chunker.chunk(
            doc=sample_parsed_doc, dataset_version="v1", source_name="Test"
        )
        ids1 = [c.chunk_id for c in chunks1]
        ids2 = [c.chunk_id for c in chunks2]
        assert ids1 == ids2

    def test_min_token_filter_applied(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        # Very high min_tokens should reject most chunks
        chunker = SemanticChunker(config={"max_tokens": 480, "min_tokens": 500})
        chunks = chunker.chunk(
            doc=sample_parsed_doc, dataset_version="v1", source_name="Test"
        )
        for chunk in chunks:
            assert chunk.token_count >= 500

    def test_chunks_not_mutated(self, sample_parsed_doc: ParsedDocument):
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        chunker = SemanticChunker()
        chunks = chunker.chunk(
            doc=sample_parsed_doc, dataset_version="v1", source_name="Test"
        )
        for chunk in chunks:
            with pytest.raises(Exception):
                chunk.content = "mutated"  # type: ignore[misc]
