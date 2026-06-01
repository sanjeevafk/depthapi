"""Switch between filesystem and Supabase/pgvector RAG backends."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional
import structlog

from api.config import get_settings
from api.services.rag.context_processing import compress_contexts, rough_token_count
from api.services.rag.filesystem_rag_store import FilesystemRAGStore
from api.services.rag.knowledge_retrieval import get_retrieval_service

logger = structlog.get_logger(__name__)

# Global singleton for filesystem store
_fs_store: Optional[FilesystemRAGStore] = None
_TRACE_PATH = Path("results/raw/retrieval_traces.jsonl")


def _hash_trace_text(value: str | None) -> str | None:
    """SHA-256 hash of a string for trace storage, preventing clear-text PII on disk."""
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _append_retrieval_trace(payload: dict) -> None:
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("retrieval_trace_write_failed", error=str(exc))


def _resolve_backend_name() -> str:
    configured = str(os.getenv("RAG_BACKEND", "auto") or "auto").strip().lower()
    if configured in {"filesystem", "pgvector"}:
        return configured

    settings = get_settings()
    has_pgvector = bool(getattr(settings, "supabase_url", "") and getattr(settings, "supabase_secret_key", ""))
    return "pgvector" if has_pgvector else "filesystem"


def get_rag_backend():
    global _fs_store
    backend = _resolve_backend_name()
    
    if backend == "filesystem":
        if _fs_store is None:
            _fs_store = FilesystemRAGStore(
                base_path=os.getenv("RAG_DATA_PATH", "data/rag")
            )
        logger.info("rag_backend_selected", backend=backend, base_path=os.getenv("RAG_DATA_PATH", "data/rag"))
        return _fs_store
    elif backend == "pgvector":
        logger.info("rag_backend_selected", backend=backend)
        return get_retrieval_service()
    
    raise ValueError(f"Unknown RAG_BACKEND: {backend}")

async def retrieve_context(query: str, api_key_id: str, **kwargs):
    backend = get_rag_backend()
    retrieval_started = time.perf_counter()
    
    if isinstance(backend, FilesystemRAGStore):
        # We need query embedding for filesystem search
        # For simplicity, we can get the embedding service here
        from api.services.rag.embeddings import get_embedding_service
        embed_service = get_embedding_service()
        
        query_vectors = await embed_service.create_embeddings([query])
        if not query_vectors:
            return []
        
        # Determine namespaces (MVP: trusted + customer collection if provided)
        namespaces = []
        if kwargs.get("use_trusted_corpus", True):
            namespaces.append(os.getenv("RAG_TRUSTED_NAMESPACE", "trusted"))
        collection_id = kwargs.get("collection_id")
        if collection_id:
            namespaces.append(f"{api_key_id}/{collection_id}")
        if not namespaces:
            return []
            
        results = await backend.retrieve(
            query_embedding=query_vectors[0],
            query_text=query,
            namespaces=namespaces,
            top_k=min(int(kwargs.get("limit", 5)), 5),
            min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.65"))
        )

        # Convert RetrievalResult to the dict format expected by the pipeline
        contexts = [
            {
                "id": r.chunk_id,
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "content": r.content,
                "citation": {
                    "filename": None, # Filesystem store uses source_url
                    "source_url": r.source_url,
                    "chunk_order": r.chunk_order,
                    "source_tier": r.namespace,
                },
                "metadata": {
                    **(r.metadata or {}),
                    "source_name": r.source_name,
                    "doc_id": r.document_id,
                    "chunk_id": r.chunk_id,
                    "section_title": r.section_title,
                    "token_count": r.token_count,
                },
                "score": r.rrf_score,
                "vector_similarity": r.vector_similarity,
                "rerank_score": r.rerank_score,
                "rerank_delta": r.rerank_delta,
                "token_count": r.token_count,
            }
            for r in results
        ]
        selected_contexts = compress_contexts(
            contexts,
            max_contexts=int(os.getenv("RAG_MAX_CONTEXTS", "3")),
            max_chars_per_context=int(os.getenv("RAG_MAX_CHARS_PER_CONTEXT", "1000")),
            max_total_chars=int(os.getenv("RAG_MAX_TOTAL_CONTEXT_CHARS", "3000")),
        )
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        timings = getattr(backend, "last_retrieval_timings", {}) or {}
        _append_retrieval_trace(
            {
                "query_hash": _hash_trace_text(query),
                "api_key_id": api_key_id,
                "backend": "filesystem",
                "namespaces": namespaces,
                "retrieval_latency_ms": retrieval_ms,
                "rerank_latency_ms": timings.get("rerank_latency_ms"),
                "total_prompt_tokens": rough_token_count(query)
                + sum(rough_token_count(str(c.get("content", ""))) for c in selected_contexts),
                "retrieved": [
                    {
                        "chunk_text_hash": _hash_trace_text(c.get("content")),
                        "source_doc_id": c.get("document_id"),
                        "chunk_id": c.get("chunk_id"),
                        "similarity_score": c.get("vector_similarity"),
                        "rrf_score": c.get("score"),
                        "rerank_score": c.get("rerank_score"),
                        "rerank_position_delta": c.get("rerank_delta"),
                        "token_count": c.get("token_count"),
                    }
                    for c in contexts
                ],
                "selected_contexts": [
                    {
                        "chunk_text_hash": _hash_trace_text(c.get("content")),
                        "source_doc_id": c.get("document_id"),
                        "chunk_id": c.get("chunk_id"),
                        "similarity_score": c.get("vector_similarity"),
                        "rerank_score": c.get("rerank_score"),
                        "token_count": c.get("token_count"),
                    }
                    for c in selected_contexts
                ],
            }
        )
        return selected_contexts
    else:
        # Supabase/pgvector backend
        kwargs.setdefault("min_similarity", float(os.getenv("RAG_MIN_SIMILARITY", "0.65")))
        results = await backend.retrieve_context(query, api_key_id, **kwargs)
        selected_contexts = compress_contexts(
            results,
            max_contexts=int(os.getenv("RAG_MAX_CONTEXTS", "3")),
            max_chars_per_context=int(os.getenv("RAG_MAX_CHARS_PER_CONTEXT", "1000")),
            max_total_chars=int(os.getenv("RAG_MAX_TOTAL_CONTEXT_CHARS", "3000")),
        )
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        _append_retrieval_trace(
            {
                "query_hash": _hash_trace_text(query),
                "api_key_id": api_key_id,
                "backend": "pgvector",
                "retrieval_latency_ms": retrieval_ms,
                "rerank_latency_ms": None,
                "total_prompt_tokens": rough_token_count(query)
                + sum(rough_token_count(str(c.get("content", ""))) for c in selected_contexts),
                "retrieved": [
                    {
                        "chunk_text_hash": _hash_trace_text(c.get("content")),
                        "source_doc_id": c.get("document_id") or c.get("doc_id"),
                        "chunk_id": c.get("chunk_id") or c.get("id"),
                        "similarity_score": c.get("vector_similarity"),
                        "rrf_score": c.get("score"),
                        "rerank_score": c.get("rerank_score"),
                        "token_count": c.get("token_count"),
                    }
                    for c in results
                    if isinstance(c, dict)
                ],
                "selected_contexts": [
                    {
                        "chunk_text_hash": _hash_trace_text(c.get("content")),
                        "source_doc_id": c.get("document_id") or c.get("doc_id"),
                        "chunk_id": c.get("chunk_id") or c.get("id"),
                        "similarity_score": c.get("vector_similarity"),
                        "rerank_score": c.get("rerank_score"),
                        "token_count": c.get("token_count"),
                    }
                    for c in selected_contexts
                    if isinstance(c, dict)
                ],
            }
        )
        return selected_contexts
