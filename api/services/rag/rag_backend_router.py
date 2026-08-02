"""PostgreSQL-only retrieval backend selection."""
from __future__ import annotations

from api.adapters.pg_adapter import execute_rpc
from api.services.rag.embeddings import embed_texts


async def retrieve(
    query: str,
    collection_id: str | None = None,
    api_key_id: str | None = None,
    trusted: bool = True,
) -> list[dict]:
    """Embed query then call hybrid_search_v5 / hybrid_search_trusted_v5."""
    [embedding_literal] = await embed_texts([query])
    fn = "hybrid_search_trusted_v5" if trusted else "hybrid_search_v5"
    return await execute_rpc(fn, {
        "query_text": query,
        "query_embedding": embedding_literal,
        "collection_filter": collection_id,
        "api_key_filter": api_key_id,
    })


def get_rag_backend():
    """Return the local retrieval callable for integrations."""
    return retrieve
