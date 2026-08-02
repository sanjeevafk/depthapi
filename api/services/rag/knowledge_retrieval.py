"""Retrieval service backed by PostgreSQL functions."""
from typing import Any
from api.adapters.pg_adapter import execute_rpc

class RetrievalService:
    async def retrieve(self, query: str, collection_id: str | None = None, trusted: bool = True) -> list[dict[str, Any]]:
        return await execute_rpc("hybrid_search_trusted_v5" if trusted else "hybrid_search_v5", {"query_text": query, "collection_filter": collection_id})

    async def search(self, query: str, collection_id: str | None = None) -> list[dict[str, Any]]:
        return await self.retrieve(query, collection_id)

    async def get_neighbors(self, chunk_id: str, window: int = 2) -> list[dict[str, Any]]:
        return await execute_rpc("get_neighbor_chunks", {"chunk_id": chunk_id, "window_size": window})

def get_retrieval_service() -> RetrievalService:
    return RetrievalService()
