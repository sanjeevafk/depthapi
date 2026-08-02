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

async def execute_rpc(fn_name: str, params: dict) -> list[dict]:
    values = list(params.values())
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM {fn_name}({placeholders})", *values)
    return [dict(row) for row in rows]

async def fetch_one(table: str, where: dict) -> dict | None:
    if not where:
        raise ValueError("where must not be empty")
    columns = list(where)
    clause = " AND ".join(f"{column} = ${i}" for i, column in enumerate(columns, 1))
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE {clause} LIMIT 1", *(where[c] for c in columns))
    return dict(row) if row else None
