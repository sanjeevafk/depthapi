#!/usr/bin/env python3
"""
reingest_missing_fastapi.py — Re-insert FastAPI chunks missing from PostgreSQL.

Compares content_hashes already in the 'FastAPI Template - Full Stack' collection
against chunks.json, then inserts any missing rows into the correct document.

Usage (from repo root):
  set -a; source .env.local; set +a
  python3 scripts/ingest_corpus/reingest_missing_fastapi.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: pip install psycopg2-binary")
    sys.exit(1)

PG_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNKS_PATH = REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"
COLLECTION_NAME = "FastAPI Template - Full Stack"
SOURCE_NAME = "Full-Stack FastAPI Template"
TARGET_NAMESPACE = "trusted_framework_examples"


def load_fastapi_chunks() -> list[dict]:
    data = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    return [c for c in data if (c.get("source_name") or "").strip() == SOURCE_NAME]


def get_existing_hashes(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT kch.content_hash
            FROM knowledge_chunks kch
            JOIN knowledge_documents kd ON kch.document_id = kd.id
            JOIN knowledge_collections kc ON kd.collection_id = kc.id
            WHERE kc.name = %s
              AND kch.metadata->>'source_name' = %s
        """, (COLLECTION_NAME, SOURCE_NAME))
        return {r[0] for r in cur.fetchall()}


def create_document(conn, collection_id: str, relative_path: str) -> str:
    """Always create a fresh document for this file path."""
    filename = relative_path if relative_path else "unknown"
    doc_id = str(uuid.uuid4())
    source_url = f"file://datasets/full-stack-fastapi-template/{relative_path}" if relative_path else ""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO knowledge_documents
                (id, collection_id, filename, source_url, content_hash, language_config, version, metadata)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
        """, (
            doc_id,
            collection_id,
            filename,
            source_url,
            str(uuid.uuid4()),   # doc-level content_hash — unique per row
            "english",           # must be in allowed set per CHECK constraint
            1,
            json.dumps({
                "source_name": SOURCE_NAME,
                "relative_path": relative_path,
                "namespace": TARGET_NAMESPACE,
            }),
        ))
    return doc_id




def get_collection_id(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM knowledge_collections WHERE name = %s", (COLLECTION_NAME,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Collection '{COLLECTION_NAME}' not found in DB")
        return str(row[0])


def insert_chunk(conn, chunk: dict, document_id: str) -> bool:
    """Insert chunk if content_hash not already in this collection. Returns True if inserted."""
    meta = chunk.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}

    meta["namespace"] = TARGET_NAMESPACE
    meta["source_name"] = SOURCE_NAME

    content_hash = chunk.get("content_hash") or ""

    with conn.cursor() as cur:
        # Check existence across the whole collection (not just this document)
        cur.execute("""
            SELECT 1 FROM knowledge_chunks kch
            JOIN knowledge_documents kd ON kch.document_id = kd.id
            JOIN knowledge_collections kc ON kd.collection_id = kc.id
            WHERE kc.name = %s AND kch.content_hash = %s
            LIMIT 1
        """, (COLLECTION_NAME, content_hash))
        if cur.fetchone():
            return False  # already present

        cur.execute("""
            INSERT INTO knowledge_chunks
                (id, document_id, content, content_hash, chunk_order, metadata)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
        """, (
            str(uuid.uuid4()),
            document_id,
            chunk.get("content") or chunk.get("raw_text") or "",
            content_hash,
            chunk.get("chunk_order") or 0,
            json.dumps(meta),
        ))
        return True


def main():
    parser = argparse.ArgumentParser(description="Re-ingest missing FastAPI chunks")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"FastAPI Missing-Chunk Re-ingest — {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"{'='*65}")

    if not CHUNKS_PATH.exists():
        print(f"ERROR: {CHUNKS_PATH} not found")
        sys.exit(1)

    all_fastapi = load_fastapi_chunks()
    print(f"\nLoaded {len(all_fastapi)} FastAPI chunks from chunks.json")

    conn = psycopg2.connect(PG_URL)
    conn.autocommit = False

    try:
        collection_id = get_collection_id(conn)
        print(f"Collection ID: {collection_id}")

        existing_hashes = get_existing_hashes(conn)
        print(f"Chunks already in DB:  {len(existing_hashes)}")

        missing = [c for c in all_fastapi if c.get("content_hash") not in existing_hashes]
        print(f"Missing chunks:        {len(missing)}")

        if not missing:
            print("\n✅ No missing chunks — DB is already complete.")
            return

        # Group missing by relative_path
        from collections import defaultdict, Counter
        by_path: dict[str, list] = defaultdict(list)
        for c in missing:
            rp = (c.get("metadata") or {}).get("relative_path") or "unknown"
            by_path[rp].append(c)

        print(f"\nMissing chunks by file:")
        for path, chunks in sorted(by_path.items(), key=lambda x: -len(x[1])):
            print(f"  {len(chunks):3d}  {path}")

        if args.dry_run:
            print("\n[DRY-RUN] No changes applied.")
            return

        inserted = 0
        skipped = 0
        for relative_path, chunks in by_path.items():
            doc_id = create_document(conn, collection_id, relative_path)
            for chunk in chunks:
                if insert_chunk(conn, chunk, doc_id):
                    inserted += 1
                else:
                    skipped += 1

        conn.commit()
        print(f"\n✅ Inserted {inserted} chunks across {len(by_path)} files")

        # Final verification
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kch.metadata->>'relative_path' AS path, COUNT(*) AS n
                FROM knowledge_chunks kch
                JOIN knowledge_documents kd ON kch.document_id = kd.id
                JOIN knowledge_collections kc ON kd.collection_id = kc.id
                WHERE kc.name = %s
                  AND kch.metadata->>'source_name' = %s
                GROUP BY 1 ORDER BY 2 DESC
            """, (COLLECTION_NAME, SOURCE_NAME))
            rows = cur.fetchall()

        print(f"\nPost-ingest FastAPI collection ({sum(r[1] for r in rows)} total chunks):")
        for r in rows:
            print(f"  {r[1]:3d}  {r[0]}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ ERROR — rolled back: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
