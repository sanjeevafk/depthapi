# DepthAPI System Architecture

DepthAPI is an open cognitive synthesis engine designed for sub-millisecond document ingestion and neural hybrid retrieval.

## Ingestion Core

Document bytes are parsed into GitHub-Flavored Markdown and segmented into deterministic chunks.

```python
import depth_engine
res = depth_engine.parse_and_chunk(doc_id="1", raw_bytes=b"# Test", filename_or_ext="test.md")
```

## Hybrid Search

PostgreSQL full-text search is fused with pgvector dense embeddings via Reciprocal Rank Fusion ($k=60$).
