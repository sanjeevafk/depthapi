"""export_to_hf.py — robust export of PostgreSQL knowledge_chunks to Hugging Face.

Usage patterns:

  Full export to a new snapshot folder (safe default):
      python3 scripts/release/export_to_hf.py

  Export only selected collections:
      python3 scripts/release/export_to_hf.py \
          --collections "FastAPI Template - Full Stack"

  Append-style run without deleting anything:
      python3 scripts/release/export_to_hf.py \
          --collections "FastAPI Template - Full Stack" \
          --append

  Force a destructive root rebuild:
      python3 scripts/release/export_to_hf.py \
          --force-rebuild

  Export locally only:
      python3 scripts/release/export_to_hf.py --no-publish

  List available collections:
      python3 scripts/release/export_to_hf.py --list-collections

Design notes:
- Exports are streamed through a server-side cursor.
- A new run directory is created every execution.
- Root deletion on Hugging Face only happens with --force-rebuild.
- Default publishing writes to a snapshot folder to avoid accidental loss.
- Collection lookup is done in SQL; no extra document-to-collection map is built.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.research_corpus.governance import build_governance_artifacts
from scripts.ingest_corpus.research_corpus.io_utils import (
    export_parquet_shard,
    write_json,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PG_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DEFAULT_HF_REPO_ID = "sanjeevafk/depthapi_technical_corpus"
WATERMARK_FILENAME = "last_export_watermark.json"

LOG = logging.getLogger("export_to_hf")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_tag() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env() -> None:
    load_dotenv(".env.local", override=True)
    load_dotenv()


def _env_value(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _pg_url() -> str:
    return (
        _env_value("DATABASE_URL", "PG_URL", default=DEFAULT_PG_URL) or DEFAULT_PG_URL
    )


def _clean_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_row(row: dict[str, Any], collection_name: str = "") -> dict[str, Any]:
    meta = _clean_metadata(row.get("metadata"))
    content = str(row.get("content") or "")
    chunk_id = str(row.get("id") or "")
    content_hash = str(row.get("content_hash") or "").strip()
    if not content_hash:
        content_hash = _sha256_text(content) if content else chunk_id

    tags = meta.get("tags") or []
    if isinstance(tags, list):
        tags = ", ".join(str(t) for t in tags)
    elif tags is None:
        tags = str(tags)

    return {
        "chunk_id": chunk_id,
        "source": str(meta.get("source_name") or meta.get("source") or "unknown"),
        "source_url": str(meta.get("source_url") or ""),
        "upstream_license": str(
            meta.get("upstream_license")
            or meta.get("license")
            or meta.get("license_name")
            or "unknown"
        ),
        "document_id": str(row.get("document_id") or meta.get("doc_id") or ""),
        "chunk_index": int(row.get("chunk_order") or 0),
        "retrieved_at": str(meta.get("retrieved_at") or ""),
        "chunker_version": str(
            meta.get("chunker_version") or meta.get("version") or "postgres-export-v2"
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


def _is_nonempty_content(content: Any) -> bool:
    return bool(str(content or "").strip())


# ---------------------------------------------------------------------------
# Watermark helpers
# ---------------------------------------------------------------------------


def _watermark_path(work_dir: Path) -> Path:
    return work_dir / WATERMARK_FILENAME


def _load_watermark(work_dir: Path) -> datetime | None:
    """Return the last successful export timestamp, or None if no watermark exists."""
    path = _watermark_path(work_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        ts = data.get("exported_at")
        if ts:
            return datetime.fromisoformat(ts)
    except Exception as exc:
        LOG.warning("Could not read watermark file %s: %s", path, exc)
    return None


def _save_watermark(work_dir: Path, exported_at: datetime, rows_exported: int) -> None:
    """Persist the watermark so future --append runs know where to start."""
    path = _watermark_path(work_dir)
    path.write_text(
        json.dumps(
            {
                "exported_at": exported_at.isoformat(),
                "rows_exported": rows_exported,
                "written_at": _utc_now().isoformat(),
            },
            indent=2,
        )
    )
    LOG.info("Watermark saved: exported_at=%s rows=%s", exported_at.isoformat(), rows_exported)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


@contextmanager
def _db_connection(
    pg_url: str | None = None, attempts: int = 3, delay_seconds: float = 2.0
):
    url = pg_url or _pg_url()
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            conn = psycopg2.connect(url)
            try:
                yield conn
            finally:
                conn.close()
            return
        except (
            Exception
        ) as exc:  # pragma: no cover - connect failures are environment-specific
            last_exc = exc
            if attempt < attempts:
                LOG.warning(
                    "Database connection failed (attempt %s/%s): %s",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(delay_seconds * attempt)

    assert last_exc is not None
    raise last_exc


def _pg_fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with _db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _pg_list_collections() -> dict[str, str]:
    rows = _pg_fetchall(
        "SELECT id::text, name FROM knowledge_collections ORDER BY name"
    )
    return {name: cid for cid, name in rows}


def _pg_resolve_collections(names: list[str]) -> dict[str, str]:
    all_cols = _pg_list_collections()
    resolved: dict[str, str] = {}
    for name in names:
        if name not in all_cols:
            available = ", ".join(sorted(all_cols))
            raise ValueError(f"Collection '{name}' not found. Available: {available}")
        resolved[name] = all_cols[name]
    return resolved


# ---------------------------------------------------------------------------
# Export logic
# ---------------------------------------------------------------------------


def _export_shards(
    output_dir: Path,
    shard_size: int,
    collection_filter: list[str] | None = None,
    limit: int | None = None,
    skip_empty_content: bool = True,
    since_timestamp: datetime | None = None,
) -> dict[str, Any]:
    """
    Stream knowledge_chunks into parquet shards.

    The output directory is treated as an isolated run directory.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    collection_id_map: dict[str, str] = {}
    collection_ids: list[str] = []
    if collection_filter:
        collection_id_map = _pg_resolve_collections(collection_filter)
        collection_ids = list(collection_id_map.values())
        LOG.info("Collections in scope: %s", ", ".join(collection_id_map.keys()))

    rows_for_manifest: list[dict[str, Any]] = []
    licenses: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    total_rows = 0
    skipped_empty = 0
    skipped_invalid = 0
    shard_index = 0
    buffer: list[dict[str, Any]] = []

    if since_timestamp:
        LOG.info(
            "Delta mode: exporting chunks created after %s",
            since_timestamp.isoformat(),
        )
    else:
        LOG.info("Full mode: exporting all chunks")

    sql = """
        SELECT
            kch.id::text AS id,
            kch.document_id::text AS document_id,
            kch.content,
            kch.content_hash,
            kch.chunk_order,
            kch.metadata,
            COALESCE(kc.name, '') AS collection_name,
            kch.created_at
        FROM knowledge_chunks kch
        LEFT JOIN knowledge_documents kd
            ON kch.document_id = kd.id
        LEFT JOIN knowledge_collections kc
            ON kd.collection_id = kc.id
        WHERE kch.deleted_at IS NULL
    """
    params: list[Any] = []
    if since_timestamp:
        sql += " AND kch.created_at > %s"
        params.append(since_timestamp)
    if collection_ids:
        sql += " AND kd.collection_id::text = ANY(%s)"
        params.append(collection_ids)
    sql += " ORDER BY kch.created_at, kch.id"

    run_tag = output_dir.name

    # Track the maximum created_at seen so we can update the watermark
    max_created_at: datetime | None = None

    with _db_connection() as conn:
        with conn.cursor(
            "export_cursor", cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.itersize = 2000
            cur.execute(sql, params or ())

            for pg_row in cur:
                content = pg_row.get("content")
                if skip_empty_content and not _is_nonempty_content(content):
                    skipped_empty += 1
                    continue

                row_created_at = pg_row.get("created_at")
                if row_created_at and (
                    max_created_at is None or row_created_at > max_created_at
                ):
                    max_created_at = row_created_at

                row: dict[str, Any] = {
                    "id": pg_row.get("id"),
                    "document_id": pg_row.get("document_id"),
                    "content": content,
                    "content_hash": pg_row.get("content_hash"),
                    "chunk_order": pg_row.get("chunk_order"),
                    "metadata": pg_row.get("metadata") or {},
                }

                try:
                    normalized = _normalize_row(
                        row, collection_name=str(pg_row.get("collection_name") or "")
                    )
                except Exception as exc:
                    skipped_invalid += 1
                    LOG.warning("Skipping malformed row %s: %s", row.get("id"), exc)
                    continue

                buffer.append(normalized)

                if len(rows_for_manifest) < 5000:
                    rows_for_manifest.append(
                        {
                            "source": normalized["source"],
                            "source_url": normalized["source_url"],
                            "upstream_license": normalized["upstream_license"],
                            "retrieved_at": normalized["retrieved_at"],
                            "collection_name": normalized["collection_name"],
                        }
                    )

                licenses[normalized["upstream_license"]] += 1
                source_counts[normalized["source"]] += 1
                total_rows += 1

                if len(buffer) >= shard_size:
                    shard_path = (
                        output_dir / f"train-{run_tag}-{shard_index:05d}.parquet"
                    )
                    export_parquet_shard(shard_path, buffer)
                    LOG.info("Wrote %s (%s rows)", shard_path.name, len(buffer))
                    buffer = []
                    shard_index += 1

                if limit is not None and total_rows >= limit:
                    break

    if buffer:
        shard_path = output_dir / f"train-{run_tag}-{shard_index:05d}.parquet"
        export_parquet_shard(shard_path, buffer)
        LOG.info("Wrote %s (%s rows)", shard_path.name, len(buffer))
        shard_index += 1

    return {
        "run_tag": run_tag,
        "rows": total_rows,
        "shards": shard_index,
        "skipped_empty": skipped_empty,
        "skipped_invalid": skipped_invalid,
        "output_dir": str(output_dir),
        "manifest_rows": rows_for_manifest,
        "licenses": dict(licenses),
        "source_counts": dict(source_counts),
        "collections_filtered": collection_filter,
        # Store as ISO string for JSON serialization; keep raw datetime internally
        "max_created_at": max_created_at,
        "max_created_at_iso": max_created_at.isoformat() if max_created_at else None,
        "since_timestamp": since_timestamp.isoformat() if since_timestamp else None,
    }


# ---------------------------------------------------------------------------
# Hugging Face publish
# ---------------------------------------------------------------------------


def _hf_token() -> str:
    token = _env_value("HF_TOKEN", "HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN or HUGGINGFACE_TOKEN not set")
    return token


def _publish_folder(
    repo_id: str,
    folder_path: Path,
    manifest_path: Path,
    license_summary_path: Path,
    commit_message: str,
    private: bool,
    replace_all_parquet: bool = False,
    repo_subdir: str = "",
) -> dict[str, Any]:
    _load_env()
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi  # type: ignore

    api = HfApi(token=_hf_token())
    api.create_repo(
        repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True
    )

    operations: list[Any] = []
    subdir_prefix = repo_subdir.strip("/")
    if subdir_prefix:
        subdir_prefix += "/"

    if replace_all_parquet and not subdir_prefix:
        existing = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
        for path in existing:
            if path.endswith(".parquet") or path in {
                "SOURCES_MANIFEST.yaml",
                "LICENSE_SUMMARY.md",
            }:
                operations.append(CommitOperationDelete(path_in_repo=path))

    local_shards = sorted(folder_path.glob("train-*.parquet"))
    if not local_shards:
        raise RuntimeError(f"No parquet shards found in {folder_path}")

    for pf in local_shards:
        repo_path = f"{subdir_prefix}{pf.name}" if subdir_prefix else pf.name
        operations.append(
            CommitOperationAdd(path_in_repo=repo_path, path_or_fileobj=str(pf))
        )

    # Keep the release docs at repo root; they describe the latest run.
    operations += [
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
        "mode": "full-replace"
        if replace_all_parquet and not subdir_prefix
        else "snapshot-or-append",
        "repo_subdir": repo_subdir,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_run_directory(
    base_dir: Path, collections: list[str] | None, append: bool
) -> Path:
    tag = _run_tag()
    scope = (
        "all"
        if not collections
        else "_".join(c.replace(" ", "_")[:24] for c in collections)
    )
    suffix = "append" if append else "export"
    return base_dir / "runs" / f"{tag}_{scope}_{suffix}"


def main() -> None:
    _load_env()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Export PostgreSQL chunks to Hugging Face dataset."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Hard cap on exported rows."
    )
    parser.add_argument(
        "--shard-size", type=int, default=10_000, help="Rows per parquet shard."
    )
    parser.add_argument(
        "--hf-repo-id", default=DEFAULT_HF_REPO_ID, help="HF dataset repo ID."
    )
    parser.add_argument(
        "--commit-message",
        default="Refresh dataset from local exporter",
        help="Commit message for Hugging Face.",
    )
    parser.add_argument(
        "--private", action="store_true", help="Create or keep the dataset private."
    )
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
        help="Keep existing repository parquet files. Safe by default unless combined with --force-rebuild.",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Destructively replace existing root parquet files in the HF repo.",
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
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include chunks with empty content.",
    )
    args = parser.parse_args()

    if args.force_rebuild and args.append:
        raise SystemExit("--force-rebuild and --append are mutually exclusive")

    if args.list_collections:
        cols = _pg_list_collections()
        print("Available collections:")
        for name in sorted(cols):
            print(f"  {name}")
        return

    repo_root = _repo_root()
    work_dir = repo_root / "data" / "hf_export"
    work_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = repo_root / "SOURCES_MANIFEST.yaml"
    license_summary_path = repo_root / "LICENSE_SUMMARY.md"

    run_dir = _build_run_directory(work_dir, args.collections, args.append)
    run_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("Export target: %s", run_dir)
    LOG.info(
        "Publishing to: %s", args.hf_repo_id if not args.no_publish else "(skipped)"
    )

    # Load watermark for delta exports
    since_ts: datetime | None = None
    if args.append:
        since_ts = _load_watermark(work_dir)
        if since_ts:
            LOG.info("Append mode: watermark found, exporting chunks since %s", since_ts.isoformat())
        else:
            LOG.warning(
                "Append mode: no watermark found — performing full export on first run. "
                "Run with --force-rebuild instead if you want to replace root shards."
            )

    export_summary = _export_shards(
        output_dir=run_dir,
        shard_size=args.shard_size,
        collection_filter=args.collections,
        limit=args.limit,
        skip_empty_content=not args.include_empty,
        since_timestamp=since_ts,
    )

    build_governance_artifacts(
        export_summary["manifest_rows"],
        license_summary_path,
        manifest_path,
    )

    export_summary_path = run_dir / "export_summary.json"
    # Serialize: replace raw datetime objects with ISO strings for JSON
    json_safe_summary = {
        **export_summary,
        "max_created_at": export_summary["max_created_at_iso"],
    }
    write_json(export_summary_path, json_safe_summary)

    LOG.info(
        "Export complete: %s rows in %s shard(s) | skipped_empty=%s | skipped_invalid=%s",
        export_summary["rows"],
        export_summary["shards"],
        export_summary["skipped_empty"],
        export_summary["skipped_invalid"],
    )

    if args.no_publish:
        LOG.info("--no-publish set; skipping HF upload")
        return

    # Routing:
    #   --force-rebuild  → delete all root parquet, re-upload full export to root
    #   --append         → delta export; upload new shards to root (timestamped names
    #                      guarantee no collision with existing shards)
    #   default          → archive snapshot to subdirectory (safe read-only copy)
    if args.force_rebuild:
        repo_subdir = ""
        replace_all_parquet = True
    elif args.append:
        # Incremental: shards carry unique run_tag names, so they won't overwrite
        # existing root shards. No deletion needed.
        repo_subdir = ""
        replace_all_parquet = False
    else:
        repo_subdir = f"snapshots/{export_summary['run_tag']}"
        replace_all_parquet = False

    publish_summary = _publish_folder(
        repo_id=args.hf_repo_id,
        folder_path=run_dir,
        manifest_path=manifest_path,
        license_summary_path=license_summary_path,
        commit_message=args.commit_message,
        private=args.private,
        replace_all_parquet=replace_all_parquet,
        repo_subdir=repo_subdir,
    )

    summary = {
        "status": "success",
        "run_tag": export_summary["run_tag"],
        "rows_exported": export_summary["rows"],
        "shards_written": export_summary["shards"],
        "shard_size": args.shard_size,
        "collections_filtered": args.collections,
        "append_mode": args.append,
        "force_rebuild": args.force_rebuild,
        "repo_id": args.hf_repo_id,
        "repo_subdir": repo_subdir,
        "publish": publish_summary,
    }
    write_json(run_dir / "upload_summary.json", summary)
    LOG.info("Publish complete: %s", summary)

    # Persist watermark so next --append run knows where to start
    if args.append or args.force_rebuild:
        new_watermark_ts = export_summary.get("max_created_at") or _utc_now()
        _save_watermark(work_dir, new_watermark_ts, export_summary["rows"])


if __name__ == "__main__":
    main()
