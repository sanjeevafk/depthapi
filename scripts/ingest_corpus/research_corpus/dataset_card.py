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
size_categories:
- 100K<n<1M
---

# depthapi_technical_corpus

## Summary

`depthapi_technical_corpus` is a mixed-license retrieval corpus built from technical documentation, books, engineering writeups, and repository-derived reference material. It is intended for open-source RAG systems, embedding benchmarks, reranker evaluation, and enterprise retrieval experiments.

## Ingestion Process

The corpus was assembled from multiple upstream source types and normalized through a reproducible local pipeline backed by Supabase.

- `Scrapling` was used for live technical documentation crawling and structured HTML extraction from documentation websites.
- [`D4Vinci/Scrapling`](https://github.com/D4Vinci/Scrapling) was used for live technical documentation crawling and structured HTML extraction from documentation websites.
- [`opendataloader-project/opendataloader-pdf`](https://github.com/opendataloader-project/opendataloader-pdf) was used for PDF extraction when ingesting book-like and document-style technical sources into normalized markdown/text blocks.
- Source material was then normalized, rechunked deterministically, deduplicated, validated, and exported to parquet for Hugging Face datasets compatibility.

## Schema

Each chunk includes:

- `chunk_id`
- `source`
- `source_url`
- `upstream_license`
- `document_id`
- `chunk_index`
- `retrieved_at`
- `chunker_version`
- `content_hash`
- `content`

## Usage

```python
from datasets import load_dataset

dataset = load_dataset("sanjeevafk/depthapi_technical_corpus", split="train", streaming=True)
for row in dataset.take(3):
    print(row["chunk_id"], row["source"], row["upstream_license"])
```

## Retrieval Benchmark Example

Use `data/research_corpus/benchmarks/queries.jsonl` with your retriever and score against `qrels.jsonl`.

## Licensing

This is a mixed-license dataset. Downstream users must inspect `upstream_license` and `SOURCES_MANIFEST.yaml` before redistribution or commercial use.
"""
    write_markdown(path, text)
