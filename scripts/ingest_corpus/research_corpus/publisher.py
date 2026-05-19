from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def publish_to_hugging_face(
    *,
    repo_id: str,
    split: str,
    parquet_path: Path,
    duplicate_path: Path,
    dataset_card_path: Path,
    manifest_path: Path,
    license_summary_path: Path,
    benchmark_dir: Path,
    commit_message: str,
    private: bool,
) -> dict:
    load_dotenv(".env.local", override=True)
    load_dotenv()

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN or HUGGINGFACE_TOKEN missing")

    from datasets import Dataset  # type: ignore[reportMissingImports]
    from huggingface_hub import CommitOperationAdd, HfApi  # type: ignore[reportMissingImports]
    import pandas as pd  # type: ignore[reportMissingImports]

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)

    frame = pd.read_parquet(parquet_path)
    dataset = Dataset.from_pandas(frame, preserve_index=False)
    dataset.push_to_hub(
        repo_id,
        split=split,
        token=token,
        private=private,
        commit_message=commit_message,
    )

    operations = [
        CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(dataset_card_path)),
        CommitOperationAdd(
            path_in_repo="SOURCES_MANIFEST.yaml",
            path_or_fileobj=str(manifest_path),
        ),
        CommitOperationAdd(
            path_in_repo="LICENSE_SUMMARY.md",
            path_or_fileobj=str(license_summary_path),
        ),
        CommitOperationAdd(
            path_in_repo="artifacts/removed_duplicates.parquet",
            path_or_fileobj=str(duplicate_path),
        ),
        CommitOperationAdd(
            path_in_repo="benchmarks/queries.jsonl",
            path_or_fileobj=str(benchmark_dir / "queries.jsonl"),
        ),
        CommitOperationAdd(
            path_in_repo="benchmarks/qrels.jsonl",
            path_or_fileobj=str(benchmark_dir / "qrels.jsonl"),
        ),
        CommitOperationAdd(
            path_in_repo="benchmarks/hard_negatives.jsonl",
            path_or_fileobj=str(benchmark_dir / "hard_negatives.jsonl"),
        ),
        CommitOperationAdd(
            path_in_repo="benchmarks/benchmark_results.md",
            path_or_fileobj=str(benchmark_dir / "benchmark_results.md"),
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
        "split": split,
        "rows_pushed": int(len(frame)),
        "artifacts_uploaded": len(operations) + 1,
    }
