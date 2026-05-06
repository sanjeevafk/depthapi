"""
evaluate_retrieval.py — Offline retrieval benchmark for the DepthAPI RAG pipeline.

Measures: Recall@K, MRR (Mean Reciprocal Rank), nDCG, Hit Rate.
Loads ground-truth from evaluation/ground_truth.json and runs queries
against the local chunks.json index (for offline) or live Supabase.

Usage:
    python scripts/ingest_corpus/evaluate_retrieval.py
    python scripts/ingest_corpus/evaluate_retrieval.py --top-k 5 10 20
    python scripts/ingest_corpus/evaluate_retrieval.py --source-filter books
    python scripts/ingest_corpus/evaluate_retrieval.py --verbose       # per-query rows
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import re
import sys
from pathlib import Path
from typing import Any
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ingest_corpus.base_ingestor import CHUNKS_FILE, log

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)

EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
QUERIES_FILE = EVAL_DIR / "queries.json"
GROUND_TRUTH_FILE = EVAL_DIR / "ground_truth.json"


# ─── Simple BM25-like keyword scorer ─────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def _bm25_score(query_tokens: list[str], doc_text: str, avg_dl: float, k1: float = 1.5, b: float = 0.75) -> float:
    """Lightweight BM25 approximation for offline offline evaluation."""
    doc_tokens = _tokenize(doc_text)
    dl = len(doc_tokens)
    freq: dict[str, int] = {}
    for t in doc_tokens:
        freq[t] = freq.get(t, 0) + 1
    score = 0.0
    for qt in query_tokens:
        tf = freq.get(qt, 0)
        if tf == 0:
            continue
        idf = math.log(1 + 1.0)  # Simplified: single-corpus IDF approximation
        score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(1, avg_dl)))
    return score


# ─── Metric helpers ───────────────────────────────────────────────────────────
def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    hits = sum(1 for cid in retrieved_ids[:k] if cid in relevant_ids)
    return hits / len(relevant_ids)


def hit_rate_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    return 1.0 if any(cid in relevant_ids for cid in retrieved_ids[:k]) else 0.0


def mrr(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for rank, cid in enumerate(retrieved_ids, start=1):
        if cid in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    def dcg(ids: list[str], rel: set[str], k: int) -> float:
        return sum(
            (1.0 / math.log2(i + 2)) for i, cid in enumerate(ids[:k]) if cid in rel
        )
    ideal = sorted([1 if cid in relevant_ids else 0 for cid in retrieved_ids[:k]], reverse=True)
    idcg = sum((1.0 / math.log2(i + 2)) for i, v in enumerate(ideal) if v)
    return dcg(retrieved_ids, relevant_ids, k) / idcg if idcg > 0 else 0.0


# ─── Offline BM25 retriever against chunks.json ───────────────────────────────
def offline_retrieve(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int,
    source_filter: str | None = None,
) -> list[str]:
    if source_filter:
        pool = [c for c in chunks if source_filter.lower() in c.get("source_name", "").lower()]
    else:
        pool = chunks

    avg_dl = sum(len(_tokenize(c.get("raw_text") or c.get("content", ""))) for c in pool) / max(1, len(pool))
    query_tokens = _tokenize(query)

    scored = [
        (c.get("chunk_id") or c.get("id", ""), _bm25_score(query_tokens, c.get("raw_text") or c.get("content", ""), avg_dl))
        for c in pool
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:top_k]]


# ─── Main evaluation loop ─────────────────────────────────────────────────────
def evaluate(
    top_k_values: list[int] = (5, 10, 20),
    source_filter: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    if not CHUNKS_FILE.exists():
        log.error(f"chunks.json not found at {CHUNKS_FILE}")
        sys.exit(1)

    with CHUNKS_FILE.open(encoding="utf-8") as f:
        chunks: list[dict] = json.load(f)

    if not QUERIES_FILE.exists() or not GROUND_TRUTH_FILE.exists():
        log.error(f"Missing evaluation files. Expected:\n  {QUERIES_FILE}\n  {GROUND_TRUTH_FILE}")
        sys.exit(1)

    with QUERIES_FILE.open(encoding="utf-8") as f:
        queries: list[dict] = json.load(f)
    with GROUND_TRUTH_FILE.open(encoding="utf-8") as f:
        ground_truth: dict[str, list[str]] = json.load(f)

    # ── Corpus stats header ───────────────────────────────────────────────────
    by_source = Counter(c.get("source_name", "unknown") for c in chunks)
    by_type   = Counter(c.get("source_type", "unknown") for c in chunks)
    total_tokens = sum(c.get("token_count", 0) for c in chunks)
    n_evaluated = sum(1 for q in queries if ground_truth.get(q["id"]))

    print("\n" + "=" * 60)
    print(f"  CORPUS: {len(chunks):,} chunks | {total_tokens:,} tokens")
    print(f"  Types:  {dict(by_type)}")
    for src, cnt in by_source.most_common(5):
        print(f"    {src:<40} {cnt:>5} chunks")
    print(f"  Queries: {len(queries)} total, {n_evaluated} with ground-truth")
    print("=" * 60)

    results_by_k: dict[int, dict[str, list[float]]] = {
        k: {"recall": [], "hit_rate": [], "mrr": [], "ndcg": []} for k in top_k_values
    }
    per_query: list[dict] = []

    max_k = max(top_k_values)
    for q_entry in queries:
        qid = q_entry["id"]
        query_text = q_entry["query"]
        relevant_ids = set(ground_truth.get(qid, []))
        if not relevant_ids:
            log.warning(f"Query {qid!r} has no ground-truth entries — skipping.")
            continue

        retrieved = offline_retrieve(query_text, chunks, top_k=max_k, source_filter=source_filter)

        q_metrics: dict[str, Any] = {"id": qid, "query": query_text[:60]}
        for k in top_k_values:
            rc = recall_at_k(retrieved, relevant_ids, k)
            hr = hit_rate_at_k(retrieved, relevant_ids, k)
            results_by_k[k]["recall"].append(rc)
            results_by_k[k]["hit_rate"].append(hr)
            results_by_k[k]["mrr"].append(mrr(retrieved, relevant_ids))
            results_by_k[k]["ndcg"].append(ndcg_at_k(retrieved, relevant_ids, k))
            q_metrics[f"hit@{k}"] = int(hr)
        per_query.append(q_metrics)

    # ── Aggregate & print ─────────────────────────────────────────────────────
    summary: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "corpus_chunks": len(chunks),
        "queries_evaluated": n_evaluated,
    }

    print(f"\n  RETRIEVAL BENCHMARK — {n_evaluated} queries evaluated")
    print("=" * 60)
    for k in top_k_values:
        m = results_by_k[k]
        n = len(m["recall"])
        if n == 0:
            print(f"\n  @K={k}  (no evaluated queries)")
            continue
        agg = {
            "Recall":  sum(m["recall"])   / n,
            "HitRate": sum(m["hit_rate"]) / n,
            "MRR":     sum(m["mrr"])      / n,
            "nDCG":    sum(m["ndcg"])     / n,
        }
        summary[f"@{k}"] = agg
        print(f"\n  @K={k}")
        for metric, val in agg.items():
            bar = "█" * int(val * 20)
            print(f"    {metric:<12}: {val:.4f}  {bar}")
    print("=" * 60)

    if verbose and per_query:
        print("\n  Per-query hit rate (@5 / @10 / @20):")
        for row in per_query:
            hits = " / ".join(str(row.get(f"hit@{k}", "?")) for k in top_k_values)
            print(f"    {row['id']}  [{hits}]  {row['query']}")
        print()

    # Persist results for trend tracking
    results_path = EVAL_DIR / "results_latest.json"
    with results_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    log.info(f"Results saved → {results_path}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DepthAPI retrieval benchmark")
    parser.add_argument("--top-k", nargs="+", type=int, default=[5, 10, 20])
    parser.add_argument("--source-filter", type=str, default=None, help="Filter chunks by source_name substring")
    parser.add_argument("--verbose", action="store_true", help="Print per-query hit rate rows")
    args = parser.parse_args()
    evaluate(top_k_values=args.top_k, source_filter=args.source_filter, verbose=args.verbose)
