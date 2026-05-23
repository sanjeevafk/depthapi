from __future__ import annotations

from pathlib import Path

from .io_utils import write_markdown


def write_dataset_card(path: Path) -> None:
    text = """---
language:
- en
tags:
- rag
- retrieval
- technical-docs
- programming
- depthapi
size_categories:
- 10K<n<100K
---
# DepthAPI Technical Corpus

## Overview

The **DepthAPI Technical Corpus** is a curated, high-quality retrieval corpus designed for modern RAG (Retrieval-Augmented Generation) systems. It features clean, aggressively normalized technical documentation, code snippets, engineering post-mortems, and system design literature.

This dataset was explicitly built to serve as the local ground-truth for the [DepthAPI](https://github.com/sanjeevafk/depthapi) project. 

### About the DepthAPI Project
DepthAPI is an enterprise-grade, declarative RAG pipeline built with asynchronous concurrency and Supabase Vector embeddings. It aims to systematize RAG data ingestion by moving away from hardcoded scripts to a configuration-driven architecture, ensuring maximum throughput, observability, and resilience. 

Key features of DepthAPI include:
- **Plugin-based Architecture**: Easily extendable ingestion strategies via declarative configurations.
- **Async Concurrency**: High-throughput processing using bounded, multi-document orchestration.
- **Namespace Isolation**: Precise control over data organization within the vector store.
- **Idempotency**: Safe, hash-based upserts to avoid duplication and state corruption.

## Included Data Sources

The corpus aggregates multiple highly-valued technical domains, explicitly isolated into distinct namespaces:
1. **OPEA Documentation** (`OPEA Documentation`): 104 detailed technical documents covering the Open Platform for Enterprise AI ecosystem.
2. **30 Seconds of Code** (`code_snippets`): 2,784 programming snippets and algorithms chunked for precise code retrieval.
3. **System Design Primer** (`system_design`): Core principles of scalable system architecture and design.
4. **Engineering Post-Mortems** (`Technical Corpus - Postmortems & Archived`): Incident reports and technical retrospectives.
5. **DepthAPI Trusted Corpus**: Curated deep learning tutorials, FastAPI templates, and SQL guides.

## Ingestion Pipeline Details

The corpus is powered by the **DepthAPI Declarative Ingestion Pipeline**, moving from manual scripts to a `config.yaml` driven approach. 

During the ingestion of this corpus, we heavily utilized the following open-source extraction libraries:
- [**Scrapling**](https://github.com/D4Vinci/Scrapling): Used for live technical documentation crawling and structured HTML extraction from documentation websites.
- [**opendataloader-pdf**](https://github.com/opendataloader-project/opendataloader-pdf): Used for PDF extraction when ingesting book-like and document-style technical sources into normalized markdown/text blocks.

Key features of the pipeline used to create this dataset:
- **Declarative Sources:** Ingestion configs declare `LocalDirSource` with strict glob matching.
- **Concurrent Processing:** Processed using `ConcurrentPipelineOrchestrator` via asynchronous worker pools.
- **Semantic Chunking:** Processed via `SemanticChunker` (v2) with strict token boundaries and overlap for contextual continuity.
- **Middleware Extraction:** Handled through custom middleware like `CodeSnippetExtractor` to isolate raw code blocks and attach precise metadata.
- **Idempotent Upserts:** Deduplicated using SHA-256 content hashing natively within Supabase pgvector tables (`knowledge_chunks`).

## Data Schema

Each exported Parquet shard normalizes the Supabase relational schema into a flat, Hugging Face compatible format:

- `chunk_id`: Unique identifier for the chunk.
- `source`: The raw file or origin name.
- `source_url`: A unique URI or file path pointing back to the specific resource.
- `upstream_license`: Licensing information associated with the document.
- `document_id`: Relational ID linking chunks back to their parent document.
- `chunk_index`: The sequential order of the chunk in the original text to maintain narrative context.
- `content`: The cleaned, ready-to-embed text block.
- `content_hash`: Deterministic SHA-256 hash used for idempotent ingestion.
- `collection_name`: The logical namespace under which the chunk was isolated (e.g., `code_snippets`).

## Usage

```python
from datasets import load_dataset

dataset = load_dataset("sanjeevafk/depthapi_technical_corpus", split="train", streaming=True)
for row in dataset.take(3):
    print(row["collection_name"], "-", row["source_url"])
```

## Licensing & Governance

This is a **mixed-license dataset**. Downstream users must dynamically inspect the `upstream_license` field per chunk and consult the accompanying `SOURCES_MANIFEST.yaml` before redistribution or commercial application.
"""
    write_markdown(path, text)
