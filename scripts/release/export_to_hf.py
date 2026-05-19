from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.research_corpus.dataset_card import write_dataset_card
from scripts.ingest_corpus.research_corpus.governance import build_governance_artifacts
from scripts.ingest_corpus.research_corpus.io_utils import (
    export_parquet_shard,
    write_json,
)


def _clean_metadata(metadata: Any) -> dict[str, Any]:
    return metadata if isinstance(metadata, dict) else {}


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    meta = _clean_metadata(row.get("metadata"))
    document_id = str(row.get("document_id") or meta.get("doc_id") or "")
    content = str(row.get("content") or "")
    content_hash = str(row.get("content_hash") or row.get("id") or "")
    tags = meta.get("tags") or []
    if isinstance(tags, list):
        tags = ", ".join(str(tag) for tag in tags)
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
        "chunker_version": str(meta.get("chunker_version") or meta.get("version") or "supabase-export-v1"),
        "content_hash": content_hash,
        "content": content,
        "namespace": str(meta.get("namespace") or "unknown"),
        "source_name": str(meta.get("source_name") or meta.get("source") or "unknown"),
        "raw_text": content,
        "cleaned_text": content,
        "tags": tags,
        "collection_name": str(meta.get("collection_name") or ""),
    }


def _export_supabase_shards(output_dir: Path, shard_size: int, limit: int | None = None) -> dict[str, Any]:
    load_dotenv(".env.local", override=True)
    load_dotenv()

    from supabase import Client, ClientOptions, create_client  # type: ignore[reportMissingImports]

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SECRET_KEY missing")

    client: Client = create_client(
        supabase_url,
        supabase_key,
        options=ClientOptions(postgrest_client_timeout=120.0),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob("train-*.parquet"):
        existing.unlink()

    rows_for_manifest: list[dict[str, Any]] = []
    licenses = Counter()
    total_rows = 0
    shard_index = 0
    buffer: list[dict[str, Any]] = []
    page_size = 1000
    last_id: int | None = None

    while True:
        query = (
            client.table("knowledge_chunks")
            .select("id,document_id,content,content_hash,chunk_order,metadata")
            .order("id")
            .limit(page_size)
        )
        if last_id is not None:
            query = query.gt("id", last_id)
        response = query.execute()
        batch = cast(list[dict[str, Any]], response.data or [])
        if not batch:
            break

        for row in batch:
            normalized = _normalize_row(row)
            buffer.append(normalized)
            if len(rows_for_manifest) < 5000:
                rows_for_manifest.append(
                    {
                        "source": normalized["source"],
                        "source_url": normalized["source_url"],
                        "upstream_license": normalized["upstream_license"],
                        "retrieved_at": normalized["retrieved_at"],
                    }
                )
            licenses[cast(str, normalized["upstream_license"])] += 1
            total_rows += 1

            if len(buffer) >= shard_size:
                shard_path = output_dir / f"train-{shard_index:05d}.parquet"
                export_parquet_shard(shard_path, buffer)
                buffer = []
                shard_index += 1

            if limit and total_rows >= limit:
                break

        if limit and total_rows >= limit:
            break

        last_id = cast(int, batch[-1]["id"])
        if len(batch) < page_size:
            break

    if buffer:
        shard_path = output_dir / f"train-{shard_index:05d}.parquet"
        export_parquet_shard(shard_path, buffer)
        shard_index += 1

    return {
        "rows": total_rows,
        "shards": shard_index,
        "output_dir": str(output_dir),
        "manifest_rows": rows_for_manifest,
        "licenses": dict(licenses),
    }


def _publish_folder(
    repo_id: str,
    folder_path: Path,
    dataset_card_path: Path,
    manifest_path: Path,
    license_summary_path: Path,
    commit_message: str,
    private: bool,
) -> dict[str, Any]:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN or HUGGINGFACE_TOKEN missing")

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi  # type: ignore[reportMissingImports]

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)

    existing_files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    operations: list[Any] = []
    for path in existing_files:
        if path.endswith(".parquet") or path in {
            "README.md",
            "SOURCES_MANIFEST.yaml",
            "LICENSE_SUMMARY.md",
        }:
            operations.append(CommitOperationDelete(path_in_repo=path))

    for parquet_file in sorted(folder_path.glob("train-*.parquet")):
        operations.append(
            CommitOperationAdd(
                path_in_repo=parquet_file.name,
                path_or_fileobj=str(parquet_file),
            )
        )
    operations.extend(
        [
            CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(dataset_card_path)),
            CommitOperationAdd(path_in_repo="SOURCES_MANIFEST.yaml", path_or_fileobj=str(manifest_path)),
            CommitOperationAdd(path_in_repo="LICENSE_SUMMARY.md", path_or_fileobj=str(license_summary_path)),
        ]
    )

    api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=commit_message,
    )
    return {
        "repo_id": repo_id,
        "files_uploaded": len(list(folder_path.glob("train-*.parquet"))) + 3,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Low-memory export of local Supabase chunks to Hugging Face")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard-size", type=int, default=10000)
    parser.add_argument("--hf-repo-id", default="sanjeevafk/depthapi_technical_corpus")
    parser.add_argument(
        "--commit-message",
        default="Refresh dataset from local Supabase low-memory exporter",
    )
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    work_dir = repo_root / "data" / "hf_export"
    dataset_card_path = repo_root / "datasets" / "depthapi_technical_corpus" / "README.md"
    manifest_path = repo_root / "SOURCES_MANIFEST.yaml"
    license_summary_path = repo_root / "LICENSE_SUMMARY.md"

    export_summary = _export_supabase_shards(
        output_dir=work_dir,
        shard_size=args.shard_size,
        limit=args.limit,
    )
    write_dataset_card(dataset_card_path)
    build_governance_artifacts(
        export_summary["manifest_rows"],
        license_summary_path,
        manifest_path,
    )
    publish_summary = _publish_folder(
        repo_id=args.hf_repo_id,
        folder_path=work_dir,
        dataset_card_path=dataset_card_path,
        manifest_path=manifest_path,
        license_summary_path=license_summary_path,
        commit_message=args.commit_message,
        private=args.private,
    )

    summary = {
        "status": "success",
        "rows_exported": export_summary["rows"],
        "shards_written": export_summary["shards"],
        "estimated_rows_per_shard": args.shard_size,
        "repo_id": args.hf_repo_id,
        "publish": publish_summary,
    }
    write_json(repo_root / "data" / "hf_export" / "upload_summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
