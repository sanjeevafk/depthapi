from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


SOURCE_RULES: list[tuple[str, dict[str, str]]] = [
    ("System Design Primer", {"namespace": "system_design", "source_family": "system-design-primer", "dataset_bucket": "system-design-primer"}),
    ("System Design Handbook - Aman Barnwal", {"namespace": "system_design", "source_family": "system-design-book", "dataset_bucket": "open-access-books-v1"}),
    ("Cracking the coding interview 6th edition", {"namespace": "system_design", "source_family": "interview-prep", "dataset_bucket": "open-access-books-v1"}),
    ("Introduction to Algorithms", {"namespace": "open_access_books", "source_family": "algorithms-book", "dataset_bucket": "open-access-books-v1"}),
    ("AlgorithmsNotesForProfessionals", {"namespace": "open_access_books", "source_family": "goalkicker-notes", "dataset_bucket": "open-access-books-v1"}),
    ("Deep Learning with Python", {"namespace": "open_access_books", "source_family": "ml-book", "dataset_bucket": "open-access-books-v1"}),
    ("Python Data Science Handbook", {"namespace": "open_access_books", "source_family": "data-science-book", "dataset_bucket": "open-access-books-v1"}),
    ("Data Engineering Cookbook", {"namespace": "open_access_books", "source_family": "data-engineering-book", "dataset_bucket": "open-access-books-v1"}),
    ("Eloquent_JavaScript", {"namespace": "open_access_books", "source_family": "javascript-book", "dataset_bucket": "open-access-books-v1"}),
    ("Use The Index, Luke", {"namespace": "open_access_books", "source_family": "sql-book", "dataset_bucket": "open-access-books-v1"}),
    ("SQL Tutorial", {"namespace": "open_access_books", "source_family": "sql-book", "dataset_bucket": "open-access-books-v1"}),
]

GOALKICKER_SUFFIX = "NotesForProfessionals"


def _headers() -> dict[str, str]:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    key = os.environ["SUPABASE_SECRET_KEY"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/knowledge_chunks"


def classify_source(source_name: str) -> dict[str, str]:
    for prefix, payload in SOURCE_RULES:
        if source_name == prefix:
            return payload
    if source_name.endswith(GOALKICKER_SUFFIX):
        return {"namespace": "open_access_books", "source_family": "goalkicker-notes", "dataset_bucket": "open-access-books-v1"}
    if "Postmortem" in source_name or "Postmortems" in source_name:
        return {"namespace": "system_design", "source_family": "postmortems", "dataset_bucket": "postmortems"}
    return {"namespace": "open_access_books", "source_family": "unclassified-book", "dataset_bucket": "open-access-books-v1"}


def fetch_unknown_rows(limit: int | None = None) -> list[dict[str, Any]]:
    base = _base_url()
    headers = _headers()
    rows: list[dict[str, Any]] = []
    last_id = ""
    page_size = 1000

    while True:
        params = {
            "select": "id,metadata",
            "order": "id",
            "limit": str(page_size),
        }
        if last_id:
            params["id"] = f"gt.{last_id}"
        response = requests.get(base, headers=headers, params=params, timeout=120)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        for row in batch:
            last_id = row["id"]
            metadata = row.get("metadata") or {}
            if str(metadata.get("namespace") or "unknown") != "unknown":
                continue
            rows.append({"id": row["id"], "metadata": metadata})
            if limit and len(rows) >= limit:
                return rows
        if len(batch) < page_size:
            break
    return rows


def normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    by_namespace = Counter()
    by_family = Counter()

    for row in rows:
        metadata = dict(row.get("metadata") or {})
        source_name = str(metadata.get("source_name") or metadata.get("source") or "unknown")
        classification = classify_source(source_name)
        metadata["namespace"] = classification["namespace"]
        metadata["source_family"] = classification["source_family"]
        metadata["dataset_bucket"] = classification["dataset_bucket"]
        metadata.setdefault("chunker_version", "legacy-supabase-import")
        metadata.setdefault("retrieved_at", "legacy-unknown")
        metadata.setdefault("upstream_license", metadata.get("license") or metadata.get("license_name") or "unknown")
        normalized.append({"id": row["id"], "metadata": metadata})
        by_namespace[classification["namespace"]] += 1
        by_family[classification["source_family"]] += 1

    summary = {
        "rows_normalized": len(normalized),
        "namespace_targets": dict(by_namespace),
        "source_family_targets": dict(by_family),
    }
    return normalized, summary


def apply_updates(rows: list[dict[str, Any]], dry_run: bool = False) -> dict[str, Any]:
    normalized, summary = normalize_rows(rows)
    if dry_run or not normalized:
        return summary

    base = _base_url()
    headers = _headers()
    batch_size = 500
    for start in range(0, len(normalized), batch_size):
        batch = normalized[start : start + batch_size]
        response = requests.post(
            base,
            headers={**headers, "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "id"},
            data=json.dumps(batch),
            timeout=120,
        )
        response.raise_for_status()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize unknown Supabase corpus metadata")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default="docs/corpus/metadata_normalization_summary.json")
    args = parser.parse_args()

    rows = fetch_unknown_rows(limit=args.limit)
    summary = apply_updates(rows, dry_run=args.dry_run)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
