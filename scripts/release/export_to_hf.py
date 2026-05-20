"""
export_to_hf.py — Export Supabase knowledge_chunks to Hugging Face.

Modes:
  Full export (default):
      python3 scripts/release/export_to_hf.py

  Collection-scoped export (incremental):
      python3 scripts/release/export_to_hf.py \\
          --collections "FastAPI Template - Full Stack" \\
          --no-publish          # skip HF upload, write parquet locally only

  Append new shards without deleting existing ones:
      python3 scripts/release/export_to_hf.py \\
          --collections "FastAPI Template - Full Stack" \\
          --append

  List available collections:
      python3 scripts/release/export_to_hf.py --list-collections
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.research_corpus.dataset_card import write_dataset_card
from scripts.ingest_corpus.research_corpus.governance import build_governance_artifacts
from scripts.ingest_corpus.research_corpus.io_utils import (
    export_parquet_shard,
    write_json,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PG_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------
def _clean_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


def _normalize_row(row: dict[str, Any], collection_name: str = "") -> dict[str, Any]:
    meta = _clean_metadata(row.get("metadata"))
    document_id = str(row.get("document_id") or meta.get("doc_id") or "")
    content = str(row.get("content") or "")
    content_hash = str(row.get("content_hash") or row.get("id") or "")
    tags = meta.get("tags") or []
    if isinstance(tags, list):
        tags = ", ".join(str(t) for t in tags)
    return {
        "chunk_id": str(row.get("id") or ""),
        "source": str(meta.get("source_name") or meta.get("source") or "unknown"),
        "source_url": str(meta.get("source_url") or ""),
        "upstream_license": str(
            meta.get("upstream_license")
            or meta.get("license")
            or meta.get("license_name")
            or "unknown"
        ),
        "document_id": document_id,
        "chunk_index": int(row.get("chunk_order") or 0),
        "retrieved_at": str(meta.get("retrieved_at") or ""),
        "chunker_version": str(
            meta.get("chunker_version") or meta.get("version") or "supabase-export-v1"
        ),
        "content_hash": content_hash,
        "content": content,
        "namespace": str(meta.get("namespace") or "unknown"),
        "source_name": str(meta.get("source_name") or meta.get("source") or "unknown"),
        "raw_text": content,
        "cleaned_text": content,
        "tags": tags,
        "collection_name": collection_name or str(meta.get("collection_name") or ""),
    }


# ---------------------------------------------------------------------------
# psycopg2 helpers (no HTTP timeouts)
# ---------------------------------------------------------------------------
def _pg_list_collections() -> dict[str, str]:
    """Return {name: id} for all collections."""
    conn = psycopg2.connect(PG_URL)
    cur = conn.cursor()
    cur.execute("SELECT id::text, name FROM knowledge_collections")
    result = {name: cid for cid, name in cur.fetchall()}
    conn.close()
    return result


def _pg_resolve_collections(names: list[str]) -> dict[str, str]:
    """Return {name: id} for the requested collection names."""
    all_cols = _pg_list_collections()
    resolved: dict[str, str] = {}
    for name in names:
        if name not in all_cols:
            available = ", ".join(sorted(all_cols))
            raise ValueError(f"Collection '{name}' not found. Available: {available}")
        resolved[name] = all_cols[name]
    return resolved


def _pg_doc_to_collection_map(collection_id_map: dict[str, str]) -> dict[str, str]:
    """Return {document_id: collection_name} for all docs in the given collections."""
    if not collection_id_map:
        return {}
    conn = psycopg2.connect(PG_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT id::text, collection_id::text FROM knowledge_documents "
        "WHERE collection_id::text = ANY(%s)",
        (list(collection_id_map.values()),),
    )
    id_to_coll_id = {doc_id: coll_id for doc_id, coll_id in cur.fetchall()}
    conn.close()
    coll_id_to_name = {v: k for k, v in collection_id_map.items()}
    return {
        doc_id: coll_id_to_name.get(coll_id, "")
        for doc_id, coll_id in id_to_coll_id.items()
    }


# ---------------------------------------------------------------------------
# Core export logic
# ---------------------------------------------------------------------------
def _export_shards(
    output_dir: Path,
    shard_size: int,
    collection_filter: list[str] | None = None,
    limit: int | None = None,
    shard_offset: int = 0,
) -> dict[str, Any]:
    """
    Stream knowledge_chunks to parquet shards via psycopg2 server-side cursor.

    Args:
        output_dir:       Directory to write train-NNNNN.parquet files.
        shard_size:       Max rows per shard.
        collection_filter: If given, only export chunks from these collection names.
        limit:            Optional hard cap on total rows exported.
        shard_offset:     Starting shard index (for append mode).
    """
    # Resolve collection → doc mapping
    collection_id_map: dict[str, str] = {}
    doc_to_collection: dict[str, str] = {}

    if collection_filter:
        collection_id_map = _pg_resolve_collections(collection_filter)
        print(f"  Collections: {list(collection_id_map.keys())}")
        doc_to_collection = _pg_doc_to_collection_map(collection_id_map)
        print(f"  Documents in scope: {len(doc_to_collection)}")

    output_dir.mkdir(parents=True, exist_ok=True)

    rows_for_manifest: list[dict[str, Any]] = []
    licenses: Counter[str] = Counter()
    total_rows = 0
    shard_index = shard_offset
    buffer: list[dict[str, Any]] = []

    # Use default transaction mode — server-side cursors require a transaction
    conn = psycopg2.connect(PG_URL)

    try:
        if collection_filter and collection_id_map:
            sql = """
                SELECT
                    kch.id::text        AS id,
                    kch.document_id::text AS document_id,
                    kch.content,
                    kch.content_hash,
                    kch.chunk_order,
                    kch.metadata
                FROM knowledge_chunks kch
                JOIN knowledge_documents kd ON kch.document_id = kd.id
                WHERE kd.collection_id::text = ANY(%s)
                  AND kch.deleted_at IS NULL
                ORDER BY kch.id
            """
            params: tuple[Any, ...] = (list(collection_id_map.values()),)
        else:
            sql = """
                SELECT
                    kch.id::text        AS id,
                    kch.document_id::text AS document_id,
                    kch.content,
                    kch.content_hash,
                    kch.chunk_order,
                    kch.metadata
                FROM knowledge_chunks kch
                WHERE kch.deleted_at IS NULL
                ORDER BY kch.id
            """
            params = ()

        with conn.cursor("export_cursor", cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.itersize = 2000
            cur.execute(sql, params or None)

            for pg_row in cur:
                row: dict[str, Any] = {
                    "id": pg_row["id"],
                    "document_id": pg_row["document_id"],
                    "content": pg_row["content"],
                    "content_hash": pg_row["content_hash"],
                    "chunk_order": pg_row["chunk_order"],
                    "metadata": pg_row["metadata"] or {},
                }
                coll_name = doc_to_collection.get(str(row["document_id"]), "")
                normalized = _normalize_row(row, collection_name=coll_name)
                buffer.append(normalized)

                if len(rows_for_manifest) < 5000:
                    rows_for_manifest.append({
                        "source": normalized["source"],
                        "source_url": normalized["source_url"],
                        "upstream_license": normalized["upstream_license"],
                        "retrieved_at": normalized["retrieved_at"],
                    })
                licenses[normalized["upstream_license"]] += 1
                total_rows += 1

                if len(buffer) >= shard_size:
                    shard_path = output_dir / f"train-{shard_index:05d}.parquet"
                    export_parquet_shard(shard_path, buffer)
                    print(f"  Wrote {shard_path.name} ({len(buffer)} rows)")
                    buffer = []
                    shard_index += 1

                if limit and total_rows >= limit:
                    break

    finally:
        conn.close()

    if buffer:
        shard_path = output_dir / f"train-{shard_index:05d}.parquet"
        export_parquet_shard(shard_path, buffer)
        print(f"  Wrote {shard_path.name} ({len(buffer)} rows)")
        shard_index += 1

    return {
        "rows": total_rows,
        "shards": shard_index - shard_offset,
        "output_dir": str(output_dir),
        "manifest_rows": rows_for_manifest,
        "licenses": dict(licenses),
    }


# ---------------------------------------------------------------------------
# HF publish
# ---------------------------------------------------------------------------
def _publish_folder(
    repo_id: str,
    folder_path: Path,
    dataset_card_path: Path,
    manifest_path: Path,
    license_summary_path: Path,
    commit_message: str,
    private: bool,
    replace_all_parquet: bool = True,
) -> dict[str, Any]:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN or HUGGINGFACE_TOKEN not set")

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi  # type: ignore

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)

    operations: list[Any] = []

    if replace_all_parquet:
        existing = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
        for path in existing:
            if path.endswith(".parquet") or path in {
                "README.md",
                "SOURCES_MANIFEST.yaml",
                "LICENSE_SUMMARY.md",
            }:
                operations.append(CommitOperationDelete(path_in_repo=path))

    local_shards = sorted(folder_path.glob("train-*.parquet"))
    for pf in local_shards:
        operations.append(CommitOperationAdd(path_in_repo=pf.name, path_or_fileobj=str(pf)))

    operations += [
        CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(dataset_card_path)),
        CommitOperationAdd(
            path_in_repo="SOURCES_MANIFEST.yaml", path_or_fileobj=str(manifest_path)
        ),
        CommitOperationAdd(
            path_in_repo="LICENSE_SUMMARY.md", path_or_fileobj=str(license_summary_path)
        ),
    ]

    api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=commit_message,
    )
    return {
        "repo_id": repo_id,
        "files_uploaded": len(local_shards) + 3,
        "mode": "full-replace" if replace_all_parquet else "append",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    load_dotenv(".env.local", override=True)
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Export Supabase chunks to Hugging Face dataset."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard-size", type=int, default=10_000)
    parser.add_argument("--hf-repo-id", default="sanjeevafk/depthapi_technical_corpus")
    parser.add_argument("--commit-message", default="Refresh dataset from local exporter")
    parser.add_argument("--private", action="store_true")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Export only these collection(s). Omit for full-DB export.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append new shards to HF repo; don't delete existing parquet files.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Write parquet locally only; skip HF upload.",
    )
    parser.add_argument(
        "--list-collections",
        action="store_true",
        help="Print all collection names and exit.",
    )
    args = parser.parse_args()

    if args.list_collections:
        cols = _pg_list_collections()
        print("Available collections:")
        for name in sorted(cols):
            print(f"  {name}")
        return

    repo_root = Path(__file__).resolve().parents[2]
    work_dir = repo_root / "data" / "hf_export"
    dataset_card_path = repo_root / "datasets" / "depthapi_technical_corpus" / "README.md"
    manifest_path = repo_root / "SOURCES_MANIFEST.yaml"
    license_summary_path = repo_root / "LICENSE_SUMMARY.md"

    if args.collections:
        if args.append:
            # Count existing shards to get the next shard index
            existing_shards = sorted(work_dir.glob("train-*.parquet"))
            shard_offset = len(existing_shards)
            out_dir = work_dir
            print(f"Append mode: {shard_offset} existing shard(s) in {work_dir}")
        else:
            # Collection-scoped full replace of local output dir
            out_dir = work_dir
            shard_offset = 0
            for f in work_dir.glob("train-*.parquet"):
                f.unlink()
            print(f"Collection export mode: cleared existing local shards")
    else:
        # Full-DB export
        out_dir = work_dir
        shard_offset = 0
        for f in work_dir.glob("train-*.parquet"):
            f.unlink()
        print("Full export mode: cleared existing local shards")

    print(f"\nExporting to: {out_dir}")
    if args.collections:
        print(f"Collections in scope: {args.collections}")

    export_summary = _export_shards(
        output_dir=out_dir,
        shard_size=args.shard_size,
        collection_filter=args.collections,
        limit=args.limit,
        shard_offset=shard_offset,
    )

    write_dataset_card(dataset_card_path)
    build_governance_artifacts(
        export_summary["manifest_rows"],
        license_summary_path,
        manifest_path,
    )

    print(
        f"\nExport complete: {export_summary['rows']} rows "
        f"in {export_summary['shards']} shard(s)"
    )

    if args.no_publish:
        print("--no-publish: skipping HF upload")
        write_json(out_dir / "export_summary.json", export_summary)
        return

    print(f"\nPublishing to: {args.hf_repo_id}")
    publish_summary = _publish_folder(
        repo_id=args.hf_repo_id,
        folder_path=out_dir,
        dataset_card_path=dataset_card_path,
        manifest_path=manifest_path,
        license_summary_path=license_summary_path,
        commit_message=args.commit_message,
        private=args.private,
        replace_all_parquet=not args.append,
    )

    summary = {
        "status": "success",
        "rows_exported": export_summary["rows"],
        "shards_written": export_summary["shards"],
        "shard_size": args.shard_size,
        "collections_filtered": args.collections,
        "append_mode": args.append,
        "repo_id": args.hf_repo_id,
        "publish": publish_summary,
    }
    write_json(work_dir / "upload_summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
