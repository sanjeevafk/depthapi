"""RAG Retrieval Service for DepthAPI.
Handles hybrid search, reranking, and context expansion.
"""

import asyncio
from functools import lru_cache
from typing import Any, Dict, List, Optional

import structlog
from api.auth import get_supabase_admin
from api.config import get_settings
from api.adapters.supabase_adapter import SupabaseHTTPClient
from api.services.embeddings import get_embedding_service

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_trusted_corpus_admin() -> SupabaseHTTPClient | None:
    """Optional client for local trusted corpus pgvector tier."""
    settings = get_settings()
    if not getattr(settings, "local_pgvector_url", ""):
        return None
    secret_key = getattr(settings, "local_pgvector_secret_key", "")
    if hasattr(secret_key, "get_secret_value"):
        secret_key = secret_key.get_secret_value()
    if not secret_key:
        logger.warning("trusted_corpus_secret_missing")
        return None
    return SupabaseHTTPClient(settings.local_pgvector_url, str(secret_key), is_admin=True)


class RetrievalService:
    def __init__(self):
        self.embed_service = get_embedding_service()

    async def retrieve_context(
        self, 
        query: str, 
        api_key_id: str, 
        limit: int = 5,
        neighbor_window: int = 1,
        min_similarity: float = 0.75,
    ) -> List[Dict[str, Any]]:
        """Perform hybrid search, expand context, and return snippets with citations."""
        if not query or not str(query).strip():
            return []

        primary_db: SupabaseHTTPClient | None = get_supabase_admin()
        trusted_db: SupabaseHTTPClient | None = get_trusted_corpus_admin()
        if not primary_db and not trusted_db:
            logger.error("retrieval_db_unavailable")
            return []
        
        # 1. Embed the query
        query_vectors = await self.embed_service.create_embeddings([query])
        if not query_vectors:
            return []
        query_vector = query_vectors[0]

        # 2. Call Hybrid RRF Search (RPC), across all available tiers.
        try:
            search_tasks = []
            if primary_db:
                search_tasks.append(
                    self._search_candidates(
                        db=primary_db,
                        tier="customer",
                        query=query,
                        query_vector=query_vector,
                        api_key_id=api_key_id,
                        limit=limit,
                        min_similarity=min_similarity,
                    )
                )
            if trusted_db:
                search_tasks.append(
                    self._search_candidates(
                        db=trusted_db,
                        tier="trusted",
                        query=query,
                        query_vector=query_vector,
                        api_key_id=api_key_id,
                        limit=limit,
                        min_similarity=min_similarity,
                    )
                )

            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            candidates: List[Dict[str, Any]] = []
            for result in search_results:
                if isinstance(result, Exception):
                    logger.warning("retrieval_tier_query_failed", error=str(result))
                    continue
                candidates.extend(result)

            if not candidates:
                return []

            # 3. Reranking Stage (Placeholder)
            # TODO: Integrate a Cross-Encoder (e.g. Cohere or Jina) here
            ranked_candidates = self._passthrough_rerank(query, candidates)[:limit]

            # 4. Context Expansion (Neighboring chunks)
            final_context = []
            for cand in ranked_candidates:
                source_db = primary_db if cand.get("source_tier") != "trusted" else trusted_db
                expanded_content = cand["content"]
                if source_db:
                    expanded_content = await self._expand_context(source_db, cand, neighbor_window)
                final_context.append({
                    "id": cand["chunk_id"],
                    "content": expanded_content,
                    "citation": {
                        "filename": cand.get("filename"),
                        "source_url": cand.get("source_url"),
                        "chunk_order": cand.get("chunk_order"),
                        "source_tier": cand.get("source_tier"),
                    },
                    "score": cand.get("rrf_score"),
                    "vector_similarity": cand.get("vector_similarity"),
                })

            return final_context

        except Exception as exc:
            logger.error("retrieval_failed", error=str(exc), api_key_id=api_key_id)
            return []

    async def _expand_context(self, supabase: SupabaseHTTPClient, candidate: Dict[str, Any], window: int) -> str:
        """Fetch neighboring chunks to provide a smoother context window."""
        if window <= 0:
            return candidate["content"]
            
        try:
            neighbor_res = await supabase.rpc(
                "get_neighbor_chunks",
                {
                    "p_chunk_id": candidate["chunk_id"],
                    "p_window_size": window
                }
            ).execute()
            
            neighbors = neighbor_res.data or []
            if not neighbors:
                return candidate["content"]
                
            # Neighbors are returned ordered by chunk_order
            neighbor_texts = [n.get("content") for n in neighbors if n.get("content") is not None]
            if len(neighbor_texts) < len(neighbors):
                logger.warning("retrieval_neighbor_content_none", 
                               chunk_id=candidate["chunk_id"], 
                               missing_count=len(neighbors) - len(neighbor_texts))
            
            if not neighbor_texts:
                return candidate["content"]
                
            return "\n".join(neighbor_texts)
            
        except Exception as exc:
            logger.warning("context_expansion_failed", chunk_id=candidate["chunk_id"], error=str(exc))
            return candidate["content"]

    def _passthrough_rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Temporary passthrough reranker. In production, this uses a Cross-Encoder."""
        deduped: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates:
            chunk_id = candidate.get("chunk_id")
            if not chunk_id:
                continue
            existing = deduped.get(chunk_id)
            if existing is None or candidate.get("rrf_score", 0) > existing.get("rrf_score", 0):
                deduped[chunk_id] = candidate
        # For now, we trust the RRF score from Postgres and keep best per chunk.
        return sorted(deduped.values(), key=lambda x: x.get("rrf_score", 0), reverse=True)

    async def _search_candidates(
        self,
        db: SupabaseHTTPClient,
        tier: str,
        query: str,
        query_vector: List[float],
        api_key_id: str,
        limit: int,
        min_similarity: float,
    ) -> List[Dict[str, Any]]:
        search_res = await db.rpc(
            "hybrid_search_v4",
            {
                "query_text": query,
                "query_embedding": query_vector,
                "target_api_key_id": api_key_id,
                "final_count": limit * 2,  # Get more for reranking
                "min_similarity": min_similarity,
            },
        ).execute()
        candidates = search_res.data or []
        for candidate in candidates:
            candidate["source_tier"] = tier
        return candidates

# Global singleton
_service: Optional[RetrievalService] = None

def get_retrieval_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service
