"""PostgreSQL-only retrieval backend selection."""
from api.adapters.pg_adapter import execute_rpc

async def retrieve(query: str, collection_id: str | None = None, trusted: bool = True) -> list[dict]:
    return await execute_rpc(
        "hybrid_search_trusted_v5" if trusted else "hybrid_search_v5",
        {"query_text": query, "collection_filter": collection_id},
    )

def get_rag_backend():
    """Return the local retrieval callable for integrations."""
    return retrieve
