"""
rag_backend_router.py — Switch between Filesystem and Supabase/pgvector backends.
"""

import os
from typing import Callable, Optional
import structlog
from api.services.filesystem_rag_store import FilesystemRAGStore
from api.services.knowledge_retrieval import get_retrieval_service

logger = structlog.get_logger(__name__)

# Global singleton for filesystem store
_fs_store: Optional[FilesystemRAGStore] = None

def get_rag_backend():
    global _fs_store
    backend = os.getenv("RAG_BACKEND", "filesystem")
    
    if backend == "filesystem":
        if _fs_store is None:
            _fs_store = FilesystemRAGStore(
                base_path=os.getenv("RAG_DATA_PATH", "data/rag")
            )
        return _fs_store
    elif backend == "pgvector":
        return get_retrieval_service()
    
    raise ValueError(f"Unknown RAG_BACKEND: {backend}")

async def retrieve_context(query: str, api_key_id: str, **kwargs):
    backend = get_rag_backend()
    
    if isinstance(backend, FilesystemRAGStore):
        # We need query embedding for filesystem search
        # For simplicity, we can get the embedding service here
        from api.services.embeddings import get_embedding_service
        embed_service = get_embedding_service()
        
        query_vectors = await embed_service.create_embeddings([query])
        if not query_vectors:
            return []
        
        # Determine namespaces (MVP: trusted + customer collection if provided)
        namespaces = [os.getenv("RAG_TRUSTED_NAMESPACE", "trusted")]
        collection_id = kwargs.get("collection_id")
        if collection_id:
            namespaces.append(f"{api_key_id}/{collection_id}")
            
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
        return await backend.retrieve_context(query, api_key_id, **kwargs)
