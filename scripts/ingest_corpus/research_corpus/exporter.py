from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .io_utils import write_jsonl
from .models import SourceDocument, stable_hash, utc_now


def _clean_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


def export_supabase_documents(output_path: Path, limit: int | None = None) -> dict[str, Any]:
    load_dotenv(".env.local", override=True)
    load_dotenv()

    from supabase import ClientOptions, create_client  # type: ignore[reportMissingImports]

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SECRET_KEY missing")

    client = create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(postgrest_client_timeout=120.0),
    )

    rows: list[dict[str, Any]] = []
    page_size = 1000
    last_id: int | None = None
    fetched = 0

    while True:
        query = (
            client.table("knowledge_chunks")
            .select("id,document_id,content,chunk_order,metadata")
            .order("id")
            .limit(page_size)
        )
        if last_id is not None:
            query = query.gt("id", last_id)
        response = query.execute()
        batch = response.data or []
        if not batch:
            break

        for row in batch:
            metadata = _clean_metadata(row.get("metadata"))
            source = str(metadata.get("source_name") or metadata.get("source") or "unknown")
            source_url = str(metadata.get("source_url") or "")
            upstream_license = str(
                metadata.get("upstream_license")
                or metadata.get("license")
                or metadata.get("license_name")
                or "unknown"
            )
            document_id = str(
                row.get("document_id")
                or metadata.get("doc_id")
                or stable_hash(f"{source}|{source_url}")[:24]
            )
            content = str(row.get("content") or "")
            title = str(metadata.get("title") or metadata.get("relative_path") or source)
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
            fetched += 1
            if limit and fetched >= limit:
                break
        if limit and fetched >= limit:
            break
        last_id = batch[-1]["id"]
        if len(batch) < page_size:
            break

    write_jsonl(output_path, rows)
    return {"documents_exported": len(rows), "output_path": str(output_path)}
