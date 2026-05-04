"""
backfill_supabase_rag.py — Idempotent backfill from chunks.json into Supabase pgvector RAG tables.

Targets schema from supabase/migrations/202604230003_rag_production_final.sql:
  - knowledge_collections
  - knowledge_documents
  - knowledge_chunks

Usage:
  .venv-ingest/bin/python scripts/ingest_corpus/backfill_supabase_rag.py \
    --api-key-id <uuid> \
    --collection-name "DepthAPI Trusted Corpus" \
    --chunk-file data/rag/trusted/chunks.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import get_supabase_admin
from scripts.ingest_corpus.base_ingestor import REPO_ROOT, log


def _sha256_16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _chunk_metadata(chunk: Mapping[str, Any]) -> dict[str, Any]:
    raw_meta = chunk.get("metadata")
    if isinstance(raw_meta, dict):
        return cast(dict[str, Any], raw_meta)
    return {}


def _doc_key(chunk: dict[str, Any]) -> str:
    meta = _chunk_metadata(chunk)
    rel = meta.get("relative_path")
    if rel:
        return str(rel)
    source_url = chunk.get("source_url")
    if source_url:
        return str(source_url)
    source_name = chunk.get("source_name", "unknown")
    namespace = meta.get("namespace", "trusted")
    return f"{namespace}/{source_name}"


def _group_chunks_by_document(chunks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ch in chunks:
        grouped[_doc_key(ch)].append(ch)
    return grouped


async def _exec_with_retry(op, label: str, retries: int = 5, base_delay: float = 1.0):
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await op()
        except Exception as exc:  # network/transient supabase errors
            last_exc = exc
            if attempt == retries:
                break
            await asyncio.sleep(base_delay * attempt)
            log.warning("supabase_op_retry label=%s attempt=%s error=%s", label, attempt, str(exc))
    raise RuntimeError(f"{label}_failed_after_retries: {last_exc}")


async def _get_or_create_collection(
    supabase: Any,
    api_key_id: str,
    collection_name: str,
    collection_metadata: dict[str, Any],
) -> str:
    existing = await _exec_with_retry(
        lambda: (
            supabase.table("knowledge_collections")
            .select("id,name,api_key_id")
            .eq("api_key_id", api_key_id)
            .limit(200)
            .execute()
        ),
        label="collection_lookup",
    )
    for row in existing.data or []:
        if row.get("name") == collection_name:
            return row["id"]

    inserted = await _exec_with_retry(
        lambda: (
            supabase.table("knowledge_collections")
            .insert(
                {
                    "api_key_id": api_key_id,
                    "name": collection_name,
                    "metadata": collection_metadata,
                }
            )
            .execute()
        ),
        label="collection_insert",
    )
    if inserted.error or not inserted.data:
        raise RuntimeError(f"collection_create_failed: {inserted.error}")
    return inserted.data[0]["id"]


async def _upsert_document(
    supabase: Any,
    collection_id: str,
    filename: str,
    source_url: str | None,
    namespace: str,
    language: str,
    metadata: dict[str, Any],
) -> str:
    content_hash = _sha256_16(f"{collection_id}:{filename}")
    payload = {
        "collection_id": collection_id,
        "filename": filename,
        "source_url": source_url,
        "content_hash": content_hash,
        "language_config": "english" if language == "en" else "simple",
        "metadata": metadata,
    }
    result = await _exec_with_retry(
        lambda: (
            supabase.table("knowledge_documents")
            .upsert(payload, on_conflict="collection_id,content_hash")
            .execute()
        ),
        label="document_upsert",
    )
    if result.error:
        raise RuntimeError(f"document_upsert_failed: {result.error}")
    # PostgREST upsert may return [] depending on config; fetch deterministically.
    doc = await _exec_with_retry(
        lambda: (
            supabase.table("knowledge_documents")
            .select("id")
            .eq("collection_id", collection_id)
            .eq("content_hash", content_hash)
            .limit(1)
            .execute()
        ),
        label="document_lookup",
    )
    if doc.error or not doc.data:
        raise RuntimeError(f"document_lookup_failed: {doc.error}")
    return doc.data[0]["id"]


async def _upsert_chunks_for_document(
    supabase: Any,
    document_id: str,
    chunks: list[dict[str, Any]],
    batch_size: int,
) -> tuple[int, int]:
    inserted = 0
    attempted = 0

    rows = []
    for ch in chunks:
        content = str(ch.get("content", "") or "").strip()
        if not content:
            continue
        content_hash = _sha256_16(content)
        meta = _chunk_metadata(ch)
        metadata_row = dict(meta)
        metadata_row.update(
            {
                "source_name": ch.get("source_name"),
                "source_url": ch.get("source_url"),
                "source_type": ch.get("source_type"),
                "tags": ch.get("tags", []),
            }
        )
        row = {
            "document_id": document_id,
            "content": content,
            "content_hash": content_hash,
            "token_count": int(ch.get("token_count", 0) or 0),
            "chunk_order": int(ch.get("chunk_order", 0) or 0),
            "metadata": metadata_row,
        }
        rows.append(row)

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        attempted += len(batch)
        result = await _exec_with_retry(
            lambda: (
                supabase.table("knowledge_chunks")
                .upsert(batch, on_conflict="document_id,content_hash")
                .execute()
            ),
            label=f"chunk_upsert_batch_{i // batch_size + 1}",
        )
        if result.error:
            raise RuntimeError(f"chunk_upsert_failed at batch {i // batch_size + 1}: {result.error}")
        inserted += len(batch)

    return inserted, attempted


async def run(
    api_key_id: str,
    collection_name: str,
    chunk_file: Path,
    batch_size: int = 500,
) -> dict[str, Any]:
    supabase = get_supabase_admin()
    if not supabase:
        raise RuntimeError("Supabase admin client unavailable. Check SUPABASE_URL and SUPABASE_SECRET_KEY.")

    chunks = json.loads(chunk_file.read_text(encoding="utf-8"))
    grouped = _group_chunks_by_document(chunks)
    collection_id = await _get_or_create_collection(
        supabase=supabase,
        api_key_id=api_key_id,
        collection_name=collection_name,
        collection_metadata={"source": "chunks.json backfill", "language": "en"},
    )

    docs_created = 0
    chunks_upserted = 0
    chunks_attempted = 0
    namespaces = defaultdict(int)

    for doc_key, doc_chunks in grouped.items():
        sample = doc_chunks[0]
        meta = _chunk_metadata(sample)
        namespace = str(meta.get("namespace", "trusted"))
        language = str(meta.get("language", "en"))
        source_url = sample.get("source_url")
        doc_meta = {
            "namespace": namespace,
            "dataset_root": meta.get("dataset_root"),
            "relative_path": meta.get("relative_path"),
            "source_name": sample.get("source_name"),
            "source_type": sample.get("source_type"),
        }
        document_id = await _upsert_document(
            supabase=supabase,
            collection_id=collection_id,
            filename=doc_key,
            source_url=source_url,
            namespace=namespace,
            language=language,
            metadata=doc_meta,
        )
        docs_created += 1
        inserted, attempted = await _upsert_chunks_for_document(
            supabase=supabase,
            document_id=document_id,
            chunks=doc_chunks,
            batch_size=batch_size,
        )
        chunks_upserted += inserted
        chunks_attempted += attempted
        namespaces[namespace] += attempted

    return {
        "collection_id": collection_id,
        "collection_name": collection_name,
        "documents_processed": docs_created,
        "chunks_attempted": chunks_attempted,
        "chunks_upserted": chunks_upserted,
        "namespace_distribution": dict(namespaces),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill chunks.json into Supabase RAG tables")
    parser.add_argument("--api-key-id", required=True, help="UUID from public.api_keys.id")
    parser.add_argument("--collection-name", required=True)
    parser.add_argument("--chunk-file", default=str(REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"))
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    result = asyncio.run(
        run(
            api_key_id=args.api_key_id,
            collection_name=args.collection_name,
            chunk_file=Path(args.chunk_file),
            batch_size=args.batch_size,
        )
    )
    print(json.dumps(result, indent=2))
    log.info(
        "supabase_backfill_complete collection_id=%s docs=%s chunks=%s",
        result["collection_id"],
        result["documents_processed"],
        result["chunks_attempted"],
    )


if __name__ == "__main__":
    main()
