"""Backfill NULL embeddings in Supabase knowledge_chunks using configured embedding provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import get_supabase_admin
from api.services.rag.embeddings import get_embedding_service


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


async def run(batch_size: int, max_batches: int | None = None) -> dict:
    supabase = get_supabase_admin()
    if not supabase:
        raise RuntimeError("Supabase admin client unavailable")
    embed = get_embedding_service()

    processed = 0
    updated = 0
    batches = 0
    failures = 0

    while True:
        if max_batches is not None and batches >= max_batches:
            break
        resp = await (
            supabase.table("knowledge_chunks")
            .select("id,content")
            .is_("embedding", None)
            .is_("deleted_at", None)
            .order("id")
            .limit(batch_size)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break

        texts = [str(r.get("content", "") or "").replace("\n", " ").strip() for r in rows]
        vectors = None
        last_err: Exception | None = None
        for attempt in range(1, 8):
            try:
                vectors = await embed.create_embeddings(texts)
                break
            except Exception as exc:
                last_err = exc
                wait_s = _retry_after_from_error(str(exc), default=15.0 * attempt)
                failures += 1
                print(f"batch={batches + 1} attempt={attempt} error={exc} wait={wait_s:.1f}s")
                await asyncio.sleep(wait_s)
        if vectors is None:
            raise RuntimeError(f"embedding_batch_failed: {last_err}")
        if len(vectors) != len(rows):
            raise RuntimeError(f"Embedding count mismatch: vectors={len(vectors)} rows={len(rows)}")

        payload = [{"id": rows[i]["id"], "embedding": _vector_literal(vectors[i])} for i in range(len(rows))]
        upd = await supabase.rpc("apply_chunk_embeddings", {"p_rows": payload}).execute()
        if upd.error:
            raise RuntimeError(f"chunk_embedding_bulk_update_failed: {upd.error}")

        processed += len(rows)
        updated += len(rows)
        batches += 1
        print(f"batch={batches} processed={processed} updated={updated}")

    remaining = await (
        supabase.table("knowledge_chunks")
        .select("id")
        .is_("embedding", None)
        .is_("deleted_at", None)
        .limit(1)
        .execute()
    )
    return {
        "processed": processed,
        "updated": updated,
        "batches": batches,
        "failures": failures,
        "complete": not bool(remaining.data),
    }


def _retry_after_from_error(error_text: str, default: float) -> float:
    m = re.search(r"retry in ([0-9]+(?:\\.[0-9]+)?)s", error_text, re.IGNORECASE)
    if m:
        return max(1.0, float(m.group(1)))
    return default


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-batches", type=int, default=None)
    args = parser.parse_args()
    result = asyncio.run(run(batch_size=args.batch_size, max_batches=args.max_batches))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
