#!/usr/bin/env python3
"""BEIR Benchmark Runner for DepthAPI.

Evaluates retrieval quality (NDCG@10, Recall@10, MRR@10, latency) across:
1. Lexical search (BM25 / token match)
2. Dense vector search (BAAI/bge-base-en-v1.5)
3. RRF hybrid search (Reciprocal Rank Fusion k=60)
4. RRF hybrid + CrossEncoder reranker (optional)

Usage:
    python scripts/benchmark_beir.py --dataset scifact --limit-docs 1000 --limit-queries 100
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import io
import json
import math
import os
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set

import numpy as np

# Ensure repository root is in python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.services.rag.embeddings import get_local_transformer
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges


DATASET_URLS = {
    "scifact": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
    "nfcorpus": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip",
    "hotpotqa": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/hotpotqa.zip",
}


def download_and_extract_dataset(dataset_name: str, cache_dir: Path) -> Path:
    target_dir = cache_dir / dataset_name
    if (target_dir / "corpus.jsonl").exists() and (target_dir / "qrels" / "test.tsv").exists():
        return target_dir

    if dataset_name not in DATASET_URLS:
        raise ValueError(f"Unknown dataset: {dataset_name}. Available: {list(DATASET_URLS.keys())}")

    url = DATASET_URLS[dataset_name]
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{dataset_name}.zip"

    if not zip_path.exists():
        print(f"Downloading {dataset_name} from {url}...")
        req = urllib.request.Request(url, headers={"User-Agent": "DepthAPI-Benchmark/1.0"})
        with urllib.request.urlopen(req, timeout=600) as resp, open(zip_path, "wb") as f_out:
            downloaded = 0
            while True:
                chunk = resp.read(2 * 1024 * 1024)
                if not chunk:
                    break
                f_out.write(chunk)
                downloaded += len(chunk)
                if downloaded % (20 * 1024 * 1024) == 0:
                    print(f"Downloaded {downloaded // (1024 * 1024)} MB...")
        print(f"Finished downloading {dataset_name} ({downloaded // (1024 * 1024)} MB).")

    print(f"Extracting {zip_path} to {cache_dir}...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(cache_dir)

    return target_dir

    return target_dir


def load_beir_data(
    data_dir: Path, limit_docs: int | None = None, limit_queries: int | None = None
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, int]]]:
    """Load queries with ground truth and their relevant docs + distractors."""
    # 1. Load qrels
    all_qrels: dict[str, dict[str, int]] = {}
    with open(data_dir / "qrels" / "test.tsv", "r", encoding="utf-8") as f:
        header = True
        for line in f:
            if header:
                header = False
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                qid, doc_id, score = parts[0], parts[1], int(parts[2])
                if qid not in all_qrels:
                    all_qrels[qid] = {}
                all_qrels[qid][doc_id] = score

    # 2. Load queries
    all_queries: dict[str, str] = {}
    with open(data_dir / "queries.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            q = json.loads(line)
            qid = str(q["_id"])
            if qid in all_qrels and all_qrels[qid]:
                all_queries[qid] = q["text"]

    # Select queries
    if limit_queries and len(all_queries) > limit_queries:
        selected_qids = list(all_queries.keys())[:limit_queries]
        queries = {qid: all_queries[qid] for qid in selected_qids}
        qrels = {qid: all_qrels[qid] for qid in selected_qids}
    else:
        queries = all_queries
        qrels = {qid: all_qrels[qid] for qid in queries}

    needed_doc_ids = {did for qid in queries for did in qrels[qid]}

    # 3. Load corpus: include all relevant documents, then add distractors up to limit_docs
    corpus: dict[str, str] = {}
    titles: dict[str, str] = {}
    distractors: dict[str, tuple[str, str]] = {}

    with open(data_dir / "corpus.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            doc_id = str(doc["_id"])
            title = doc.get("title", "").strip()
            text = f"{title} {doc.get('text', '')}".strip()
            if doc_id in needed_doc_ids:
                corpus[doc_id] = text
                titles[doc_id] = title
            elif not limit_docs or (len(corpus) + len(distractors)) < limit_docs:
                distractors[doc_id] = (title, text)

            if limit_docs and len(corpus) == len(needed_doc_ids) and (len(corpus) + len(distractors)) >= limit_docs:
                break

    for did, (t, text) in distractors.items():
        if limit_docs and len(corpus) >= limit_docs:
            break
        corpus[did] = text
        titles[did] = t

    return corpus, queries, qrels, titles


def compute_dcg(relevances: list[int], k: int = 10) -> float:
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        dcg += (2**rel - 1) / math.log2(i + 2)
    return dcg


def compute_metrics(
    retrieved_ids: list[str], ground_truth: dict[str, int], k: int = 10
) -> dict[str, float]:
    relevances = [ground_truth.get(did, 0) for did in retrieved_ids[:k]]
    actual_dcg = compute_dcg(relevances, k)

    ideal_relevances = sorted(ground_truth.values(), reverse=True)
    ideal_dcg = compute_dcg(ideal_relevances, k)

    ndcg = actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0

    # Recall@k (binary relevance: score > 0)
    relevant_retrieved = sum(1 for r in relevances if r > 0)
    total_relevant = sum(1 for score in ground_truth.values() if score > 0)
    recall = relevant_retrieved / total_relevant if total_relevant > 0 else 0.0

    # MRR@k
    mrr = 0.0
    for i, r in enumerate(relevances):
        if r > 0:
            mrr = 1.0 / (i + 1)
            break

    return {"ndcg@10": ndcg, "recall@10": recall, "mrr@10": mrr}


def build_lexical_index(corpus: dict[str, str]):
    """Build lightweight token-based BM25 index."""
    from rank_bm25 import BM25Okapi

    doc_ids = list(corpus.keys())
    tokenized_corpus = [corpus[did].lower().split() for did in doc_ids]
    bm25 = BM25Okapi(tokenized_corpus)
    return bm25, doc_ids


def evaluate(
    corpus: dict[str, str],
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    titles: dict[str, str] | None = None,
    cache_path: Path | None = None,
    enable_rerank: bool = False,
) -> dict[str, Any]:
    print(f"\nIndexing {len(corpus)} documents with BAAI/bge-base-en-v1.5...")
    doc_ids = list(corpus.keys())

    corpus_emb_matrix = None
    if cache_path and cache_path.exists():
        try:
            cached_data = np.load(cache_path, allow_pickle=True)
            if list(cached_data["doc_ids"]) == doc_ids:
                corpus_emb_matrix = cached_data["embeddings"]
                print(f"Loaded cached embeddings from {cache_path} (Shape: {corpus_emb_matrix.shape})")
        except Exception:
            corpus_emb_matrix = None

    if corpus_emb_matrix is None:
        model = get_local_transformer("BAAI/bge-base-en-v1.5")
        if model is None:
            raise RuntimeError("Failed to load local SentenceTransformer model")
        corpus_texts = [corpus[did] for did in doc_ids]
        t0 = time.perf_counter()
        corpus_embeddings = model.encode(
            corpus_texts, batch_size=32, show_progress_bar=True, normalize_embeddings=True
        )
        corpus_emb_matrix = np.array(corpus_embeddings, dtype=np.float32)
        print(f"Corpus embedded in {time.perf_counter() - t0:.2f}s (Shape: {corpus_emb_matrix.shape})")
        if cache_path:
            np.savez_compressed(cache_path, doc_ids=doc_ids, embeddings=corpus_emb_matrix)
            print(f"Saved embedding cache to {cache_path}")
    else:
        model = get_local_transformer("BAAI/bge-base-en-v1.5")

    print("Building lexical BM25 index...")
    bm25, bm25_doc_ids = build_lexical_index(corpus)

    print("Extracting relational concept graph from corpus...")
    doc_concepts: dict[str, set[str]] = defaultdict(set)
    concept_docs: dict[str, set[str]] = defaultdict(set)
    concept_edges: dict[str, set[str]] = defaultdict(set)

    # Known entities are the document titles with length >= 3
    entity_catalog = [t for t in (titles or {}).values() if len(t) >= 3]

    for did, doc_text in corpus.items():
        doc_title = (titles or {}).get(did)
        graph = extract_concepts_and_edges(
            raw_text=doc_text,
            document_title=doc_title,
            known_entities=entity_catalog,
        )
        for c in graph.concepts:
            c_name = c.name.lower()
            doc_concepts[did].add(c_name)
            concept_docs[c_name].add(did)
        for e in graph.edges:
            s = e.source_concept.lower()
            t = e.target_concept.lower()
            concept_edges[s].add(t)
            concept_edges[t].add(s)

    total_edges = sum(len(v) for v in concept_edges.values()) // 2
    print(f"Extracted {len(concept_docs)} concepts and {total_edges} cross-concept edges.")

    reranker = None
    if enable_rerank:
        from api.services.rag.reranker import get_reranker_service

        reranker = get_reranker_service()

    strategies = ["dense", "lexical", "hybrid_rrf", "hybrid_graph_1hop", "hybrid_graph_2hop"]
    if enable_rerank:
        strategies.append("hybrid_rrf_rerank")

    metrics_by_strategy: dict[str, list[dict[str, float]]] = {s: [] for s in strategies}
    latencies_by_strategy: dict[str, list[float]] = {s: [] for s in strategies}

    print(f"\nEvaluating {len(queries)} queries across strategies: {strategies}...")

    for qid, query_text in queries.items():
        truth = qrels[qid]

        # 1. Dense retrieval
        t_start = time.perf_counter()
        q_emb = model.encode([query_text], normalize_embeddings=True)[0]
        # Cosine similarity is dot product because vectors are normalized
        dense_scores = np.dot(corpus_emb_matrix, q_emb)
        dense_top_indices = np.argsort(-dense_scores)[:40]
        dense_top_ids = [doc_ids[idx] for idx in dense_top_indices]
        latencies_by_strategy["dense"].append(time.perf_counter() - t_start)
        metrics_by_strategy["dense"].append(compute_metrics(dense_top_ids, truth))

        # 2. Lexical retrieval
        t_start = time.perf_counter()
        tokenized_query = query_text.lower().split()
        lex_scores = bm25.get_scores(tokenized_query)
        lex_top_indices = np.argsort(-lex_scores)[:40]
        lex_top_ids = [bm25_doc_ids[idx] for idx in lex_top_indices]
        latencies_by_strategy["lexical"].append(time.perf_counter() - t_start)
        metrics_by_strategy["lexical"].append(compute_metrics(lex_top_ids, truth))

        # 3. Hybrid RRF (k=60)
        t_start = time.perf_counter()
        rrf_scores: dict[str, float] = defaultdict(float)
        for rank, did in enumerate(dense_top_ids):
            rrf_scores[did] += 1.0 / (60.0 + rank + 1)
        for rank, did in enumerate(lex_top_ids):
            rrf_scores[did] += 1.0 / (60.0 + rank + 1)

        hybrid_ranked = sorted(rrf_scores.keys(), key=lambda d: rrf_scores[d], reverse=True)
        latencies_by_strategy["hybrid_rrf"].append(time.perf_counter() - t_start)
        metrics_by_strategy["hybrid_rrf"].append(compute_metrics(hybrid_ranked, truth))

        # 4. Hybrid Graph 1-hop
        t_start = time.perf_counter()
        graph_1hop_scores = dict(rrf_scores)
        seed_docs = hybrid_ranked[:5]
        seed_concepts = set()
        for sdid in seed_docs:
            seed_concepts.update(doc_concepts[sdid])

        traversed_1hop = set()
        for sc in seed_concepts:
            traversed_1hop.update(concept_edges[sc])

        for c in traversed_1hop:
            for target_did in concept_docs[c]:
                graph_1hop_scores[target_did] = graph_1hop_scores.get(target_did, 0.0) + (1.0 / (60.0 + 1)) * 0.25

        ranked_1hop = sorted(graph_1hop_scores.keys(), key=lambda d: graph_1hop_scores[d], reverse=True)
        latencies_by_strategy["hybrid_graph_1hop"].append(time.perf_counter() - t_start)
        metrics_by_strategy["hybrid_graph_1hop"].append(compute_metrics(ranked_1hop, truth))

        # 5. Hybrid Graph 2-hop
        t_start = time.perf_counter()
        graph_2hop_scores = dict(graph_1hop_scores)
        traversed_2hop = set()
        for c in traversed_1hop:
            traversed_2hop.update(concept_edges[c])
        traversed_2hop.difference_update(seed_concepts)
        traversed_2hop.difference_update(traversed_1hop)

        for c in traversed_2hop:
            for target_did in concept_docs[c]:
                graph_2hop_scores[target_did] = graph_2hop_scores.get(target_did, 0.0) + (1.0 / (60.0 + 2)) * 0.25

        ranked_2hop = sorted(graph_2hop_scores.keys(), key=lambda d: graph_2hop_scores[d], reverse=True)
        latencies_by_strategy["hybrid_graph_2hop"].append(time.perf_counter() - t_start)
        metrics_by_strategy["hybrid_graph_2hop"].append(compute_metrics(ranked_2hop, truth))

        # 6. Hybrid RRF + Reranker (optional)
        if enable_rerank and reranker:
            t_start = time.perf_counter()
            top_candidates = [{"id": did, "content": corpus[did]} for did in hybrid_ranked[:10]]
            import asyncio

            reranked = asyncio.run(reranker.rerank(query_text, top_candidates, top_n=10))
            reranked_ids = [c["id"] for c in reranked]
            latencies_by_strategy["hybrid_rrf_rerank"].append(time.perf_counter() - t_start)
            metrics_by_strategy["hybrid_rrf_rerank"].append(compute_metrics(reranked_ids, truth))

    summary: dict[str, Any] = {}
    for s in strategies:
        m_list = metrics_by_strategy[s]
        l_list = latencies_by_strategy[s]
        summary[s] = {
            "ndcg@10": round(float(np.mean([m["ndcg@10"] for m in m_list])), 4),
            "recall@10": round(float(np.mean([m["recall@10"] for m in m_list])), 4),
            "mrr@10": round(float(np.mean([m["mrr@10"] for m in m_list])), 4),
            "p50_latency_ms": round(float(np.percentile(l_list, 50) * 1000), 2),
            "p95_latency_ms": round(float(np.percentile(l_list, 95) * 1000), 2),
        }

    return summary


def main():
    parser = argparse.ArgumentParser(description="DepthAPI BEIR Retrieval Evaluation")
    parser.add_argument("--dataset", choices=list(DATASET_URLS.keys()), default="scifact")
    parser.add_argument("--limit-docs", type=int, default=1000, help="Max documents (0 for full)")
    parser.add_argument("--limit-queries", type=int, default=100, help="Max queries (0 for full)")
    parser.add_argument("--rerank", action="store_true", help="Include cross-encoder reranker")
    parser.add_argument("--output", type=str, default="datasets/benchmarks/results.json")
    args = parser.parse_args()

    cache_dir = REPO_ROOT / "datasets" / "benchmarks" / "beir_cache"
    data_dir = download_and_extract_dataset(args.dataset, cache_dir)

    limit_docs = None if args.limit_docs <= 0 else args.limit_docs
    limit_queries = None if args.limit_queries <= 0 else args.limit_queries

    corpus, queries, qrels, titles = load_beir_data(
        data_dir, limit_docs=limit_docs, limit_queries=limit_queries
    )
    print(f"Loaded {len(corpus)} documents and {len(queries)} evaluated test queries.")

    cache_path = data_dir / f"embeddings_{len(corpus)}.npz"
    summary = evaluate(
        corpus, queries, qrels, titles=titles, cache_path=cache_path, enable_rerank=args.rerank
    )

    print("\n" + "=" * 65)
    print(f"BEIR Benchmark Results: {args.dataset.upper()} (Corpus: {len(corpus)}, Queries: {len(queries)})")
    print("=" * 65)
    print(f"{'Strategy':<20} | {'NDCG@10':<9} | {'Recall@10':<10} | {'MRR@10':<8} | {'p50 (ms)':<9} | {'p95 (ms)'}")
    print("-" * 65)
    for strat, res in summary.items():
        print(
            f"{strat:<20} | {res['ndcg@10']:<9.4f} | {res['recall@10']:<10.4f} | {res['mrr@10']:<8.4f} | {res['p50_latency_ms']:<9.2f} | {res['p95_latency_ms']:.2f}"
        )
    print("=" * 65)

    out_path = REPO_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "dataset": args.dataset,
                "docs_evaluated": len(corpus),
                "queries_evaluated": len(queries),
                "results": summary,
            },
            f,
            indent=2,
        )
    print(f"\nManifest and benchmark results written to: {out_path}")


if __name__ == "__main__":
    main()
