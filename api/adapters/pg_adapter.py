"""Small asyncpg adapter used by the local-first API."""
from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None

async def init_pool(dsn: str) -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn)

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool has not been initialised")
    return _pool

_ALLOWED_RPC_FUNCTIONS = frozenset({
    "hybrid_search_v5",
    "hybrid_search_trusted_v5",
    "hybrid_search_with_graph_v5",
    "hybrid_search_trusted_with_graph_v5",
    "dense_search_v5",
    "queue_document",
    "dequeue_document",
    "complete_document",
    "get_neighbor_chunks",
    "get_embedding_dimension",
    "link_chunk_to_concept",
    "get_concept_lineage",
    "delete_collection",
})

_ALLOWED_TABLES = frozenset({
    "api_keys",
    "knowledge_collections",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_concepts",
    "knowledge_edges",
    "knowledge_chunk_concepts",
    "knowledge_ingestion_queue",
    "knowledge_query_logs",
})

_ALLOWED_COLUMNS = frozenset({
    "id",
    "key_hash",
    "is_active",
    "plan",
    "api_key_id",
    "collection_id",
    "document_id",
    "content_hash",
    "status",
    "name",
    "expires_at",
    "scopes",
    "revoked_at",
})

async def execute_rpc(fn_name: str, params: dict) -> list[dict]:
    if fn_name not in _ALLOWED_RPC_FUNCTIONS:
        raise ValueError(f"RPC function not allowed: {fn_name}")
    values = list(params.values())
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM {fn_name}({placeholders})", *values)
    return [dict(row) for row in rows]

async def fetch_one(table: str, where: dict) -> dict | None:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Table not allowed: {table}")
    if not where:
        raise ValueError("where must not be empty")
    columns = list(where)
    for column in columns:
        if column not in _ALLOWED_COLUMNS:
            raise ValueError(f"Column not allowed: {column}")
    clause = " AND ".join(f"{column} = ${i}" for i, column in enumerate(columns, 1))
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE {clause} LIMIT 1", *(where[c] for c in columns))
    return dict(row) if row else None
