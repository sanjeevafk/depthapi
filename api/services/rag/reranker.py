"""Reranker service for DepthAPI.
Uses Cross-Encoders to re-sort hybrid search results for higher precision.
"""

import os
from typing import List, Dict, Any
import structlog
import asyncio

logger = structlog.get_logger(__name__)

class RerankerService:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-base")
        # Using CPU for stability in local dev
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(self.model_name, device="cpu")
        logger.info("reranker_model_loaded", model=self.model_name, device="cpu")

    async def rerank(self, query: str, candidates: List[Dict[str, Any]], top_n: int = 10) -> List[Dict[str, Any]]:
        """
        Re-scores and re-sorts candidates based on the query.
        
        Args:
            query: The user query string.
            candidates: List of chunk dictionaries (must contain 'content').
            top_n: Number of results to return after reranking.
            
        Returns:
            Sorted list of candidates with an added 'rerank_score' field.
        """
        if not candidates:
            return []

        # Prepare pairs for cross-encoder
        pairs = [[query, c.get("content", "")] for c in candidates]
        
        # Run inference in a thread to avoid blocking asyncio loop
        scores = await asyncio.to_thread(self.model.predict, pairs)
        
        # Attach scores and sort
        for i, candidate in enumerate(candidates):
            candidate["rerank_score"] = float(scores[i])
            
        sorted_candidates = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        return sorted_candidates[:top_n]

_reranker: Any = None

def get_reranker_service() -> RerankerService:
    global _reranker
    if _reranker is None:
        _reranker = RerankerService()
    return _reranker
