"""Tests for Markdown chunking and IngestionWorker compatibility."""
from __future__ import annotations

from api.services.rag.chunker import ChunkData, chunk_markdown

SAMPLE_MARKDOWN = """\
# System Design

## Load Balancing

Load balancing distributes incoming requests across multiple backend servers.
It improves reliability and reduces single points of failure in distributed systems.

### Types of Load Balancers

- **Layer 4 (Transport)**: Routes based on IP and TCP/UDP port
- **Layer 7 (Application)**: Routes based on HTTP headers, URLs, cookies

```
Client -> Load Balancer -> [Server A]
                       -> [Server B]
                       -> [Server C]
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


def test_chunk_markdown_splits_and_tracks_metadata():
    chunks = chunk_markdown(SAMPLE_MARKDOWN, max_tokens=480, min_tokens=10)
    assert len(chunks) >= 2
    for idx, chunk in enumerate(chunks):
        assert isinstance(chunk, ChunkData)
        assert chunk.chunk_order == idx
        assert chunk.token_count >= 10
        assert chunk.content_hash


def test_min_token_filter_applied():
    chunks = chunk_markdown(SAMPLE_MARKDOWN, max_tokens=480, min_tokens=500)
    for chunk in chunks:
        assert chunk.token_count >= 500
