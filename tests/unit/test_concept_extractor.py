"""Unit tests for deterministic concept extraction and graph resolution."""
from api.services.rag.graph.concept_extractor import (
    extract_concepts_and_edges,
    Concept,
    ConceptEdge,
    ChunkConceptLink,
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
