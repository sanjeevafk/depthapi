from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


DATASET_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "content": {"root": "datasets/content/files", "status": "ingested"},
    "website": {"root": "datasets/website/content", "status": "ingested"},
    "cpython": {"root": "datasets/cpython/Doc", "status": "ingested"},
    "node": {"root": "datasets/node/doc/api", "status": "ingested"},
    "react.dev": {"root": "datasets/react.dev/src/content", "status": "ingested"},
    "postmortems": {"root": "datasets/post-mortems + datasets/postmortems", "status": "ingested"},
    "system-design-primer": {"root": "datasets/system-design-primer", "status": "ingested"},
    "full-stack-fastapi-template": {"root": "datasets/full-stack-fastapi-template", "status": "partially_ingested"},
    "open-access-books-v1": {"root": "datasets/open-access-books-v1 + selected books", "status": "partially_ingested"},
    "opea-docs": {"root": "datasets/opea-docs", "status": "present_not_ingested"},
    "coding-interview-university": {"root": "datasets/coding-interview-university", "status": "present_not_ingested"},
    "code_search_net": {"root": "datasets/code_search_net", "status": "present_not_ingested"},
    "CodeAlpaca-20k": {"root": "datasets/CodeAlpaca-20k", "status": "present_not_ingested"},
    "awesome": {"root": "datasets/awesome", "status": "present_not_ingested"},
}


def _headers() -> dict[str, str]:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    key = os.environ["SUPABASE_SECRET_KEY"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


def _guess_bucket(source: str, url: str, namespace: str) -> str:
    low = f"{source} {url} {namespace}".lower()
    if "mdn" in low or "developer.mozilla.org" in low:
        return "content"
    if "kubernetes" in low or "/website/" in low:
        return "website"
    if "cpython" in low or "docs.python.org" in low:
        return "cpython"
    if "node.js api docs" in low or "nodejs api docs" in low:
        return "node"
    if "react.dev" in low:
        return "react.dev"
    if "postmortem" in low:
        return "postmortems"
    if "system design primer" in low:
        return "system-design-primer"
    if "full-stack fastapi template" in low:
        return "full-stack-fastapi-template"
    if "notesforprofessionals" in low or "deep learning with python" in low or "eloquent_javascript" in low:
        return "open-access-books-v1"
    return "unmapped"


def fetch_rows() -> list[dict[str, Any]]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/knowledge_chunks"
    headers = _headers()
    rows: list[dict[str, Any]] = []
    last_id = ""
    while True:
        params = {"select": "id,metadata", "order": "id", "limit": "1000"}
        if last_id:
            params["id"] = f"gt.{last_id}"
        response = requests.get(base, headers=headers, params=params, timeout=120)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 1000:
            break
    return rows


def main() -> None:
    load_dotenv(".env.local", override=True)
    load_dotenv()
    rows = fetch_rows()
    bucket_counts = Counter()
    namespace_counts = Counter()
    source_counts = Counter()

    for row in rows:
        metadata = row.get("metadata") or {}
        source = str(metadata.get("source_name") or metadata.get("source") or "unknown")
        url = str(metadata.get("source_url") or "")
        namespace = str(metadata.get("namespace") or "unknown")
        bucket = str(metadata.get("dataset_bucket") or "").strip() or _guess_bucket(source, url, namespace)
        bucket_counts[bucket] += 1
        namespace_counts[namespace] += 1
        source_counts[source] += 1

    audit_json = {
        "total_chunks": len(rows),
        "bucket_counts": dict(bucket_counts),
        "namespace_counts": dict(namespace_counts),
        "top_sources": source_counts.most_common(30),
    }
    json_path = Path("docs/corpus/DATASET_CHUNK_AUDIT.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(audit_json, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Dataset Chunk Audit",
        "",
        f"Total local Supabase chunks: **{len(rows):,}**",
        "",
        "| Dataset bucket | Local root | Status | Chunk count |",
        "| --- | --- | --- | ---: |",
    ]
    buckets = set(DATASET_DESCRIPTIONS) | set(bucket_counts)
    for bucket in sorted(buckets):
        desc = DATASET_DESCRIPTIONS.get(bucket, {"root": "n/a", "status": "unmapped"})
        lines.append(
            f"| {bucket} | {desc['root']} | {desc['status']} | {bucket_counts.get(bucket, 0):,} |"
        )
    lines.extend(
        [
            "",
            "## Namespace Counts",
            "",
            "| Namespace | Chunk count |",
            "| --- | ---: |",
        ]
    )
    for namespace, count in namespace_counts.most_common():
        lines.append(f"| {namespace} | {count:,} |")
    lines.extend(
        [
            "",
            "## Top Sources",
            "",
            "| Source | Chunk count |",
            "| --- | ---: |",
        ]
    )
    for source, count in source_counts.most_common(30):
        lines.append(f"| {source} | {count:,} |")

    md_path = Path("docs/corpus/DATASET_CHUNK_AUDIT.md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "total_chunks": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
