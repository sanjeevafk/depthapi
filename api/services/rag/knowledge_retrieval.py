"""RAG Retrieval Service for DepthAPI.
Handles hybrid search, reranking, and context expansion.
"""

import asyncio
from functools import lru_cache
from typing import Any, Dict, List, Optional

import structlog
from pydantic import SecretStr
from api.auth import get_supabase_admin
from api.config import get_settings
from api.adapters.supabase_adapter import SupabaseHTTPClient
from api.services.rag.embeddings import get_embedding_service
from api.services.rag.reranker import get_reranker_service

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_trusted_corpus_admin() -> SupabaseHTTPClient | None:
    """Optional client for local trusted corpus pgvector tier."""
    settings = get_settings()
    if not getattr(settings, "local_pgvector_url", ""):
        return None
    secret_key = getattr(settings, "local_pgvector_secret_key", "")
    if isinstance(secret_key, SecretStr):
        secret_key = secret_key.get_secret_value()
    secret_key = str(secret_key or "").strip()
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
        use_trusted_corpus: bool = True,
        collection_id: str | None = None,
        query_mode: str = "conceptual",
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
                        collection_id=collection_id,
                        query_mode=query_mode,
                    )
                )
            if trusted_db and use_trusted_corpus:
                search_tasks.append(
                    self._search_candidates(
                        db=trusted_db,
                        tier="trusted",
                        query=query,
                        query_vector=query_vector,
                        api_key_id=api_key_id,
                        limit=limit,
                        min_similarity=min_similarity,
                        query_mode=query_mode,
                    )
                )

            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            candidates: List[Dict[str, Any]] = []
            for result in search_results:
                if isinstance(result, Exception):
                    logger.warning("retrieval_tier_query_failed", error=str(result))
                    continue
                if isinstance(result, list):
                    candidates.extend(result)
                    continue
                logger.warning("retrieval_tier_query_unexpected", result_type=type(result).__name__)

            if not candidates:
                return []

            # 3. Reranking Stage (Cross-Encoder)
            ranked_candidates = (await self._passthrough_rerank(query, candidates))[:limit]

            # 4. Context Expansion (Neighboring chunks)
            final_context = []
            for cand in ranked_candidates:
                source_db = primary_db if cand.get("source_tier") != "trusted" else trusted_db
                expanded_content = cand["content"]
                if source_db:
                    expanded_content = await self._expand_context(source_db, cand, neighbor_window)
                final_context.append({
                    "id": cand["chunk_id"],
                    "chunk_id": cand.get("chunk_id"),
                    "document_id": cand.get("document_id"),
                    "content": expanded_content,
                    "citation": {
                        "filename": cand.get("filename"),
                        "source_url": cand.get("source_url"),
                        "chunk_order": cand.get("chunk_order"),
                        "source_tier": cand.get("source_tier"),
                    },
                    "metadata": cand.get("metadata") or {},
                    "score": cand.get("rrf_score"),
                    "vector_similarity": cand.get("vector_similarity"),
                    "match_source": cand.get("match_source"),
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

    def _get_text_shingles(self, text: str, n: int = 3) -> set:
        """Convert text into a set of n-gram shingles for similarity comparison."""
        tokens = text.lower().split()
        if len(tokens) < n:
            return set(tokens)
        return {" ".join(tokens[i:i+n]) for i in range(len(tokens)-n+1)}

    def _calculate_jaccard(self, set1: set, set2: set) -> float:
        """Calculate Jaccard similarity between two shingle sets."""
        if not set1 or not set2:
            return 0.0
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return intersection / union

    async def _passthrough_rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rerank candidates with a cross-encoder, applying diversity filtering."""
        # 1. ID-based deduplication
        deduped: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates:
            chunk_id = candidate.get("chunk_id")
            if not chunk_id:
                continue
            existing = deduped.get(chunk_id)
            if existing is None or candidate.get("rrf_score", 0) > existing.get("rrf_score", 0):
                deduped[chunk_id] = candidate
        
        # 2. Structural Diversity Filter (Jaccard Shingling)
        # We process in order of RRF score to keep the best representatives of a template
        initial_ordered = sorted(deduped.values(), key=lambda x: x.get("rrf_score", 0), reverse=True)
        diverse_results: List[Dict[str, Any]] = []
        seen_shingles: List[set] = []
        
        for cand in initial_ordered:
            cand_content = cand.get("content", "")
            cand_shingles = self._get_text_shingles(cand_content)
            
            is_redundant = False
            for prev_shingles in seen_shingles:
                # 0.75 threshold captures near-identical documentation templates (P2 Hardening)
                if self._calculate_jaccard(cand_shingles, prev_shingles) > 0.75:
                    is_redundant = True
                    break
            
            if not is_redundant:
                diverse_results.append(cand)
                seen_shingles.append(cand_shingles)
            else:
                logger.debug("retrieval_diversity_suppressed", chunk_id=cand.get("chunk_id"))

        if not query or len(diverse_results) <= 1:
            return diverse_results

        try:
            reranker = get_reranker_service()
            return await reranker.rerank(query, diverse_results, top_n=len(diverse_results))
        except Exception as exc:
            logger.warning("rerank_failed_fallback", error=str(exc))
            return diverse_results

    async def _search_candidates(
        self,
        db: SupabaseHTTPClient,
        tier: str,
        query: str,
        query_vector: List[float],
        api_key_id: str,
        limit: int,
        min_similarity: float,
        collection_id: str | None = None,
        query_mode: str = "conceptual",
    ) -> List[Dict[str, Any]]:
        if tier == "trusted":
            # RPC v5+ supports dual-tsvector and query_mode
            rpc_name = "hybrid_search_trusted_v5"
            payload: dict[str, Any] = {
                "query_text": query,
                "query_embedding": query_vector,
                "query_mode": query_mode,
                "final_count": limit * 2,
                "min_similarity": min_similarity,
            }
        else:
            # RPC v5+ supports dual-tsvector, query_mode, and collection filtering
            rpc_name = "hybrid_search_v5"
            # P0 FIX: Convert 'anonymous' string to valid UUID for Postgres
            target_api_key_id = api_key_id if api_key_id != "anonymous" else "00000000-0000-0000-0000-000000000000"
            payload = {
                "query_text": query,
                "query_embedding": query_vector,
                "target_api_key_id": target_api_key_id,
                "query_mode": query_mode,
                "final_count": limit * 2,
                "min_similarity": min_similarity,
            }
            if collection_id:
                # v5 update 202605160001 added target_collection_id support
                payload["target_collection_id"] = collection_id

        try:
            search_res = await db.rpc(rpc_name, payload).execute()
            candidates = search_res.data or []
        except Exception as exc:
            err_msg = str(exc).lower()
            # Fallback 1: Parameter mismatch (target_collection_id)
            if "target_collection_id" in err_msg and collection_id:
                logger.warning("rpc_param_unsupported", param="target_collection_id", rpc=rpc_name)
                del payload["target_collection_id"]
                search_res = await db.rpc(rpc_name, payload).execute()
                candidates = search_res.data or []
            # Fallback 2: Function not found (v5 vs v4)
            elif "not found" in err_msg or "does not exist" in err_msg:
                fallback_rpc = "hybrid_search_trusted_v4" if tier == "trusted" else "hybrid_search_v4"
                logger.warning("rpc_version_unsupported", current=rpc_name, fallback=fallback_rpc)
                # Filter payload for v4 (no query_mode or target_collection_id)
                v4_payload = {
                    "query_text": query,
                    "query_embedding": query_vector,
                    "final_count": limit * 2,
                    "min_similarity": min_similarity,
                }
                if tier != "trusted":
                    # P0 FIX: Convert 'anonymous' for v4 fallback
                    v4_payload["target_api_key_id"] = api_key_id if api_key_id != "anonymous" else "00000000-0000-0000-0000-000000000000"
                
                search_res = await db.rpc(fallback_rpc, v4_payload).execute()
                candidates = search_res.data or []
            else:
                raise exc
        
        # Defensive check: Ensure candidates is a list before iteration
        if not isinstance(candidates, list):
            print(f"DEBUG: Tier {tier} returned {type(candidates).__name__}: {candidates}")
            return []

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
