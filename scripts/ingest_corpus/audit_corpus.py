"""
audit_corpus.py — Validate and summarize the ingested RAG corpus.
Checks for empty content, missing source URLs, tag distributions, and overall health.
"""

import json
from collections import Counter
from pathlib import Path

def audit_corpus(json_path: str):
    path = Path(json_path)
    if not path.exists():
        print(f"Error: {json_path} not found.")
        return

    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    total = len(chunks)
    print(f"=== Corpus Audit: {path.name} ===")
    print(f"Total Chunks: {total}")

    if total == 0:
        return

    # Check for empty content
    empty = [c for c in chunks if not c.get("content") or len(c["content"].strip()) < 10]
    print(f"Empty/Tiny Chunks: {len(empty)}")

    # Source Distribution
    source_counts = Counter(c.get("source_name", "Unknown") for c in chunks)
    print("\nSources:")
    for src, count in source_counts.most_common():
        print(f"  - {src}: {count} ({count/total:.1%})")

    # Tag Distribution
    all_tags = []
    for c in chunks:
        all_tags.extend(c.get("tags", []))
    tag_counts = Counter(all_tags)
    print("\nTop Tags:")
    for tag, count in tag_counts.most_common(15):
        print(f"  - {tag}: {count}")

    # Check for missing URLs
    missing_urls = [c for c in chunks if not c.get("source_url")]
    print(f"\nMissing URLs: {len(missing_urls)}")

    # Deduplication Check (ID collision)
    ids = [c["id"] for c in chunks]
    unique_ids = set(ids)
    if len(ids) != len(unique_ids):
        print(f"CRITICAL: ID Collisions detected! {len(ids) - len(unique_ids)} duplicates.")
    else:
        print("Deduplication: OK (All IDs unique)")

    # Content length stats
    lengths = [len(c["content"]) for c in chunks]
    avg_len = sum(lengths) / total
    print(f"Average Content Length: {avg_len:.1f} chars")
    print(f"Min/Max Length: {min(lengths)} / {max(lengths)} chars")

if __name__ == "__main__":
    audit_corpus("data/rag/trusted/chunks.json")
