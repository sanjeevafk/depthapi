"""
test_depth_engine.py — Unit tests for compiled Rust depth_engine and its Python adapter.
"""
from __future__ import annotations

import uuid
import pytest

import depth_engine
from api.routers.ingest import _run_pipeline
from api.services.rag.pipeline.depth_engine_adapter import (
    get_engine_version,
    has_depth_engine,
    run_depth_engine_pipeline,
)
from api.services.rag.pipeline.models import Chunk, Document


def test_depth_engine_version() -> None:
    assert has_depth_engine() is True
    assert get_engine_version() == "0.1.0"
    assert depth_engine.engine_version() == "0.1.0"


def test_markdown_to_markdown() -> None:
    raw = b"# Header 1\n\nSome introductory text.\n\n## Subheader\n\nMore details."
    res = depth_engine.to_markdown(raw, "notes.md")
    assert res["format"] == "markdown"
    assert res["confidence"] == 1.0
    assert "# Header 1" in res["markdown"]
    assert "More details." in res["markdown"]
    assert res["warnings"] == []


def test_html_to_markdown() -> None:
    html = b"<html><body><h1>Title</h1><p>Paragraph with &amp; entity</p></body></html>"
    res = depth_engine.to_markdown(html, "index.html")
    assert res["format"] == "html"
    assert res["confidence"] >= 0.9
    assert "# Title" in res["markdown"]
    assert "Paragraph with & entity" in res["markdown"]


def test_csv_to_markdown_via_anydoc() -> None:
    csv_bytes = b"module,status\nauth,passed\ningest,passed\n"
    res = depth_engine.to_markdown(csv_bytes, "status.csv")
    assert res["format"] == "csv"
    assert "| module | status |" in res["markdown"]
    assert "| auth | passed |" in res["markdown"]


def test_chunk_markdown_structure() -> None:
    md = """# Architecture Overview

DepthAPI is an open cognitive synthesis engine.

## Component A

Component A handles ingestion and validation.

## Component B

Component B manages pgvector hybrid search and reranking.
"""
    doc_id = "test-doc-abc"
    chunks = depth_engine.chunk_markdown(
        markdown=md,
        doc_id=doc_id,
        source_name="Architecture",
        source_url="https://docs.depthapi.dev/arch",
        dataset_version="v1",
        source_content_hash="dummy-hash",
        parser_version="depth-engine@0.1.0",
        parser_name="depth-engine",
        extraction_confidence=1.0,
        max_tokens=480,
        min_tokens=5,
    )
    assert len(chunks) >= 2
    for i, c in enumerate(chunks):
        assert c["doc_id"] == doc_id
        assert c["chunk_order"] == i
        assert len(c["chunk_id"]) == 64  # SHA-256
        assert c["token_count"] > 0
        assert 0.0 <= c["quality_score"] <= 1.0
        assert c["schema_version"] == "1.0.0"


def test_parse_and_chunk_full_native() -> None:
    raw = b"# Section 1\n\nContent paragraph.\n\n## Section 2\n\nCode snippet:\n```python\nprint('hello')\n```"
    doc_id = "doc-xyz"
    result = depth_engine.parse_and_chunk(
        doc_id=doc_id,
        raw_bytes=raw,
        filename_or_ext="code.md",
        source_url="https://depthapi.dev/code",
        source_name="Code Snippets",
        dataset_version="v1",
        max_tokens=480,
        min_tokens=5,
    )
    assert "parsed_doc" in result
    assert "chunks" in result
    assert result["parsed_doc"]["doc_id"] == doc_id
    assert len(result["chunks"]) >= 1


def test_corrupt_file_soft_fail_with_lineage_warning() -> None:
    # Partially readable fake PDF containing extractable ASCII strings
    corrupt_pdf = b"%PDF-1.4 " + b"Z" * 30 + b" Valid salvaged string from degraded file " + b"\x00\xff" * 15
    res = depth_engine.to_markdown(corrupt_pdf, "corrupted.pdf")
    assert res["confidence"] < 0.5
    assert len(res["warnings"]) > 0
    assert "AnyDoc conversion failed" in res["warnings"][0]
    assert "Valid salvaged string" in res["markdown"]


def test_unrecognizable_binary_hard_fail() -> None:
    garbage = b"\x00\x01\x02\x03\xff\xfe\xfd\xfc" * 4
    with pytest.raises(ValueError, match="Unsupported or unrecognized binary format"):
        depth_engine.to_markdown(garbage, "garbage.bin")


def test_run_depth_engine_pipeline_adapter() -> None:
    doc_id = uuid.uuid4()
    doc, chunks = run_depth_engine_pipeline(
        raw_text="# Testing Adapter\n\nValidating Pydantic model conversion.",
        document_id=doc_id,
        filename="adapter_test.md",
        user_metadata={"tag": "unit-test"},
    )
    assert isinstance(doc, Document)
    assert len(chunks) >= 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].doc_id == str(doc_id)
    assert chunks[0].metadata["tag"] == "unit-test"
    assert chunks[0].quality_score >= 0.8


def test_ingest_router_run_pipeline_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = uuid.uuid4()
    text = "# Pure Python Fallback\n\nTesting fallback when native engine is disabled."

    # Force has_depth_engine to return False
    monkeypatch.setattr("api.routers.ingest.has_depth_engine", lambda: False)

    doc, chunks = _run_pipeline(
        raw_text=text,
        document_id=doc_id,
        filename="fallback.md",
        source_url=None,
        collection_name="test_col",
        user_metadata={"fallback": True},
    )
    assert isinstance(doc, Document)
    assert len(chunks) >= 1
    assert chunks[0].doc_id == doc.doc_id
    assert chunks[0].parser_version.startswith("MarkdownParser")



def test_ingest_router_run_pipeline_with_depth_engine() -> None:
    doc_id = uuid.uuid4()
    text = "# Native Depth Engine\n\nDirectly invoking native core via engine parameter."

    doc, chunks = _run_pipeline(
        raw_text=text,
        document_id=doc_id,
        filename="native.md",
        source_url=None,
        collection_name="test_col",
        user_metadata={"engine": "depth-engine"},
        engine="depth-engine",
    )
    assert isinstance(doc, Document)
    assert len(chunks) >= 1
    assert chunks[0].doc_id == str(doc_id)
    assert "depth-engine" in chunks[0].parser_version

