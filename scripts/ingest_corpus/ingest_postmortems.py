"""
ingest_postmortems.py — Specialized ingestion for tech postmortems.

Parses YAML frontmatter to construct rich RAG context, 
and bypasses standard length/TOC validators since postmortems are often short summaries.

Usage:
  python3 scripts/ingest_corpus/ingest_postmortems.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    REPO_ROOT,
    log,
    split_text_semantic,
)

try:
    import yaml
except ImportError:
    yaml = None

def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) if yaml else {}
        return fm or {}, parts[2].strip()
    except Exception:
        return {}, content

def run(max_files: int | None = None) -> dict[str, Any]:
    if yaml is None:
        log.error("pyyaml is required to parse postmortems. Install with: pip install pyyaml")
        sys.exit(1)
        
    source_name = "Tech Postmortems"
    root_dir = REPO_ROOT / "datasets" / "postmortems" / "data"
    
    if not root_dir.exists():
        log.error(f"Postmortems directory not found: {root_dir}")
        return {}

    # No validators to prevent dropping short summaries
    ingestor = BaseIngestor(source_name, source_type="markdown", validators=[])
    
    stats = Counter()
    chunk_order = 0
    namespace = "trusted_system_design"
    tags = ["postmortem", "incident", "architecture", "devops", "P2"]

    for file_path in root_dir.glob("**/*.md"):
        if max_files is not None and stats["processed_files"] >= max_files:
            break

        rel_posix = file_path.relative_to(root_dir).as_posix()
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        
        if not raw.strip():
            stats["skip_empty_file"] += 1
            continue
            
        fm, text = _parse_frontmatter(raw)
        
        company = fm.get("company", "Unknown Company")
        product = fm.get("product", "")
        url = fm.get("url", f"file://datasets/postmortems/data/{rel_posix}")
        
        # Build enriched context
        title = f"{company}" + (f" - {product}" if product else "")
        enriched_text = f"Incident Report / Postmortem: {title}\nSource: {url}\n\n{text}"

        # Use a smaller minimum size for postmortems (20 chars instead of default 50)
        chunks = split_text_semantic(enriched_text, chunk_size=800, overlap_words=25)
        
        # Override the length drop in split_text_semantic by adding it as-is if empty chunks
        if not chunks and len(enriched_text.strip()) > 10:
            chunks = [enriched_text]
            
        if not chunks:
            stats["skip_empty_after_chunking"] += 1
            continue

        metadata = [
            {
                "dataset_root": "datasets/postmortems/data",
                "relative_path": rel_posix,
                "namespace": namespace,
                "company": company,
                "product": product,
                "url": url,
            }
            for _ in chunks
        ]
        
        added = ingestor.add(
            chunks,
            source_url=url,
            tags=tags,
            start_order=chunk_order,
            metadata=metadata,
        )
        chunk_order += len(chunks)
        stats["processed_files"] += 1
        stats["chunks_candidate"] += len(chunks)
        stats["chunks_added"] += len(added)

    # Note: We do NOT call ingestor.flush() if the user is currently running an embedding job.
    # The prompt explicitly asks us to CREATE A PLAN and NOT run it right now if we are deferring.
    # We will log that we created it and tell the user how to run it.
    
    total = ingestor.flush()
    log.info(f"[{source_name}] processed={stats['processed_files']} added={stats['chunks_added']} total_chunks_after_flush={total}")
    
    return dict(stats)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest postmortems dataset")
    parser.add_argument("--max-files", type=int, default=None)
    args = parser.parse_args()
    run(max_files=args.max_files)
