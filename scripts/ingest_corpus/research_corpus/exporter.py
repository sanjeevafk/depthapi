from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .io_utils import write_jsonl
from .models import SourceDocument, stable_hash, utc_now


def _clean_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


async def _read_postgres_documents(limit: int | None = None) -> list[dict[str, Any]]:
    import asyncpg

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL corpus export")

    query = """
        SELECT c.document_id, c.content, c.chunk_order, c.metadata,
               d.source_url, d.filename, d.title, d.metadata AS document_metadata
        FROM knowledge_chunks AS c
        JOIN knowledge_documents AS d ON d.id = c.document_id
        ORDER BY c.document_id, c.chunk_order
    """
    if limit is not None:
        query += " LIMIT $1"

    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(query, limit) if limit is not None else await connection.fetch(query)
        return [dict(row) for row in rows]
    finally:
        await connection.close()


def export_postgres_documents(output_path: Path, limit: int | None = None) -> dict[str, Any]:
    load_dotenv(".env.local", override=True)
    load_dotenv()

    rows: list[dict[str, Any]] = []
    for row in asyncio.run(_read_postgres_documents(limit)):
        chunk_metadata = _clean_metadata(row.get("metadata"))
        document_metadata = _clean_metadata(row.get("document_metadata"))
        metadata = {**document_metadata, **chunk_metadata}
        source = str(metadata.get("source_name") or row.get("filename") or "unknown")
        source_url = str(metadata.get("source_url") or row.get("source_url") or "")
        upstream_license = str(
            metadata.get("upstream_license")
            or metadata.get("license")
            or metadata.get("license_name")
            or "unknown"
        )
        document_id = str(row.get("document_id") or metadata.get("doc_id") or stable_hash(f"{source}|{source_url}")[:24])
        content = str(row.get("content") or "")
        title = str(metadata.get("title") or row.get("title") or metadata.get("relative_path") or source)
        rows.append(
            SourceDocument(
                document_id=document_id,
                source=source,
                source_url=source_url,
                upstream_license=upstream_license,
                title=title,
                retrieved_at=str(metadata.get("retrieved_at") or utc_now()),
                namespace=str(metadata.get("namespace") or "unknown"),
                content=content,
                metadata=metadata,
            ).to_dict()
        )

    write_jsonl(output_path, rows)
    return {"documents_exported": len(rows), "output_path": str(output_path)}
