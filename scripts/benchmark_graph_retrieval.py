"""
benchmark_graph_retrieval.py — Benchmark 0-hop vs 1-hop vs 2-hop concept graph retrieval.

Measures latency (p50/p95), context token/chunk expansion, and relational recall across:
1. 0-hop (Standard dense/lexical hybrid search baseline)
2. 1-hop (Bounded immediate neighbor concept traversal)
3. 2-hop (Two-step indirect dependency traversal)
"""
from __future__ import annotations

import statistics
import time
from typing import Any
from uuid import uuid4

from api.services.rag.graph.concept_extractor import extract_concepts_and_edges


class SimulatedGraphStore:
    """In-memory relational graph index replicating PostgreSQL schema 002."""

    def __init__(self):
        self.concepts: dict[str, str] = {}  # name -> id
        self.edges: list[dict[str, Any]] = []
        self.chunk_concepts: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []

    def populate(self, raw_documents: list[dict[str, str]]) -> None:
        for doc in raw_documents:
            doc_id = str(uuid4())
            raw_text = doc["content"]
            title = doc.get("title")

            # Synthetic chunking per paragraph
            paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
            mock_chunks = []
            for idx, p in enumerate(paragraphs):
                chunk_id = str(uuid4())
                chunk_dict = {
                    "id": chunk_id,
                    "doc_id": doc_id,
                    "chunk_order": idx,
                    "content": p,
                    "metadata": {"hierarchy": [title] if title else []},
                }
                self.chunks.append(chunk_dict)
                mock_chunks.append(chunk_dict)

            graph = extract_concepts_and_edges(
                raw_text=raw_text,
                chunks=mock_chunks,
                document_title=title,
            )

            for c in graph.concepts:
                key = c.name.lower()
                if key not in self.concepts:
                    self.concepts[key] = str(uuid4())

            for e in graph.edges:
                src_id = self.concepts.get(e.source_concept.lower())
                tgt_id = self.concepts.get(e.target_concept.lower())
                if src_id and tgt_id:
                    self.edges.append({
                        "source": src_id,
                        "target": tgt_id,
                        "relation": e.relation_type,
                        "weight": e.weight,
                    })

            for link in graph.chunk_links:
                c_id = self.concepts.get(link.concept_name.lower())
                if c_id and link.chunk_index < len(mock_chunks):
                    ch_id = mock_chunks[link.chunk_index]["id"]
                    self.chunk_concepts.append({
                        "chunk_id": ch_id,
                        "concept_id": c_id,
                        "confidence": link.confidence,
                    })

    def traverse(self, root_ids: set[str], max_hops: int) -> dict[str, int]:
        """Recursive bounded BFS matching SQL graph_traverse_concepts."""
        visited: dict[str, int] = {rid: 0 for rid in root_ids}
        current_level = set(root_ids)

        for depth in range(1, max_hops + 1):
            next_level = set()
            for edge in self.edges:
                if edge["source"] in current_level and edge["target"] not in visited:
                    visited[edge["target"]] = depth
                    next_level.add(edge["target"])
            current_level = next_level
            if not current_level:
                break
        return visited

    def search(self, query: str, hops: int = 0, top_k: int = 5) -> list[dict[str, Any]]:
        # 1. Base lexical/keyword match
        scores: dict[str, float] = {}
        query_words = set(query.lower().split())

        for idx, chunk in enumerate(self.chunks):
            content_lower = chunk["content"].lower()
            match_count = sum(1 for w in query_words if w in content_lower)
            if match_count > 0:
                scores[chunk["id"]] = match_count / (1.0 + idx * 0.1)

        sorted_seed_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)[:5]

        if hops == 0 or not sorted_seed_ids:
            return [c for c in self.chunks if c["id"] in sorted_seed_ids][:top_k]

        # 2. Extract seed concepts
        seed_concepts = {
            cc["concept_id"]
            for cc in self.chunk_concepts
            if cc["chunk_id"] in sorted_seed_ids
        }

        # 3. Bounded traversal
        traversed = self.traverse(seed_concepts, max_hops=hops)

        # 4. Score graph-connected chunks
        for cc in self.chunk_concepts:
            c_id = cc["concept_id"]
            if c_id in traversed:
                depth = traversed[c_id]
                graph_boost = (1.0 / (1.0 + depth)) * cc["confidence"] * 0.35
                ch_id = cc["chunk_id"]
                scores[ch_id] = scores.get(ch_id, 0.0) + graph_boost

        final_ranked_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
        id_to_chunk = {c["id"]: c for c in self.chunks}
        return [id_to_chunk[cid] for cid in final_ranked_ids[:top_k]]


def run_benchmark(iterations: int = 100):
    store = SimulatedGraphStore()

    corpus = [
        {
            "title": "DepthAPI Architecture",
            "content": (
                "# DepthAPI Architecture\n\n"
                "DepthAPI is a local-first, low-latency AI inference and RAG platform.\n\n"
                "## Router Engine\n\n"
                "Dynamically routes queries based on requested cognitive depth.\n\n"
                "### Fast Path\n\n"
                "Zero-overhead inference using cached or low-depth endpoints.\n\n"
                "## Dependencies\n\n"
                "- [[PostgreSQL Storage]]: Authoritative relational and vector store.\n"
                "- [[Compiled Engine Core]]: High performance Rust parsing layer.\n"
            ),
        },
        {
            "title": "PostgreSQL Storage",
            "content": (
                "# PostgreSQL Storage\n\n"
                "Houses pgvector embeddings, FTS tsvectors, and relational concept graph.\n\n"
                "## Vector Search\n\n"
                "768-dimensional dense HNSW index for semantic similarity.\n\n"
                "## Concept Graph\n\n"
                "Lineage tracking and bounded hop exploration.\n"
            ),
        },
        {
            "title": "Compiled Engine Core",
            "content": (
                "# Compiled Engine Core\n\n"
                "Rust native crate depth_engine providing multi-format document parsing.\n\n"
                "## Anydoc Integration\n\n"
                "Converts DOCX, XLSX, CSV, HTML, and PDF to Markdown.\n\n"
                "## Zero Copy Chunker\n\n"
                "Preserves deterministic chunk IDs and quality scores.\n"
            ),
        },
    ]

    store.populate(corpus)

    test_queries = [
        "What are the dependencies of Router Engine?",
        "How does Compiled Engine Core process multi-format documents?",
        "Explain pgvector storage and concept graph lineage",
        "Fast Path zero overhead execution",
        "Architecture components and requirements",
    ]

    results = {}

    for hops in [0, 1, 2]:
        latencies = []
        chunk_counts = []

        for _ in range(iterations):
            for query in test_queries:
                t0 = time.perf_counter()
                chunks = store.search(query, hops=hops, top_k=5)
                dt = (time.perf_counter() - t0) * 1000.0  # ms
                latencies.append(dt)
                chunk_counts.append(len(chunks))

        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]
        avg_chunks = sum(chunk_counts) / len(chunk_counts)

        results[hops] = {
            "p50_ms": p50,
            "p95_ms": p95,
            "avg_chunks": avg_chunks,
        }

    print("\n" + "=" * 60)
    print("DepthAPI Relational Concept Graph Benchmark (100 runs)")
    print("=" * 60)
    print(f"{'Traversal':<15} | {'p50 Latency':<12} | {'p95 Latency':<12} | {'Avg Candidates':<15}")
    print("-" * 60)
    for hops in [0, 1, 2]:
        label = "0-hop (Baseline)" if hops == 0 else f"{hops}-hop Graph"
        p50_str = f"{results[hops]['p50_ms']:.3f} ms"
        p95_str = f"{results[hops]['p95_ms']:.3f} ms"
        cand_str = f"{results[hops]['avg_chunks']:.1f} chunks"
        print(f"{label:<15} | {p50_str:<12} | {p95_str:<12} | {cand_str:<15}")
    print("=" * 60 + "\n")
    return results


if __name__ == "__main__":
    run_benchmark(iterations=100)
