"""
benchmark_depth_engine.py — Measure parsing & chunking throughput of depth_engine (Rust) vs Python.

Generates self-contained minimal valid fixtures (.docx, .xlsx, .csv, .html, .md)
and benchmarks end-to-end ingestion throughput and p50/p95 latency.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import depth_engine
from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
from api.services.rag.pipeline.models import Document
from api.services.rag.pipeline.parsers.markdown_parser import MarkdownParser

FIXTURES_DIR = Path("tests/fixtures")


def generate_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        z.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        z.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t># DepthAPI Technical Specification</w:t></w:r></w:p>
    <w:p><w:r><w:t>This document outlines the high-speed Rust ingestion engine and chunk lineage tracking.</w:t></w:r></w:p>
    <w:p><w:r><w:t>## Architecture Highlights</w:t></w:r></w:p>
    <w:p><w:r><w:t>Native compiled extension handles zero-copy document tokenization and extraction.</w:t></w:r></w:p>
  </w:body>
</w:document>""",
        )
    return buf.getvalue()


def generate_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""",
        )
        z.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>""",
        )
        z.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Benchmark" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>""",
        )
        z.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6">
  <si><t>Benchmark Component</t></si>
  <si><t>Measured Metric</t></si>
  <si><t>Parsing Speed</t></si>
  <si><t>0.45ms</t></si>
  <si><t>Memory Footprint</t></si>
  <si><t>Sub-Megabyte</t></si>
</sst>""",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
    <row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3" t="s"><v>5</v></c></row>
  </sheetData>
</worksheet>""",
        )
    return buf.getvalue()


def ensure_fixtures() -> dict[str, bytes]:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, bytes] = {}

    # DOCX
    docx_bytes = generate_docx()
    (FIXTURES_DIR / "sample.docx").write_bytes(docx_bytes)
    fixtures["sample.docx"] = docx_bytes

    # XLSX
    xlsx_bytes = generate_xlsx()
    (FIXTURES_DIR / "metrics.xlsx").write_bytes(xlsx_bytes)
    fixtures["metrics.xlsx"] = xlsx_bytes

    # CSV
    csv_bytes = b"service,qps,p95_latency_ms\ningestion,2500,0.4\nembedding,120,45.0\nretrieval,850,2.1\n"
    (FIXTURES_DIR / "data.csv").write_bytes(csv_bytes)
    fixtures["data.csv"] = csv_bytes

    # HTML
    html_bytes = b"<!DOCTYPE html><html><body><h1>DepthAPI Reference</h1><p>Documentation on high-speed neural RAG architecture.</p><h2>Endpoints</h2><ul><li>POST /ingest</li><li>POST /query</li></ul></body></html>"
    (FIXTURES_DIR / "page.html").write_bytes(html_bytes)
    fixtures["page.html"] = html_bytes

    # Markdown
    md_bytes = b"""# DepthAPI System Architecture

DepthAPI is an open cognitive synthesis engine designed for sub-millisecond document ingestion and neural hybrid retrieval.

## Ingestion Core

Document bytes are parsed into GitHub-Flavored Markdown and segmented into deterministic chunks.

```python
import depth_engine
res = depth_engine.parse_and_chunk(doc_id="1", raw_bytes=b"# Test", filename_or_ext="test.md")
```

## Hybrid Search

PostgreSQL full-text search is fused with pgvector dense embeddings via Reciprocal Rank Fusion ($k=60$).
"""
    (FIXTURES_DIR / "article.md").write_bytes(md_bytes)
    fixtures["article.md"] = md_bytes

    return fixtures


def benchmark_format(filename: str, raw_bytes: bytes, iterations: int = 100) -> dict[str, Any]:
    latencies: list[float] = []

    for i in range(iterations):
        t0 = time.perf_counter()
        res = depth_engine.parse_and_chunk(
            doc_id=f"bench-{i}",
            raw_bytes=raw_bytes,
            filename_or_ext=filename,
            source_name=filename,
            max_tokens=480,
            min_tokens=5,
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)  # ms

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    avg = sum(latencies) / len(latencies)
    throughput = 1000.0 / avg if avg > 0 else 0.0

    return {
        "filename": filename,
        "format": res["parsed_doc"]["format"],
        "chunks": len(res["chunks"]),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "avg_ms": round(avg, 3),
        "docs_per_sec": round(throughput, 1),
    }


def benchmark_python_markdown(raw_bytes: bytes, iterations: int = 100) -> dict[str, Any]:
    parser = MarkdownParser()
    chunker = SemanticChunker(config={"min_tokens": 5, "max_tokens": 480})
    latencies: list[float] = []

    for i in range(iterations):
        t0 = time.perf_counter()
        doc = Document.from_bytes("direct://test", raw_bytes, "text/markdown")
        p_doc = parser.parse(doc)
        chunks = chunker.chunk(p_doc, "v1", "test")
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    avg = sum(latencies) / len(latencies)
    throughput = 1000.0 / avg if avg > 0 else 0.0

    return {
        "filename": "article.md (Python Reference)",
        "format": "markdown",
        "chunks": len(chunks),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "avg_ms": round(avg, 3),
        "docs_per_sec": round(throughput, 1),
    }


def main() -> None:
    print("=" * 70)
    print("  DepthAPI Multi-Format Ingestion Benchmark: Rust (depth_engine) vs Python")
    print("=" * 70)
    print(f"Engine Version: {depth_engine.engine_version()}")

    fixtures = ensure_fixtures()
    print(f"Generated {len(fixtures)} self-contained fixtures in {FIXTURES_DIR}/\n")

    results: list[dict[str, Any]] = []
    for filename, raw_bytes in fixtures.items():
        res = benchmark_format(filename, raw_bytes, iterations=100)
        results.append(res)

    py_res = benchmark_python_markdown(fixtures["article.md"], iterations=100)

    print(f"{'Format/File':<28} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'Avg (ms)':<9} | {'Throughput (docs/s)':<20}")
    print("-" * 85)
    for r in results:
        print(f"{r['filename']:<28} | {r['p50_ms']:<9} | {r['p95_ms']:<9} | {r['avg_ms']:<9} | {r['docs_per_sec']:<20}")
    print("-" * 85)
    print(f"{py_res['filename']:<28} | {py_res['p50_ms']:<9} | {py_res['p95_ms']:<9} | {py_res['avg_ms']:<9} | {py_res['docs_per_sec']:<20}")
    print("=" * 85)

    md_rust = next(r for r in results if r["filename"] == "article.md")
    speedup = py_res["avg_ms"] / md_rust["avg_ms"] if md_rust["avg_ms"] > 0 else 1.0
    print(f"\n⚡ Speedup on Markdown: Rust depth_engine is {speedup:.1f}x FASTER than Python reference pipeline!")


if __name__ == "__main__":
    main()
