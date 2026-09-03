"""
filesystem_rag_store.py — High-performance local RAG using FAISS + BM25.
Designed for the DepthAPI Developer Vertical MVP.
"""

import json
import os
import pickle
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import structlog
from filelock import FileLock
from rank_bm25 import BM25Okapi

from api.services.rag.context_processing import canonical_id, rough_token_count
from api.services.rag.reranker import get_reranker_service

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False

logger = structlog.get_logger(__name__)


def _load_faiss() -> Any:
    """Import faiss only when filesystem RAG operations need it."""
    import faiss

    return faiss

@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: Optional[str]
    content: str
    source_name: str
    source_url: Optional[str]
    chunk_order: int
    section_title: Optional[str]
    token_count: int
    rrf_score: float
    vector_similarity: float
    namespace: str
    rerank_score: Optional[float] = None
    rerank_delta: Optional[int] = None
    metadata: Dict[str, Any] | None = None

class FilesystemRAGStore:
    def __init__(self, base_path: str = "data/rag"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # Cache for loaded namespaces: {namespace: {"index": faiss_index, "bm25": bm25_obj, "chunks": list}}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.last_retrieval_timings: dict[str, float | None] = {}

    def _get_ns_paths(self, namespace: str) -> Dict[str, Path]:
        ns_dir = self.base_path / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return {
            "dir": ns_dir,
            "chunks": ns_dir / "chunks.json",
            "vectors": ns_dir / "vectors.faiss",
            "bm25": ns_dir / "bm25.pkl",
            "manifest": ns_dir / "manifest.json",
            "lock": ns_dir / "store.lock",
        }

    async def ingest(
        self,
        namespace: str,
        chunks: List[str],
        embeddings: List[List[float]],
        metadata: List[Dict[str, Any]],
    ) -> int:
        """
        Ingest chunks and embeddings into a namespace.
        Rebuilds BM25 and updates FAISS index.
        """
        paths = self._get_ns_paths(namespace)
        
        with FileLock(str(paths["lock"])):
            # 1. Load existing chunks
            existing_chunks = []
            if paths["chunks"].exists():
                with open(paths["chunks"], "r", encoding="utf-8") as f:
                    existing_chunks = json.load(f)

            # 2. Append new chunks
            new_chunks_data = []
            for i, (content, vector, meta) in enumerate(zip(chunks, embeddings, metadata)):
                chunk_id = hashlib.sha256(content.encode("utf-8")).hexdigest()
                new_chunks_data.append({
                    "id": meta.get("chunk_id") or chunk_id,
                    "content_hash": chunk_id,
                    "content": content,
                    "source_name": meta.get("source_name", "Unknown"),
                    "source_url": meta.get("source_url"),
                    "chunk_order": meta.get("chunk_order", i),
                    "token_count": int(meta.get("token_count") or rough_token_count(content)),
                    "document_id": meta.get("document_id") or meta.get("doc_id"),
                    "doc_id": meta.get("doc_id") or meta.get("document_id"),
                    "section_title": meta.get("section_title", ""),
                    "embedding": vector,
                    "metadata": {
                        **dict(meta.get("metadata") or {}),
                        "doc_id": meta.get("doc_id") or meta.get("document_id"),
                        "chunk_id": meta.get("chunk_id") or chunk_id,
                        "section_title": meta.get("section_title", ""),
                        "token_count": int(meta.get("token_count") or rough_token_count(content)),
                        "chunking_version": meta.get("chunking_version", "v3-semantic-local"),
                    },
                })
            
            all_chunks = existing_chunks + new_chunks_data
            
            # 3. Update FAISS Index
            # Use METRIC_INNER_PRODUCT so that dot product on L2-normalised vectors
            # equals cosine similarity exactly (values in [-1, 1]).
            dim = len(embeddings[0]) if embeddings else 768
            faiss = _load_faiss()
            if paths["vectors"].exists():
                index = faiss.read_index(str(paths["vectors"]))
            else:
                index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
                index.hnsw.efConstruction = 200

            # L2-normalise before adding so stored vectors are unit vectors.
            vecs = np.array(embeddings, dtype="float32")
            faiss.normalize_L2(vecs)
            index.add(vecs)
            
            # 4. Rebuild BM25 (always from scratch for consistency)
            tokenized_corpus = [c["content"].lower().split() for c in all_chunks]
            bm25 = BM25Okapi(tokenized_corpus)
            
            # 5. Save all
            with open(paths["chunks"], "w", encoding="utf-8") as f:
                json.dump(all_chunks, f, indent=2)
            
            faiss.write_index(index, str(paths["vectors"]))
            
            with open(paths["bm25"], "wb") as f:
                pickle.dump(bm25, f)
            
            # Update manifest
            manifest = {
                "total_chunks": len(all_chunks),
                "last_updated": Path(paths["chunks"]).stat().st_mtime,
                "dim": dim
            }
            with open(paths["manifest"], "w") as f:
                json.dump(manifest, f)

            # Invalidate cache
            if namespace in self._cache:
                del self._cache[namespace]
            
            return len(all_chunks)

    def load_namespace(self, namespace: str):
        """Lazy load index and chunks into memory."""
        if namespace in self._cache:
            return

        paths = self._get_ns_paths(namespace)
        if paths["chunks"].exists() and (not paths["vectors"].exists() or not paths["bm25"].exists()):
            self._bootstrap_indices_from_chunks(namespace)

        if not paths["chunks"].exists() or not paths["vectors"].exists() or not paths["bm25"].exists():
            logger.warning("rag_namespace_not_found", namespace=namespace)
            return

        logger.info("loading_rag_namespace", namespace=namespace)
        
        with open(paths["chunks"], "r", encoding="utf-8") as f:
            chunks = json.load(f)
        
        faiss = _load_faiss()
        index = faiss.read_index(str(paths["vectors"]))
        
        with open(paths["bm25"], "rb") as f:
            bm25 = pickle.load(f)
            
        self._cache[namespace] = {
            "chunks": chunks,
            "index": index,
            "bm25": bm25
        }

    def _bootstrap_indices_from_chunks(self, namespace: str) -> None:
        paths = self._get_ns_paths(namespace)
        if not paths["chunks"].exists():
            return

        with FileLock(str(paths["lock"])):
            with open(paths["chunks"], "r", encoding="utf-8") as f:
                chunks = json.load(f)

            if not isinstance(chunks, list) or not chunks:
                logger.warning("rag_bootstrap_empty_chunks", namespace=namespace)
                return

            embeddings: list[list[float]] = []
            normalized_chunks: list[dict[str, Any]] = []
            for idx, chunk in enumerate(chunks):
                embedding = chunk.get("embedding")
                content = chunk.get("content")
                if not isinstance(embedding, list) or not embedding or not isinstance(content, str) or not content.strip():
                    continue
                embeddings.append([float(value) for value in embedding])
                normalized_chunks.append(
                    {
                        "id": chunk.get("id") or hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "content": content,
                        "source_name": chunk.get("source_name", "Unknown"),
                        "source_url": chunk.get("source_url"),
                        "chunk_order": int(chunk.get("chunk_order", idx) or idx),
                        "token_count": int(chunk.get("token_count", 0) or 0),
                        "document_id": chunk.get("document_id") or chunk.get("doc_id"),
                        "doc_id": chunk.get("doc_id") or chunk.get("document_id"),
                        "section_title": chunk.get("section_title", ""),
                        "embedding": embedding,
                        "metadata": chunk.get("metadata") or {},
                    }
                )

            if not embeddings or not normalized_chunks:
                logger.warning("rag_bootstrap_missing_embeddings", namespace=namespace)
                return

            dim = len(embeddings[0])
            faiss = _load_faiss()
            index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = 200
            vectors = np.array(embeddings, dtype="float32")
            faiss.normalize_L2(vectors)
            index.add(vectors)

            tokenized_corpus = [c["content"].lower().split() for c in normalized_chunks]
            bm25 = BM25Okapi(tokenized_corpus)

            faiss.write_index(index, str(paths["vectors"]))
            with open(paths["bm25"], "wb") as f:
                pickle.dump(bm25, f)
            with open(paths["manifest"], "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "total_chunks": len(normalized_chunks),
                        "last_updated": Path(paths["chunks"]).stat().st_mtime,
                        "dim": dim,
                        "bootstrapped_from_chunks": True,
                    },
                    f,
                )

            logger.info(
                "rag_bootstrap_completed",
                namespace=namespace,
                total_chunks=len(normalized_chunks),
                dim=dim,
            )

    async def retrieve(
        self,
        query_embedding: List[float],
        query_text: str,
        namespaces: List[str],
        top_k: int = 5,
        min_similarity: float = 0.65,
    ) -> List[RetrievalResult]:
        """Hybrid search across namespaces, then MMR and cross-encoder reranking."""
        retrieval_started = time.perf_counter()
        all_results: List[RetrievalResult] = []
        candidate_pool = max(top_k * 4, int(os.getenv("RAG_CANDIDATE_POOL", "20")))
        
        for ns in namespaces:
            self.load_namespace(ns)
            if ns not in self._cache:
                continue
            
            data = self._cache[ns]
            chunks = data["chunks"]
            index = data["index"]
            bm25 = data["bm25"]
            
            # 1. Vector Search (FAISS — METRIC_INNER_PRODUCT on L2-normalised vectors)
            # D values are cosine similarities in [-1, 1]; higher is more similar.
            faiss = _load_faiss()
            xq = np.array([query_embedding], dtype="float32")
            faiss.normalize_L2(xq)

            D, I = index.search(xq, candidate_pool)

            # 2. Keyword Search (BM25)
            tokenized_query = query_text.lower().split()
            bm25_scores = bm25.get_scores(tokenized_query)
            bm25_top_indices = np.argsort(bm25_scores)[::-1][:candidate_pool]

            # 3. RRF Fusion
            scores_by_idx: dict[int, float] | None = None
            if _HAS_DEPTH_ENGINE:
                try:
                    dense_ids = [str(int(idx)) for idx in I[0] if idx != -1]
                    lex_ids = [str(int(idx)) for idx in bm25_top_indices]
                    fused = depth_engine.fuse_rrf(dense_ids, lex_ids, 60.0)
                    scores_by_idx = {int(doc_id): score for doc_id, score in fused}
                except Exception:
                    scores_by_idx = None

            vector_ranks = {int(idx): rank for rank, idx in enumerate(I[0]) if idx != -1}
            bm25_ranks = {int(idx): rank for rank, idx in enumerate(bm25_top_indices)}

            k = 60  # RRF constant
            combined_indices = set(scores_by_idx.keys()) if scores_by_idx is not None else (set(vector_ranks.keys()) | set(bm25_ranks.keys()))

            # Build a fast lookup from FAISS result arrays.
            faiss_idx_to_score: dict[int, float] = {
                int(I[0][rank]): float(D[0][rank])
                for rank in range(len(I[0]))
                if I[0][rank] != -1
            }

            ns_results = []
            for idx in combined_indices:
                b_rank = bm25_ranks.get(idx, 1e6)
                if scores_by_idx is not None and idx in scores_by_idx:
                    score = scores_by_idx[idx]
                else:
                    v_rank = vector_ranks.get(idx, 1e6)
                    score = (1.0 / (k + v_rank)) + (1.0 / (k + b_rank))

                # D is a cosine similarity in [-1, 1]; use it directly.
                similarity = faiss_idx_to_score.get(idx, -1.0)

                if similarity >= min_similarity or b_rank < top_k:
                    chunk = chunks[idx]
                    ns_results.append(RetrievalResult(
                        chunk_id=chunk["id"],
                        document_id=chunk.get("document_id") or chunk.get("doc_id"),
                        content=chunk["content"],
                        source_name=chunk["source_name"],
                        source_url=chunk.get("source_url"),
                        chunk_order=chunk["chunk_order"],
                        section_title=chunk.get("section_title"),
                        token_count=int(chunk.get("token_count") or rough_token_count(chunk.get("content", ""))),
                        rrf_score=score,
                        vector_similarity=float(similarity),
                        namespace=ns,
                        metadata={**dict(chunk.get("metadata") or {}), "embedding": chunk.get("embedding")},
                    ))
            
            all_results.extend(ns_results)

        # Final sort by RRF score, then diversify and rerank.
        all_results.sort(key=lambda x: x.rrf_score, reverse=True)
        deduped = self._dedupe_results(all_results)
        diversified = self._apply_mmr(deduped, query_embedding=query_embedding, top_n=min(candidate_pool, len(deduped)))
        reranked, rerank_ms = await self._rerank_results(query_text, diversified, final_k=top_k)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        self.last_retrieval_timings = {
            "retrieval_latency_ms": round(retrieval_ms, 2),
            "rerank_latency_ms": rerank_ms,
        }
        logger.info(
            "filesystem_retrieval_completed",
            candidates=len(all_results),
            deduped=len(deduped),
            diversified=len(diversified),
            selected=len(reranked),
            retrieval_ms=round(retrieval_ms, 2),
        )
        return reranked[:top_k]

    def _dedupe_results(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        deduped: dict[str, RetrievalResult] = {}
        for result in results:
            key = result.chunk_id or hashlib.sha256(result.content.encode("utf-8")).hexdigest()
            current = deduped.get(key)
            if current is None or result.rrf_score > current.rrf_score:
                deduped[key] = result
        return list(deduped.values())

    def _embedding_for_result(self, result: RetrievalResult) -> np.ndarray | None:
        metadata = result.metadata or {}
        embedding = metadata.get("embedding")
        if embedding is None:
            embedding = metadata.get("_embedding")
        if embedding is None:
            return None
        try:
            vec = np.array(embedding, dtype="float32")
            norm = np.linalg.norm(vec)
            return vec / norm if norm else vec
        except Exception:
            return None

    def _text_similarity(self, left: str, right: str) -> float:
        left_terms = set(str(left or "").lower().split())
        right_terms = set(str(right or "").lower().split())
        if not left_terms or not right_terms:
            return 0.0
        return len(left_terms & right_terms) / len(left_terms | right_terms)

    def _apply_mmr(
        self,
        results: list[RetrievalResult],
        *,
        query_embedding: list[float],
        top_n: int,
        lambda_mult: float = 0.65,
    ) -> list[RetrievalResult]:
        if len(results) <= 1:
            return results
        selected: list[RetrievalResult] = []
        remaining = list(results)
        query_vec = np.array(query_embedding, dtype="float32")
        query_norm = np.linalg.norm(query_vec)
        if query_norm:
            query_vec = query_vec / query_norm

        while remaining and len(selected) < top_n:
            best_idx = 0
            best_score = float("-inf")
            for idx, candidate in enumerate(remaining):
                cand_vec = self._embedding_for_result(candidate)
                relevance = candidate.rrf_score
                if cand_vec is not None and query_vec.size == cand_vec.size:
                    relevance = float(np.dot(query_vec, cand_vec))
                max_diversity_penalty = 0.0
                for prior in selected:
                    prior_vec = self._embedding_for_result(prior)
                    if cand_vec is not None and prior_vec is not None and cand_vec.size == prior_vec.size:
                        similarity = float(np.dot(cand_vec, prior_vec))
                    else:
                        similarity = self._text_similarity(candidate.content, prior.content)
                    same_doc_penalty = 0.15 if canonical_id(candidate.document_id) == canonical_id(prior.document_id) else 0.0
                    max_diversity_penalty = max(max_diversity_penalty, similarity + same_doc_penalty)
                mmr_score = lambda_mult * relevance - (1.0 - lambda_mult) * max_diversity_penalty
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx
            selected.append(remaining.pop(best_idx))
        return selected

    async def _rerank_results(
        self,
        query_text: str,
        results: list[RetrievalResult],
        *,
        final_k: int,
    ) -> tuple[list[RetrievalResult], float | None]:
        if len(results) <= 1 or os.getenv("RAG_DISABLE_RERANK", "0") == "1":
            return results[:final_k], None
        original_positions = {result.chunk_id: idx for idx, result in enumerate(results)}
        candidates = [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "_result": result,
                "rrf_score": result.rrf_score,
                "vector_similarity": result.vector_similarity,
            }
            for result in results
        ]
        try:
            rerank_started = time.perf_counter()
            reranker = get_reranker_service()
            ranked = await reranker.rerank(query_text, candidates, top_n=len(candidates))
            rerank_ms = (time.perf_counter() - rerank_started) * 1000
            out: list[RetrievalResult] = []
            for idx, candidate in enumerate(ranked):
                result = candidate["_result"]
                result.rerank_score = float(candidate.get("rerank_score", 0.0))
                result.rerank_delta = original_positions.get(result.chunk_id, idx) - idx
                out.append(result)
            logger.info("filesystem_rerank_completed", candidates=len(candidates), rerank_ms=round(rerank_ms, 2))
            return out[:final_k], round(rerank_ms, 2)
        except Exception as exc:
            logger.warning("filesystem_rerank_failed_fallback", error=str(exc))
            return results[:final_k], None
