"""Reranker service for DepthAPI.
Uses Cross-Encoders to re-sort hybrid search results for higher precision.
"""

import asyncio
import functools
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-base")
        self.model: Any = None

    def _ensure_model(self) -> Any:
        if self.model is None:
            try:
                from sentence_transformers import CrossEncoder

                self.model = CrossEncoder(self.model_name, device="cpu")
                logger.info("Loaded reranker model %s on cpu", self.model_name)
            except Exception as exc:
                logger.warning("Failed to load reranker model %s: %s", self.model_name, exc)
                return None
        return self.model

    async def rerank(
        self, query: str, candidates: list[dict[str, Any]], top_n: int = 10
    ) -> list[dict[str, Any]]:
        """
        Re-scores and re-sorts candidates based on the query.
        Falls back gracefully to original candidate order if the model is unavailable.
        """
        if not candidates:
            return []

        model = await asyncio.to_thread(self._ensure_model)
        if model is None:
            return candidates[:top_n]

        try:
            # Prepare pairs for cross-encoder
            pairs = [[query, c.get("content", "")] for c in candidates]

            # Run inference in a thread to avoid blocking asyncio loop
            scores = await asyncio.to_thread(model.predict, pairs)

            # Attach scores and sort
            for i, candidate in enumerate(candidates):
                candidate["rerank_score"] = float(scores[i])

            sorted_candidates = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
            return sorted_candidates[:top_n]
        except Exception as exc:
            logger.warning("Rerank inference failed: %s", exc)
            return candidates[:top_n]


@functools.cache
def get_reranker_service(model_name: str | None = None) -> RerankerService:
    return RerankerService(model_name=model_name)

