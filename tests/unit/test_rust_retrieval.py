"""Unit tests for compiled Rust retrieval engine bindings in depth_engine."""
from __future__ import annotations

import pytest
import depth_engine
from api.services.rag.context_processing import (
    compress_contexts,
    normalize_context_text,
    reorder_lost_in_the_middle,
)
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges
from api.services.rag.graph.router import detect_graph_hops


def test_reorder_lost_in_the_middle_parity():
    # Odd count: 5 items
    items_odd = [{"id": f"doc_{i}", "rank": i} for i in range(5)]
    reordered_odd = depth_engine.reorder_lost_in_the_middle(items_odd)
    ranks_odd = [x["rank"] for x in reordered_odd]
    assert ranks_odd == [0, 2, 4, 3, 1]

    # Even count: 4 items
    items_even = [{"id": f"doc_{i}", "rank": i} for i in range(4)]
    reordered_even = depth_engine.reorder_lost_in_the_middle(items_even)
    ranks_even = [x["rank"] for x in reordered_even]
    assert ranks_even == [0, 2, 3, 1]

    # Small lists
    assert depth_engine.reorder_lost_in_the_middle([]) == []
    assert depth_engine.reorder_lost_in_the_middle([{"id": 1}]) == [{"id": 1}]
    assert depth_engine.reorder_lost_in_the_middle([{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]


def test_fuse_rrf_scoring_and_mosaic_algebra():
    dense = ["doc_a", "doc_b", "doc_c"]
    lex = ["doc_b", "doc_a", "doc_d"]

    # Basic fusion (k=60.0)
    fused = depth_engine.fuse_rrf(dense, lex, 60.0)
    assert len(fused) == 4
    scores = dict(fused)

    # doc_a: 1/60 + 1/61
    # doc_b: 1/61 + 1/60
    assert pytest.approx(scores["doc_a"], rel=1e-5) == scores["doc_b"]
    assert scores["doc_a"] > scores["doc_c"]
    assert scores["doc_b"] > scores["doc_d"]

    # Negative query algebra test (soft penalty lambda = 0.5)
    texts = {
        "doc_a": "FastAPI with OAuth2 authentication",
        "doc_b": "FastAPI with API Key tokens",
    }
    fused_penalized = depth_engine.fuse_rrf(
        dense,
        lex,
        60.0,
        negative_terms=["oauth2"],
        candidate_texts=texts,
    )
    penalized_scores = dict(fused_penalized)
    # doc_a should have its score cut in half by lambda=0.5
    assert pytest.approx(penalized_scores["doc_a"], rel=1e-5) == scores["doc_a"] * 0.5
    assert penalized_scores["doc_b"] > penalized_scores["doc_a"]


def test_detect_graph_hops_accuracy():
    # Intent = 1 (relational, dependency, architecture)
    assert depth_engine.detect_graph_hops("What depends on this module?") == 1
    assert depth_engine.detect_graph_hops("Show me the lineage of dataset X") == 1
    assert depth_engine.detect_graph_hops("What is the blast radius if service fails?") == 1
    assert depth_engine.detect_graph_hops("How does the ingest pipeline interact with postgres?") == 1
    assert depth_engine.detect_graph_hops("What calls this function?") == 1

    # Intent = 0 (factual, entity lookup)
    assert depth_engine.detect_graph_hops("What is Python?") == 0
    assert depth_engine.detect_graph_hops("How do I install postgres?") == 0
    assert depth_engine.detect_graph_hops("") == 0
    assert depth_engine.detect_graph_hops("   ") == 0


def test_crag_confidence_evaluation():
    # Empty candidates -> insufficient
    res_empty = depth_engine.evaluate_confidence([], False)
    assert res_empty["confidence"] == "insufficient"
    assert res_empty["is_insufficient"] is True

    # Reranked candidates
    res_high = depth_engine.evaluate_confidence([2.8, 1.1], True)
    assert res_high["confidence"] == "high"
    assert res_high["is_insufficient"] is False

    res_med = depth_engine.evaluate_confidence([-0.5, -1.2], True)
    assert res_med["confidence"] == "medium"

    res_low = depth_engine.evaluate_confidence([-3.5, -4.0], True)
    assert res_low["confidence"] == "low"

    # Context dict evaluation
    ctx_high = [{"rerank_score": 3.2, "content": "Sample"}]
    assert depth_engine.evaluate_confidence(ctx_high)["confidence"] == "high"

    ctx_low = [{"score": 0.005, "content": "Sample"}]
    assert depth_engine.evaluate_confidence(ctx_low)["confidence"] == "low"


def test_context_normalization_and_compression():
    raw_text = (
        "# Section 1\n"
        "[Link Title](https://example.com)\n"
        "> Quote block here.\n"
        "Repeated sentence. Repeated sentence. Second unique sentence."
    )
    normalized = depth_engine.normalize_context_text(raw_text, 1000)
    assert "# Section 1" not in normalized
    assert "Section 1" in normalized
    assert "Link Title (https://example.com)" in normalized
    assert normalized.count("Repeated sentence.") == 1
    assert "Second unique sentence." in normalized

    contexts = [
        {"doc_id": "doc_1", "content": "First document with information about rust."},
        {"doc_id": "doc_1", "content": "First document with duplicate text."},
        {"doc_id": "doc_2", "content": "Second unique document text."},
    ]
    compressed = depth_engine.compress_contexts(
        contexts,
        max_contexts=2,
        max_chars_per_context=500,
        max_total_chars=1000,
    )
    assert len(compressed) <= 2
    assert compressed[0]["token_count"] > 0


def test_concept_extractor_parity():
    md = """# Depth Engine
    ## Ingestion Pipeline
    Depends on [[PostgreSQL]] database for storage.
    """
    res = depth_engine.extract_concepts_and_edges(
        raw_text=md,
        document_title="Engine Architecture",
        known_entities=["PostgreSQL"],
    )
    assert "concepts" in res
    assert "edges" in res
    concept_names = [c["name"] for c in res["concepts"]]
    assert "Engine Architecture" in concept_names
    assert "PostgreSQL" in concept_names

    edges = res["edges"]
    assert any(e["relation_type"] == "depends_on" and e["target_concept"] == "PostgreSQL" for e in edges)
