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
import asyncio
import json
import math
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

# Ensure repository root is in python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.services.rag.embeddings import DEFAULT_LOCAL_MODEL, embed_texts, get_local_transformer
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges


class _PplxContextEmbedder:
    """Adapter for perplexity-ai/pplx-embed-v2-context-* (transformers custom code).

    Queries go through `encode_queries`, documents through `encode` (each as a
    single-chunk list). The model does not truncate, so inputs are cut to
    `max_seq_length` tokens here.
    """

    def __init__(self, model_name: str, max_seq_length: int | None = 512):
        import torch
        from transformers import AutoModel, AutoTokenizer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = AutoModel.from_pretrained(model_name, trust_remote_code=True).to(device).eval()
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        except Exception:
            self.tokenizer = None
        self.max_seq_length = max_seq_length

    def half(self) -> None:
        self.model.half()

    def _truncate(self, text: str) -> str:
        limit = self.max_seq_length
        if not limit:
            return text
        if self.tokenizer is None:
            return text[: limit * 4]
        ids = self.tokenizer(text, add_special_tokens=False, truncation=True, max_length=limit)["input_ids"]
        return self.tokenizer.decode(ids) if len(ids) >= limit else text

    def encode(self, texts: list[str], batch_size: int = 16, show_progress_bar: bool = False,
               normalize_embeddings: bool = True, is_query: bool = False) -> np.ndarray:
        import torch

        chunks = [[self._truncate(t) or " "] for t in texts]
        fn = self.model.encode_queries if is_query else self.model.encode
        with torch.inference_mode():
            out = fn(chunks, batch_size=batch_size, normalize_embeddings=normalize_embeddings,
                     show_progress_bar=show_progress_bar)
        return np.stack([np.asarray(o, dtype=np.float32)[0] for o in out])


def _encode_texts(
    texts: list[str],
    model: Any | None,
    batch_size: int = 32,
    dim: int | None = None,
    is_query: bool = False,
) -> np.ndarray:
    """Embed texts using local SentenceTransformer when available, else API/hash embed_texts."""
    if model is not None:
        kwargs = {"is_query": is_query} if isinstance(model, _PplxContextEmbedder) else {}
        embs = model.encode(
            texts, batch_size=batch_size, show_progress_bar=len(texts) > 32, normalize_embeddings=True, **kwargs
        )
        arr = np.array(embs, dtype=np.float32)
    else:
        vectors: list[list[float]] = []

        async def _run() -> None:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                literals = await embed_texts(batch)
                vectors.extend(json.loads(lit) for lit in literals)

        asyncio.run(_run())
        arr = np.array(vectors, dtype=np.float32)

    if dim is not None and arr.shape[1] > dim:
        arr = arr[:, :dim]
        norms = np.linalg.norm(arr, axis=-1, keepdims=True)
        arr = arr / np.where(norms == 0, 1.0, norms)
    return arr

DATASET_URLS = {
    "scifact": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
    "nfcorpus": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip",
    "fiqa": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip",
    "hotpotqa": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/hotpotqa.zip",
    "arguana": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/arguana.zip",
    "quora": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/quora.zip",
    "scidocs": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scidocs.zip",
    "trec-covid": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/trec-covid.zip",
    "webis-touche2020": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/webis-touche2020.zip",
    "fever": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fever.zip",
    "climate-fever": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/climate-fever.zip",
    "dbpedia-entity": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/dbpedia-entity.zip",
    "nq": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nq.zip",
    "msmarco": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/msmarco.zip",
    "cqadupstack": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/cqadupstack.zip",
}


def _download_from_huggingface(dataset_name: str, target_dir: Path) -> Path:
    """Fallback downloader using HuggingFace BeIR/{dataset} parquet + BeIR/{dataset}-qrels."""
    import pandas as pd
    from huggingface_hub import hf_hub_download, list_repo_files

    print(f"Downloading {dataset_name} from HuggingFace (BeIR/{dataset_name})...")
    repo_id = f"BeIR/{dataset_name}"
    qrels_repo = f"BeIR/{dataset_name}-qrels"

    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "qrels").mkdir(parents=True, exist_ok=True)

    files = list_repo_files(repo_id, repo_type="dataset")
    corpus_files = sorted(f for f in files if f.startswith("corpus/") and f.endswith(".parquet"))
    query_files = sorted(f for f in files if f.startswith("queries/") and f.endswith(".parquet"))
    if not corpus_files or not query_files:
        raise RuntimeError(f"Could not find parquet corpus/queries in {repo_id}")

    with open(target_dir / "corpus.jsonl", "w", encoding="utf-8") as out_c:
        for cf in corpus_files:
            local_p = hf_hub_download(repo_id, cf, repo_type="dataset")
            df = pd.read_parquet(local_p)
            for row in df.to_dict(orient="records"):
                out_c.write(json.dumps({
                    "_id": str(row.get("_id", "")),
                    "title": str(row.get("title") or ""),
                    "text": str(row.get("text") or ""),
                }) + "\n")

    with open(target_dir / "queries.jsonl", "w", encoding="utf-8") as out_q:
        for qf in query_files:
            local_p = hf_hub_download(repo_id, qf, repo_type="dataset")
            df = pd.read_parquet(local_p)
            for row in df.to_dict(orient="records"):
                out_q.write(json.dumps({
                    "_id": str(row.get("_id", "")),
                    "text": str(row.get("text") or ""),
                }) + "\n")

    qrel_files = list_repo_files(qrels_repo, repo_type="dataset")
    qrel_name = "test.tsv" if "test.tsv" in qrel_files else ("dev.tsv" if "dev.tsv" in qrel_files else None)
    if qrel_name is None:
        raise RuntimeError(f"No test.tsv or dev.tsv found in {qrels_repo}")
    qrel_p = hf_hub_download(qrels_repo, qrel_name, repo_type="dataset")
    (target_dir / "qrels" / "test.tsv").write_bytes(Path(qrel_p).read_bytes())
    print(f"Prepared {dataset_name} from HuggingFace at {target_dir}.")
    return target_dir


def download_and_extract_dataset(dataset_name: str, cache_dir: Path) -> Path:
    target_dir = cache_dir / dataset_name
    if (target_dir / "corpus.jsonl").exists() and (
        (target_dir / "qrels" / "test.tsv").exists() or (target_dir / "qrels" / "dev.tsv").exists()
    ):
        return target_dir

    if dataset_name not in DATASET_URLS:
        raise ValueError(f"Unknown dataset: {dataset_name}. Available: {list(DATASET_URLS.keys())}")

    url = DATASET_URLS[dataset_name]
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{dataset_name}.zip"

    if zip_path.exists() and not zipfile.is_zipfile(zip_path):
        zip_path.unlink()

    if not zip_path.exists():
        print(f"Downloading {dataset_name} from {url}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DepthAPI-Benchmark/1.0"})
            tmp_path = zip_path.with_suffix(".zip.part")
            with urllib.request.urlopen(req, timeout=15) as resp, open(tmp_path, "wb") as f_out:
                downloaded = 0
                while True:
                    chunk = resp.read(2 * 1024 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (20 * 1024 * 1024) == 0:
                        print(f"Downloaded {downloaded // (1024 * 1024)} MB...")
            tmp_path.replace(zip_path)
            print(f"Finished downloading {dataset_name} ({downloaded // (1024 * 1024)} MB).")
        except Exception as exc:
            print(f"Primary mirror failed ({exc}); falling back to HuggingFace...")
            return _download_from_huggingface(dataset_name, target_dir)

    print(f"Extracting {zip_path} to {cache_dir}...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(cache_dir)

    return target_dir


def load_beir_data(
    data_dir: Path, limit_docs: int | None = None, limit_queries: int | None = None
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, int]], dict[str, str]]:
    """Load queries with ground truth and their relevant docs + distractors."""
    # 1. Load qrels
    all_qrels: dict[str, dict[str, int]] = {}
    qrels_path = data_dir / "qrels" / "test.tsv"
    if not qrels_path.exists():
        qrels_path = data_dir / "qrels" / "dev.tsv"  # msmarco ships dev qrels only
    with open(qrels_path, "r", encoding="utf-8") as f:
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
            if all_qrels.get(qid):
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
    """Build lightweight token-based BM25 index.

    Uses bm25s' regex word tokenizer with English stopwords, plus Porter-style
    stemming when PyStemmer is installed (closer to published BM25 baselines
    than whitespace splitting).
    """
    import bm25s

    doc_ids = list(corpus.keys())
    bm25 = bm25s.BM25()
    bm25.index(bm25_tokenize([corpus[did] for did in doc_ids]))
    return bm25, doc_ids


def bm25_tokenize(texts: list[str]) -> list[list[str]]:
    import bm25s

    try:
        import Stemmer

        stemmer = Stemmer.Stemmer("english")
    except ImportError:
        stemmer = None
    return bm25s.tokenize(texts, stopwords="en", stemmer=stemmer, return_ids=False, show_progress=False)


def evaluate(
    corpus: dict[str, str],
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    titles: dict[str, str] | None = None,
    cache_path: Path | None = None,
    enable_rerank: bool = False,
    model_name: str | None = None,
    dim: int | None = None,
    fp16: bool = False,
    skip_graph: bool = False,
    batch_size: int = 16,
    max_seq_length: int | None = None,
    dense_weight: float = 1.5,
    lexical_weight: float = 0.5,
    rerank_top_k: int = 25,
) -> dict[str, Any]:
    from api.config import get_settings

    resolved_model = model_name or get_settings().embedding_model
    print(f"\nIndexing {len(corpus)} documents with {resolved_model}...")
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

    if "pplx-embed" in resolved_model:
        model = _PplxContextEmbedder(resolved_model, max_seq_length)
    else:
        model = get_local_transformer(resolved_model)
    if model is None and model_name:
        raise RuntimeError(f"Could not load requested --model {model_name!r}; refusing to fall back silently.")
    if (
        model is None
        and get_settings().embedding_provider.lower() == "local"
        and get_local_transformer(DEFAULT_LOCAL_MODEL) is None
    ):
        raise RuntimeError(
            "No local embedding model available (is sentence-transformers installed?); "
            "refusing to benchmark with hash embeddings."
        )
    if model is not None and max_seq_length is not None:
        model.max_seq_length = max_seq_length
    if model is not None and fp16:
        try:
            import torch

            if torch.cuda.is_available():
                model.half()
        except Exception:
            pass
    if corpus_emb_matrix is None:
        corpus_texts = [corpus[did] for did in doc_ids]
        t0 = time.perf_counter()
        corpus_emb_matrix = _encode_texts(corpus_texts, model, batch_size=batch_size, dim=dim)
        if fp16:
            corpus_emb_matrix = corpus_emb_matrix.astype(np.float16)
        mode_label = "local SentenceTransformer" if model is not None else "API/fallback embed_texts"
        print(f"Corpus embedded via {mode_label} in {time.perf_counter() - t0:.2f}s (Shape: {corpus_emb_matrix.shape})")
        if cache_path:
            np.savez_compressed(cache_path, doc_ids=doc_ids, embeddings=corpus_emb_matrix)
            print(f"Saved embedding cache to {cache_path}")
    elif fp16 and corpus_emb_matrix.dtype != np.float16:
        corpus_emb_matrix = corpus_emb_matrix.astype(np.float16)

    print("Building lexical BM25 index...")
    bm25, bm25_doc_ids = build_lexical_index(corpus)

    doc_concepts: dict[str, set[str]] = defaultdict(set)
    concept_docs: dict[str, set[str]] = defaultdict(set)
    concept_edges: dict[str, set[str]] = defaultdict(set)

    if not skip_graph:
        print("Extracting relational concept graph from corpus...")
        # Avoid O(N^2) substring scans on large corpora
        raw_entities = [t for t in (titles or {}).values() if len(t) >= 3]
        entity_catalog = raw_entities if len(raw_entities) <= 500 else None

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

    strategies = ["dense", "lexical", "hybrid_rrf"]
    if not skip_graph:
        strategies.extend(["hybrid_graph_1hop", "hybrid_graph_2hop"])
    if enable_rerank:
        strategies.append("hybrid_rrf_rerank")

    metrics_by_strategy: dict[str, list[dict[str, float]]] = {s: [] for s in strategies}
    latencies_by_strategy: dict[str, list[float]] = {s: [] for s in strategies}

    print(f"\nEvaluating {len(queries)} queries across strategies: {strategies}...")

    for qid, query_text in queries.items():
        truth = qrels[qid]

        # 1. Dense retrieval
        t_start = time.perf_counter()
        q_emb = _encode_texts([query_text], model, batch_size=1, dim=dim, is_query=True)[0]
        # Cosine similarity is dot product because vectors are normalized
        dense_scores = np.dot(corpus_emb_matrix, q_emb)
        dense_top_indices = np.argsort(-dense_scores)[:40]
        dense_top_ids = [doc_ids[idx] for idx in dense_top_indices]
        latencies_by_strategy["dense"].append(time.perf_counter() - t_start)
        metrics_by_strategy["dense"].append(compute_metrics(dense_top_ids, truth))

        # 2. Lexical retrieval
        t_start = time.perf_counter()
        tokenized_query = bm25_tokenize([query_text])[0]
        lex_scores = bm25.get_scores(tokenized_query)
        lex_top_indices = np.argsort(-lex_scores)[:40]
        lex_top_ids = [bm25_doc_ids[idx] for idx in lex_top_indices]
        latencies_by_strategy["lexical"].append(time.perf_counter() - t_start)
        metrics_by_strategy["lexical"].append(compute_metrics(lex_top_ids, truth))

        # 3. Weighted Hybrid RRF (k=60)
        t_start = time.perf_counter()
        rrf_scores: dict[str, float] = defaultdict(float)
        for rank, did in enumerate(dense_top_ids):
            rrf_scores[did] += dense_weight / (60.0 + rank + 1)
        for rank, did in enumerate(lex_top_ids):
            rrf_scores[did] += lexical_weight / (60.0 + rank + 1)

        hybrid_ranked = sorted(rrf_scores.keys(), key=lambda d: rrf_scores[d], reverse=True)
        latencies_by_strategy["hybrid_rrf"].append(time.perf_counter() - t_start)
        metrics_by_strategy["hybrid_rrf"].append(compute_metrics(hybrid_ranked, truth))

        # 4. Hybrid Graph 1-hop & 2-hop
        if not skip_graph:
            max_concept_df = max(50, len(corpus) // 20)
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
                target_docs = concept_docs[c]
                if len(target_docs) > max_concept_df:
                    continue
                for target_did in target_docs:
                    graph_1hop_scores[target_did] = graph_1hop_scores.get(target_did, 0.0) + (1.0 / (60.0 + 1)) * 0.25

            ranked_1hop = sorted(graph_1hop_scores.keys(), key=lambda d: graph_1hop_scores[d], reverse=True)
            latencies_by_strategy["hybrid_graph_1hop"].append(time.perf_counter() - t_start)
            metrics_by_strategy["hybrid_graph_1hop"].append(compute_metrics(ranked_1hop, truth))

            t_start = time.perf_counter()
            graph_2hop_scores = dict(graph_1hop_scores)
            traversed_2hop = set()
            for c in traversed_1hop:
                traversed_2hop.update(concept_edges[c])
            traversed_2hop.difference_update(seed_concepts)
            traversed_2hop.difference_update(traversed_1hop)

            for c in traversed_2hop:
                target_docs = concept_docs[c]
                if len(target_docs) > max_concept_df:
                    continue
                for target_did in target_docs:
                    graph_2hop_scores[target_did] = graph_2hop_scores.get(target_did, 0.0) + (1.0 / (60.0 + 2)) * 0.25

            ranked_2hop = sorted(graph_2hop_scores.keys(), key=lambda d: graph_2hop_scores[d], reverse=True)
            latencies_by_strategy["hybrid_graph_2hop"].append(time.perf_counter() - t_start)
            metrics_by_strategy["hybrid_graph_2hop"].append(compute_metrics(ranked_2hop, truth))

        # 6. Hybrid RRF + Reranker (optional)
        if enable_rerank and reranker:
            t_start = time.perf_counter()
            top_candidates = [{"id": did, "content": corpus[did]} for did in hybrid_ranked[:rerank_top_k]]
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
    parser.add_argument(
        "--dataset",
        type=str,
        default="scifact",
        help="Dataset name, comma-separated list (e.g. scifact,fiqa,hotpotqa), or 'all'",
    )
    parser.add_argument("--limit-docs", type=int, default=1000, help="Max documents (0 for full)")
    parser.add_argument("--limit-queries", type=int, default=100, help="Max queries (0 for full)")
    parser.add_argument("--rerank", action="store_true", help="Include cross-encoder reranker")
    parser.add_argument("--model", type=str, default=None, help="Embedding model name (defaults to EMBEDDING_MODEL setting)")
    parser.add_argument("--dim", type=int, default=None, help="Optional Matryoshka truncation dimension (e.g. 256)")
    parser.add_argument("--batch-size", type=int, default=16, help="Embedding batch size")
    parser.add_argument("--max-seq-length", type=int, default=512, help="Max token length per doc/query (0 for model default)")
    parser.add_argument("--fp16", action="store_true", help="Store corpus embedding matrix in float16 to halve RAM")
    parser.add_argument("--skip-graph", action="store_true", help="Skip concept graph extraction for faster large-corpus runs")
    parser.add_argument("--dense-weight", type=float, default=1.5, help="RRF weight for dense retrieval results")
    parser.add_argument("--lexical-weight", type=float, default=0.5, help="RRF weight for lexical BM25 results")
    parser.add_argument("--rerank-top-k", type=int, default=25, help="Number of RRF candidates passed to CrossEncoder reranker")
    parser.add_argument("--output", type=str, default="datasets/benchmarks/results.json")
    args = parser.parse_args()

    cache_dir = REPO_ROOT / "datasets" / "benchmarks" / "beir_cache"

    if args.dataset.strip().lower() == "all":
        datasets_to_run = list(DATASET_URLS)
    else:
        datasets_to_run = [d.strip() for d in args.dataset.split(",") if d.strip()]

    out_path = REPO_ROOT / args.output
    all_manifest = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                all_manifest = existing_data.get("datasets", {})
        except Exception:
            all_manifest = {}

    limit_docs = None if args.limit_docs <= 0 else args.limit_docs
    limit_queries = None if args.limit_queries <= 0 else args.limit_queries

    for ds in datasets_to_run:
        print("\n" + "#" * 65)
        print(f"BENCHMARKING DATASET: {ds.upper()}")
        print("#" * 65)
        try:
            data_dir = download_and_extract_dataset(ds, cache_dir)
            corpus, queries, qrels, titles = load_beir_data(
                data_dir, limit_docs=limit_docs, limit_queries=limit_queries
            )
        except Exception as exc:
            print(f"SKIPPING {ds}: could not download/load ({type(exc).__name__}: {exc})")
            all_manifest[ds] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        print(f"Loaded {len(corpus)} documents and {len(queries)} evaluated test queries.")

        model_tag = (args.model or "default").replace("/", "_")
        dim_tag = f"_{args.dim}d" if args.dim else ""
        cache_path = data_dir / f"embeddings_{len(corpus)}_{model_tag}{dim_tag}.npz"
        summary = evaluate(
            corpus,
            queries,
            qrels,
            titles=titles,
            cache_path=cache_path,
            enable_rerank=args.rerank,
            model_name=args.model,
            dim=args.dim,
            fp16=args.fp16,
            skip_graph=args.skip_graph,
            batch_size=args.batch_size,
            max_seq_length=(None if args.max_seq_length <= 0 else args.max_seq_length),
            dense_weight=args.dense_weight,
            lexical_weight=args.lexical_weight,
            rerank_top_k=args.rerank_top_k,
        )

        print("\n" + "=" * 65)
        print(f"BEIR Benchmark Results: {ds.upper()} (Corpus: {len(corpus)}, Queries: {len(queries)})")
        print("=" * 65)
        print(f"{'Strategy':<20} | {'NDCG@10':<9} | {'Recall@10':<10} | {'MRR@10':<8} | {'p50 (ms)':<9} | {'p95 (ms)'}")
        print("-" * 65)
        for strat, res in summary.items():
            print(
                f"{strat:<20} | {res['ndcg@10']:<9.4f} | {res['recall@10']:<10.4f} | {res['mrr@10']:<8.4f} | {res['p50_latency_ms']:<9.2f} | {res['p95_latency_ms']:.2f}"
            )
        print("=" * 65)

        all_manifest[ds] = {
            "docs_evaluated": len(corpus),
            "queries_evaluated": len(queries),
            "results": summary,
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"datasets": all_manifest}, f, indent=2)

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    if len(datasets_to_run) > 1:
        print("\n" + "=" * 75)
        print("📊 CONSOLIDATED MULTI-DATASET SCOREBOARD (NDCG@10)")
        print("=" * 75)
        header = f"{'Dataset':<12} | {'Lexical BM25':<14} | {'Dense Vector':<14} | {'RRF Hybrid':<16} | {'Reranked':<14}"
        print(header)
        print("-" * 75)
        for ds, data in all_manifest.items():
            if "results" not in data:
                continue
            r = data["results"]
            bm25 = f"{r.get('lexical', {}).get('ndcg@10', 0):.4f}"
            dense = f"{r.get('dense', {}).get('ndcg@10', 0):.4f}"
            rrf = f"{r.get('hybrid_rrf', {}).get('ndcg@10', 0):.4f}"
            rerank = f"{r.get('hybrid_rrf_rerank', {}).get('ndcg@10', 0):.4f}" if args.rerank else "N/A"
            print(f"{ds.upper():<12} | {bm25:<14} | {dense:<14} | {rrf:<16} | {rerank:<14}")
        print("=" * 75)

    out_path = REPO_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "datasets": all_manifest,
            },
            f,
            indent=2,
        )
    print(f"\nManifest and benchmark results written to: {out_path}")


if __name__ == "__main__":
    main()
