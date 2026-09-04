#!/usr/bin/env python3
"""
benchmark_rust_retrieval_speedup.py
Rigorous micro-benchmark measuring execution latency and percentiles (mean, p50, p95)
of compiled Rust depth_engine retrieval functions vs pure Python reference implementations.
"""
from __future__ import annotations

import statistics
import time

import depth_engine

from api.services.rag.graph import concept_extractor as py_concept_mod
from api.services.rag.graph.router import _RELATIONAL_PATTERNS


def python_reorder(contexts: list[dict]) -> list[dict]:
    if len(contexts) <= 2:
        return list(contexts)
    reordered = [None] * len(contexts)
    left = 0
    right = len(contexts) - 1
    for i, ctx in enumerate(contexts):
        if i % 2 == 0:
            reordered[left] = ctx
            left += 1
        else:
            reordered[right] = ctx
            right -= 1
    return reordered


def python_router(query: str) -> int:
    clean = query.strip()
    for p in _RELATIONAL_PATTERNS:
        if p.search(clean):
            return 1
    return 0


def python_rrf(dense_ranks: list[str], lex_ranks: list[str], k: float = 60.0) -> list[tuple[str, float]]:
    dense_map = {id_: rank for rank, id_ in enumerate(dense_ranks)}
    lex_map = {id_: rank for rank, id_ in enumerate(lex_ranks)}
    all_ids = set(dense_map.keys()) | set(lex_map.keys())
    results = []
    for id_ in all_ids:
        v_rank = dense_map.get(id_, 1_000_000)
        b_rank = lex_map.get(id_, 1_000_000)
        score = (1.0 / (k + v_rank)) + (1.0 / (k + b_rank))
        results.append((id_, score))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def python_crag(scores: list[float], is_reranked: bool = False) -> dict:
    if not scores:
        return {"confidence": "insufficient", "is_insufficient": True, "max_score": None}
    max_score = max(scores)
    if is_reranked:
        tier = "low" if max_score < -2.0 else ("medium" if max_score < 0.0 else "high")
    else:
        tier = "low" if max_score < 0.012 else ("medium" if max_score < 0.020 else "high")
    return {"confidence": tier, "is_insufficient": False, "max_score": max_score}


def measure(fn, *args, samples: int = 100, inner_iters: int = 200) -> tuple[float, float, float]:
    latencies = []
    for _ in range(samples):
        t0 = time.perf_counter()
        for _ in range(inner_iters):
            fn(*args)
        latencies.append((time.perf_counter() - t0) * 1e6 / inner_iters)
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    return statistics.mean(latencies), statistics.median(latencies), p95


def main() -> None:
    print("=" * 80)
    print("DepthAPI Retrieval Engine Benchmark: Compiled Rust vs Pure Python")
    print("=" * 80)

    # 1. Intent Router: Factual Query (worst-case for Python, common case in RAG)
    factual_q = "What is the recommended python version for local development?"
    py_mean, py_p50, py_p95 = measure(python_router, factual_q)
    rs_mean, rs_p50, rs_p95 = measure(depth_engine.detect_graph_hops, factual_q)
    print("\n[1] Intent Classification - Factual (DFA 1-pass vs 11 Python regex checks):")
    print(f"    Python: mean={py_mean:6.3f} µs | p50={py_p50:6.3f} µs | p95={py_p95:6.3f} µs")
    print(f"    Rust:   mean={rs_mean:6.3f} µs | p50={rs_p50:6.3f} µs | p95={rs_p95:6.3f} µs")
    print(f"    --> Speedup: {py_mean / rs_mean:5.1f}x (mean) | {py_p50 / rs_p50:5.1f}x (p50)")

    # 2. Intent Router: Relational Query (early-exit in Python)
    relational_q = "How does the ingestion pipeline interact with postgres and what depends on it?"
    py_mean, py_p50, py_p95 = measure(python_router, relational_q)
    rs_mean, rs_p50, rs_p95 = measure(depth_engine.detect_graph_hops, relational_q)
    print("\n[2] Intent Classification - Relational (Early-match pattern):")
    print(f"    Python: mean={py_mean:6.3f} µs | p50={py_p50:6.3f} µs | p95={py_p95:6.3f} µs")
    print(f"    Rust:   mean={rs_mean:6.3f} µs | p50={rs_p50:6.3f} µs | p95={rs_p95:6.3f} µs")
    print(f"    --> Speedup: {py_mean / rs_mean:5.1f}x (mean) | {py_p50 / rs_p50:5.1f}x (p50)")

    # 3. Lost-in-the-Middle Permutation (10 contexts)
    contexts = [{"id": f"chunk_{i}", "score": 1.0 / (i + 1)} for i in range(10)]
    py_mean, py_p50, py_p95 = measure(python_reorder, contexts)
    rs_mean, rs_p50, rs_p95 = measure(depth_engine.reorder_lost_in_the_middle, contexts)
    print("\n[3] Lost-in-the-Middle U-shaped Permutation (10 contexts):")
    print(f"    Python: mean={py_mean:6.3f} µs | p50={py_p50:6.3f} µs | p95={py_p95:6.3f} µs")
    print(f"    Rust:   mean={rs_mean:6.3f} µs | p50={rs_p50:6.3f} µs | p95={rs_p95:6.3f} µs")
    print(f"    --> Speedup: {py_mean / rs_mean:5.1f}x (mean) | {py_p50 / rs_p50:5.1f}x (p50)")

    # 4. CRAG Confidence Gating
    scores = [2.8, 1.2, 0.4, -0.1, -1.2]
    py_mean, py_p50, py_p95 = measure(python_crag, scores, True)
    rs_mean, rs_p50, rs_p95 = measure(depth_engine.evaluate_confidence, scores, True)
    print("\n[4] Corrective RAG Confidence Gating (5 scores):")
    print(f"    Python: mean={py_mean:6.3f} µs | p50={py_p50:6.3f} µs | p95={py_p95:6.3f} µs")
    print(f"    Rust:   mean={rs_mean:6.3f} µs | p50={rs_p50:6.3f} µs | p95={rs_p95:6.3f} µs")
    print(f"    --> Speedup: {py_mean / rs_mean:5.1f}x (mean) | {py_p50 / rs_p50:5.1f}x (p50)")

    # 5. Concept & Edge Graph Extraction
    doc = """
    # System Architecture Specification
    DepthAPI is an open cognitive synthesis engine.
    ## PostgreSQL Storage Engine
    The database layer manages pgvector indexes and isolation.
    Depends on [[PostgreSQL]] and [[pgvector]].
    ### Connection Pool
    Requires [[Asyncpg]] connection pooler for low-latency queries.
    ## Cross-Encoder Reranker
    Uses [[BAAI/bge-reranker-large]] for scoring top candidates.
    ## Hybrid Retrieval Pipeline
    Orchestrates dense vector search and BM25 postings.
    Depends on [[FAISS]] and [[BM25Okapi]].
    """
    entities = ["PostgreSQL", "pgvector", "Asyncpg", "FAISS", "BM25Okapi", "DepthAPI"]

    def run_py_extractor():
        py_concept_mod._HAS_DEPTH_ENGINE = False
        return py_concept_mod.extract_concepts_and_edges(doc, None, "Architecture", None, entities)

    def run_rs_extractor():
        return depth_engine.extract_concepts_and_edges(doc, None, "Architecture", None, entities)

    py_mean, py_p50, py_p95 = measure(run_py_extractor, samples=50, inner_iters=50)
    rs_mean, rs_p50, rs_p95 = measure(run_rs_extractor, samples=50, inner_iters=50)
    print("\n[5] AST Concept & Lineage Graph Extraction (Markdown AST + Entity Catalog):")
    print(f"    Python: mean={py_mean:6.3f} µs | p50={py_p50:6.3f} µs | p95={py_p95:6.3f} µs")
    print(f"    Rust:   mean={rs_mean:6.3f} µs | p50={rs_p50:6.3f} µs | p95={rs_p95:6.3f} µs")
    print(f"    --> Speedup: {py_mean / rs_mean:5.1f}x (mean) | {py_p50 / rs_p50:5.1f}x (p50)")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
