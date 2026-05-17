"""
filesystem_rag_store.py — High-performance local RAG using FAISS + BM25.
Designed for the DepthAPI Developer Vertical MVP.
"""

import json
import os
import pickle
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import faiss
import numpy as np
import structlog
from filelock import FileLock
from rank_bm25 import BM25Okapi

logger = structlog.get_logger(__name__)

@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    source_name: str
    source_url: Optional[str]
    chunk_order: int
    rrf_score: float
    vector_similarity: float
    namespace: str

class FilesystemRAGStore:
    def __init__(self, base_path: str = "data/rag"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # Cache for loaded namespaces: {namespace: {"index": faiss_index, "bm25": bm25_obj, "chunks": list}}
        self._cache: Dict[str, Dict[str, Any]] = {}

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
                    "id": chunk_id,
                    "content": content,
                    "source_name": meta.get("source_name", "Unknown"),
                    "source_url": meta.get("source_url"),
                    "chunk_order": meta.get("chunk_order", i),
                    "token_count": meta.get("token_count", 0),
                })
            
            all_chunks = existing_chunks + new_chunks_data
            
            # 3. Update FAISS Index
            dim = len(embeddings[0]) if embeddings else 768
            if paths["vectors"].exists():
                index = faiss.read_index(str(paths["vectors"]))
            else:
                # Use HNSW as specified in MVP_DEV_VERTICAL.md
                index = faiss.IndexHNSWFlat(dim, 32)
                index.hnsw.efConstruction = 200
            
            index.add(np.array(embeddings).astype("float32"))
            
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
                    }
                )

            if not embeddings or not normalized_chunks:
                logger.warning("rag_bootstrap_missing_embeddings", namespace=namespace)
                return

            dim = len(embeddings[0])
            index = faiss.IndexHNSWFlat(dim, 32)
            index.hnsw.efConstruction = 200
            vectors = np.array(embeddings).astype("float32")
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
        """
        Hybrid search across namespaces using RRF.
        """
        all_results: List[RetrievalResult] = []
        
        for ns in namespaces:
            self.load_namespace(ns)
            if ns not in self._cache:
                continue
            
            data = self._cache[ns]
            chunks = data["chunks"]
            index = data["index"]
            bm25 = data["bm25"]
            
            # 1. Vector Search (FAISS)
            xq = np.array([query_embedding]).astype("float32")
            faiss.normalize_L2(xq) # Assuming cosine similarity if index is inner product, 
                                   # but IndexHNSWFlat uses L2 distance by default.
                                   # We should use IndexHNSWFlat with InnerProduct for cosine.
                                   # For MVP, we'll convert L2 to a similarity score.
            
            D, I = index.search(xq, top_k * 2)
            
            # 2. Keyword Search (BM25)
            tokenized_query = query_text.lower().split()
            bm25_scores = bm25.get_scores(tokenized_query)
            # Get top indices from BM25
            bm25_top_indices = np.argsort(bm25_scores)[::-1][:top_k * 2]
            
            # 3. RRF Fusion
            # Create ranks
            vector_ranks = {idx: rank for rank, idx in enumerate(I[0]) if idx != -1}
            bm25_ranks = {idx: rank for rank, idx in enumerate(bm25_top_indices)}
            
            k = 60 # RRF constant
            combined_indices = set(vector_ranks.keys()) | set(bm25_ranks.keys())
            
            ns_results = []
            for idx in combined_indices:
                v_rank = vector_ranks.get(idx, 1e6)
                b_rank = bm25_ranks.get(idx, 1e6)
                
                score = (1.0 / (k + v_rank)) + (1.0 / (k + b_rank))
                
                # Get similarity (for FAISS L2 distance, smaller is better)
                # Convert distance to a rough similarity [0, 1]
                dist = D[0][list(I[0]).index(idx)] if idx in vector_ranks else 2.0
                similarity = 1.0 / (1.0 + dist)
                
                if similarity >= min_similarity or b_rank < top_k:
                    chunk = chunks[idx]
                    ns_results.append(RetrievalResult(
                        chunk_id=chunk["id"],
                        content=chunk["content"],
                        source_name=chunk["source_name"],
                        source_url=chunk.get("source_url"),
                        chunk_order=chunk["chunk_order"],
                        rrf_score=score,
                        vector_similarity=float(similarity),
                        namespace=ns
                    ))
            
            all_results.extend(ns_results)

        # Final sort by RRF score
        all_results.sort(key=lambda x: x.rrf_score, reverse=True)
        return all_results[:top_k]
