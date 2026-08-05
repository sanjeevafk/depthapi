#!/usr/bin/env python3
"""Replicate PostgreSQL/pgvector chunks into Turso/libSQL edge storage.

PostgreSQL is authoritative. Turso stores a compact, read-optimized copy for
edge retrieval and cache/backup use; it is never used as the system of record.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from typing import Any

import asyncpg
import libsql_experimental as libsql


def _embedding_blob(value: Any) -> bytes:
    if value is None:
        return b""
    values = value if isinstance(value, (list, tuple)) else str(value).strip("[]").split(",")
    return struct.pack(f"{len(values)}f", *(float(item) for item in values))


async def _read_postgres(database_url: str, full: bool) -> list[dict[str, Any]]:
    connection = await asyncpg.connect(database_url)
    try:
        query = """
            SELECT c.id, c.document_id, c.content, c.embedding, c.metadata,
                   c.chunk_hash, c.chunk_order, c.created_at, c.updated_at,
                   d.collection_id
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE ($1 OR c.updated_at >= now() - interval '24 hours')
            ORDER BY c.updated_at NULLS FIRST, c.id
        """
        return [dict(row) for row in await connection.fetch(query, full)]
    finally:
        await connection.close()


def sync(*, full: bool = False) -> int:
    database_url = os.environ.get("DATABASE_URL")
    turso_url = os.environ.get("TURSO_DATABASE_URL")
    turso_token = os.environ.get("TURSO_AUTH_TOKEN")
    if not database_url or not turso_url or not turso_token:
        raise RuntimeError("DATABASE_URL, TURSO_DATABASE_URL, and TURSO_AUTH_TOKEN are required")

    rows = asyncio.run(_read_postgres(database_url, full))
    edge = libsql.connect(turso_url, auth_token=turso_token, isolation_level=None)
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        content_hash = row.get("chunk_hash") or hashlib.sha256(row["content"].encode()).hexdigest()
        sync_hash = hashlib.sha256(f"{row['id']}|{content_hash}".encode()).hexdigest()
        edge.execute(
            """INSERT INTO knowledge_chunks_platform
               (id, document_id, content, tenant_id, source, aux_metadata,
                embedding, embedding_dim, embedding_model, embedding_version,
                chunking_version, content_hash, sync_hash, created_at, updated_at, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET content=excluded.content,
                aux_metadata=excluded.aux_metadata, embedding=excluded.embedding,
                content_hash=excluded.content_hash, sync_hash=excluded.sync_hash,
                updated_at=excluded.updated_at, synced_at=excluded.synced_at""",
            (str(row["id"]), str(row["document_id"]), row["content"],
             str(row["collection_id"]), metadata.get("source_url"), json.dumps(metadata),
             _embedding_blob(row.get("embedding")), 768, metadata.get("embedding_model", "configured"),
             metadata.get("embedding_version", "v1"), metadata.get("chunking_version", "v2"),
             content_hash, sync_hash, row.get("created_at"), row.get("updated_at"), now),
        )
    edge.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync PostgreSQL pgvector to Turso/libSQL edge storage")
    parser.add_argument("--full", action="store_true", help="Replicate all chunks instead of the recent window")
    args = parser.parse_args()
    print(f"replicated {sync(full=args.full)} PostgreSQL chunks to Turso")


if __name__ == "__main__":
    main()
