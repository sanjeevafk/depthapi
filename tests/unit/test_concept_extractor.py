"""Unit tests for deterministic concept extraction and graph resolution."""
from api.services.rag.graph.concept_extractor import (
    extract_concepts_and_edges,
)


class MockChunk:
    def __init__(self, content: str, chunk_order: int, metadata: dict | None = None):
        self.content = content
        self.chunk_order = chunk_order
        self.metadata = metadata or {}


def test_extract_concepts_from_headings():
    raw_text = """# Architecture Overview
This is the system architecture.

## Router Engine
Handles request routing.

### Fast Path
Optimized execution branch.
"""
    graph = extract_concepts_and_edges(raw_text, document_title="System Spec")
    concept_names = [c.name for c in graph.concepts]
    assert "System Spec" in concept_names
    assert "Architecture Overview" in concept_names
    assert "Router Engine" in concept_names
    assert "Fast Path" in concept_names

    edge_pairs = [(e.source_concept, e.target_concept, e.relation_type) for e in graph.edges]
    assert ("System Spec", "Architecture Overview", "contains") in edge_pairs
    assert ("Architecture Overview", "Router Engine", "contains") in edge_pairs
    assert ("Router Engine", "Fast Path", "contains") in edge_pairs


def test_extract_wikilinks_and_dependencies():
    raw_text = """# Multi-LLM Router
Dynamically routes queries.

## Dependencies
- [[FastAPI Endpoints]]: Receives depth request
- [[Hybrid Retrieval]]: Triggered when depth >= 3
"""
    graph = extract_concepts_and_edges(raw_text)
    concept_names = [c.name for c in graph.concepts]
    assert "Multi-LLM Router" in concept_names
    assert "FastAPI Endpoints" in concept_names
    assert "Hybrid Retrieval" in concept_names

    edge_pairs = [(e.source_concept, e.target_concept, e.relation_type) for e in graph.edges]
    assert ("Dependencies", "FastAPI Endpoints", "depends_on") in edge_pairs
    assert ("Dependencies", "Hybrid Retrieval", "depends_on") in edge_pairs


def test_chunk_to_concept_linking():
    raw_text = """# Pipeline
Processing details.
"""
    chunks = [
        MockChunk(
            content="Mentions [[Hermes Agent]] in text.",
            chunk_order=0,
            metadata={"hierarchy": ["Pipeline", "Ingest"]},
        ),
        MockChunk(
            content="Another chunk without links.",
            chunk_order=1,
            metadata={},
        ),
    ]

    graph = extract_concepts_and_edges(raw_text, chunks=chunks, document_title="Pipeline Doc")
    assert len(graph.chunk_links) >= 3

    chunk0_links = [l for l in graph.chunk_links if l.chunk_index == 0]
    chunk0_concepts = {l.concept_name for l in chunk0_links}
    assert "Ingest" in chunk0_concepts
    assert "Pipeline" in chunk0_concepts
    assert "Hermes Agent" in chunk0_concepts

    chunk1_links = [l for l in graph.chunk_links if l.chunk_index == 1]
    assert any(l.concept_name == "Pipeline Doc" for l in chunk1_links)


def test_empty_or_plain_document_graceful_fallback():
    graph = extract_concepts_and_edges("", chunks=[], document_title=None)
    assert len(graph.concepts) == 0
    assert len(graph.edges) == 0
    assert len(graph.chunk_links) == 0


def test_extract_with_known_entities():
    raw_text = "Alabama is bordered by Tennessee to the north and Georgia to the east."
    chunks = [MockChunk(content=raw_text, chunk_order=0)]
    graph = extract_concepts_and_edges(
        raw_text=raw_text,
        chunks=chunks,
        document_title="Alabama",
        known_entities=["Tennessee", "Georgia", "Florida"],
    )

    names = {c.name for c in graph.concepts}
    assert "Alabama" in names
    assert "Tennessee" in names
    assert "Georgia" in names
    assert "Florida" not in names

    edges = [(e.source_concept, e.target_concept, e.relation_type) for e in graph.edges]
    assert ("Alabama", "Tennessee", "references") in edges
    assert ("Alabama", "Georgia", "references") in edges

    chunk_concepts = {l.concept_name for l in graph.chunk_links}
    assert "Tennessee" in chunk_concepts
    assert "Georgia" in chunk_concepts

