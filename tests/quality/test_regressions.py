"""Quality, determinism, and normalization regression tests."""
from __future__ import annotations

from api.services.rag.chunker import chunk_markdown, normalize_urls, strip_toc

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

## References

- https://redis.io/docs/manual/
- https://memcached.org/
"""


def test_chunk_count_in_expected_range():
    chunks = chunk_markdown(SAMPLE_MARKDOWN, max_tokens=480, min_tokens=10)
    assert 2 <= len(chunks) <= 20


def test_chunking_is_deterministic():
    c1 = chunk_markdown(SAMPLE_MARKDOWN, max_tokens=480, min_tokens=10)
    c2 = chunk_markdown(SAMPLE_MARKDOWN, max_tokens=480, min_tokens=10)
    assert [c.content_hash for c in c1] == [c.content_hash for c in c2]
    assert [c.chunk_order for c in c1] == list(range(len(c1)))


def test_strip_toc_is_idempotent():
    doc = "## Table of Contents\n- [Intro](#intro)\n\n## Intro\nBody text.\n"
    once = strip_toc(doc)
    twice = strip_toc(once)
    assert once == twice
    assert "Table of Contents" not in once
    assert "Body text." in once


def test_normalize_urls_resolves_relative_links():
    doc = "See [guide](docs/guide.md) and [ext](https://example.com)."
    out = normalize_urls(doc, "https://depthapi.dev/start/")
    assert out == "See [guide](https://depthapi.dev/start/docs/guide.md) and [ext](https://example.com)."
