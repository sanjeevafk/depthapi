#!/usr/bin/env python3
"""
benchmark_rust_retrieval_speedup.py
Measures execution latency and throughput speedup of compiled Rust depth_engine
retrieval functions vs pure Python reference implementations across 10,000 iterations.
"""
from __future__ import annotations

import time
import depth_engine
import numpy as np
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


def main() -> None:
    iterations = 10_000
    print("=" * 70)
    print(f"DepthAPI Rust Retrieval Engine Speedup Benchmark ({iterations:,} runs)")
    print("=" * 70)

    # 1. Benchmark Query Intent Router
    test_query = "How does the ingestion pipeline interact with postgres and what depends on it?"
    # Warmup
    for _ in range(100):
        python_router(test_query)
        depth_engine.detect_graph_hops(test_query)

    t0 = time.perf_counter()
    for _ in range(iterations):
        python_router(test_query)
    py_router_time = (time.perf_counter() - t0) * 1e6 / iterations

    t0 = time.perf_counter()
    for _ in range(iterations):
        depth_engine.detect_graph_hops(test_query)
    rust_router_time = (time.perf_counter() - t0) * 1e6 / iterations

    router_speedup = py_router_time / max(rust_router_time, 1e-9)
    print(f"\n[1] Intent Classification (Compiled RegexSet DFA vs Python Regexes):")
    print(f"    Python: {py_router_time:.3f} µs/query")
    print(f"    Rust:   {rust_router_time:.3f} µs/query  --> Speedup: {router_speedup:.1f}x")

    # 2. Benchmark Lost-in-the-Middle Reordering (10 candidates)
    test_contexts = [{"id": f"chunk_{i}", "score": 1.0 / (i + 1)} for i in range(10)]
    for _ in range(100):
        python_reorder(test_contexts)
        depth_engine.reorder_lost_in_the_middle(test_contexts)

    t0 = time.perf_counter()
    for _ in range(iterations):
        python_reorder(test_contexts)
    py_reorder_time = (time.perf_counter() - t0) * 1e6 / iterations

    t0 = time.perf_counter()
    for _ in range(iterations):
        depth_engine.reorder_lost_in_the_middle(test_contexts)
    rust_reorder_time = (time.perf_counter() - t0) * 1e6 / iterations

    reorder_speedup = py_reorder_time / max(rust_reorder_time, 1e-9)
    print(f"\n[2] Lost-in-the-Middle Permutation (10 contexts):")
    print(f"    Python: {py_reorder_time:.3f} µs/call")
    print(f"    Rust:   {rust_reorder_time:.3f} µs/call  --> Speedup: {reorder_speedup:.1f}x")

    # 3. Benchmark RRF Fusion (40 dense + 40 lexical = 80 pool candidates)
    dense_pool = [f"doc_{i}" for i in range(40)]
    lex_pool = [f"doc_{i}" for i in range(20, 60)]
    for _ in range(100):
        python_rrf(dense_pool, lex_pool)
        depth_engine.fuse_rrf(dense_pool, lex_pool, 60.0)

    t0 = time.perf_counter()
    for _ in range(iterations):
        python_rrf(dense_pool, lex_pool)
    py_rrf_time = (time.perf_counter() - t0) * 1e6 / iterations

    t0 = time.perf_counter()
    for _ in range(iterations):
        depth_engine.fuse_rrf(dense_pool, lex_pool, 60.0)
    rust_rrf_time = (time.perf_counter() - t0) * 1e6 / iterations

    rrf_speedup = py_rrf_time / max(rust_rrf_time, 1e-9)
    print(f"\n[3] Reciprocal Rank Fusion (80 candidates, k=60):")
    print(f"    Python: {py_rrf_time:.3f} µs/call")
    print(f"    Rust:   {rust_rrf_time:.3f} µs/call  --> Speedup: {rrf_speedup:.1f}x")

    print("\n" + "=" * 70)
    print("Retrieval Engine Benchmark Finished Successfully.")
    print("=" * 70)


if __name__ == "__main__":
    main()
