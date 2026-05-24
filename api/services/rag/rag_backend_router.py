"""Switch between filesystem and Supabase/pgvector RAG backends."""

import os
from typing import Optional
import structlog

from api.config import get_settings
from api.services.rag.filesystem_rag_store import FilesystemRAGStore
from api.services.rag.knowledge_retrieval import get_retrieval_service

logger = structlog.get_logger(__name__)

# Global singleton for filesystem store
_fs_store: Optional[FilesystemRAGStore] = None


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
            top_k=kwargs.get("limit", 5),
            min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.65"))
        )
        
        # Convert RetrievalResult to the dict format expected by the pipeline
        return [
            {
                "id": r.chunk_id,
                "content": r.content,
                "citation": {
                    "filename": None, # Filesystem store uses source_url
                    "source_url": r.source_url,
                    "chunk_order": r.chunk_order,
                    "source_tier": r.namespace,
                },
                "score": r.rrf_score,
                "vector_similarity": r.vector_similarity,
            }
            for r in results
        ]
    else:
        # Supabase/pgvector backend
        kwargs.setdefault("min_similarity", float(os.getenv("RAG_MIN_SIMILARITY", "0.65")))
        return await backend.retrieve_context(query, api_key_id, **kwargs)
