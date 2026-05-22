"""ingest_opea_docs.py — Ingest local OPEA markdown/rst docs into chunks.json.

Pipeline shape in this repo:
1) Parse + chunk local files into data/rag/trusted/chunks.json
2) Backfill chunks.json into Supabase via backfill_supabase_rag.py

Usage:
    .venv/bin/python scripts/ingest_corpus/ingest_opea_docs.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    REPO_ROOT,
    log,
    make_link_ratio_validator,
    make_min_word_validator,
    split_text_semantic,
)

DATA_ROOT = REPO_ROOT / "datasets" / "opea-docs"
NAMESPACE = "ai_ref_knowledge"
SOURCE_NAME = "OPEA Documentation"
TAGS = ["opea", "enterprise-ai", "genai", "docs", "P1"]


def _clean_rst_noise(text: str) -> str:
    """Remove high-noise Sphinx/rst directives while preserving prose."""
    cleaned = text
    cleaned = re.sub(r"(?m)^\s*\.\.\s+(toctree|code-block|note|warning|tip|important|seealso|image)::.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*:[a-zA-Z0-9_-]+:\s*.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\.{2}\s+_.*?:\s*https?://\S+\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[=\-~`^\"']{3,}\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _collect_files() -> list[Path]:
    md_files = sorted(DATA_ROOT.rglob("*.md"))
    rst_files = sorted(DATA_ROOT.rglob("*.rst"))
    return md_files + rst_files


def run() -> dict[str, object]:
    if not DATA_ROOT.exists():
        raise FileNotFoundError(f"OPEA dataset directory missing: {DATA_ROOT}")

    files = _collect_files()
    log.info("ingest_opea: discovered %s document files", len(files))

    validators = [
        make_min_word_validator(20),
        make_link_ratio_validator(0.35),
    ]
    ingestor = BaseIngestor(
        source_name=SOURCE_NAME,
        source_type="markdown",
        validators=validators,
    )

    stats: Counter[str] = Counter()
    total_order = 0

    for path in files:
        rel = path.relative_to(DATA_ROOT).as_posix()
        raw = path.read_text(encoding="utf-8", errors="ignore")
        cleaned = _clean_rst_noise(raw)
        if not cleaned:
            stats["empty_files"] += 1
            continue

        chunks = split_text_semantic(cleaned, chunk_size=800, overlap_words=25)
        if not chunks:
            stats["empty_after_chunking"] += 1
            continue

        metadata = [
            {
                "namespace": NAMESPACE,
                "dataset_root": "datasets/opea-docs",
                "relative_path": rel,
                "language": "en",
                "chunker_version": "opea-semantic-v1",
            }
            for _ in chunks
        ]
        source_url = f"file://datasets/opea-docs/{rel}"
        added = ingestor.add(
            chunks,
            source_url=source_url,
            tags=TAGS,
            start_order=total_order,
            metadata=metadata,
        )

        total_order += len(chunks)
        stats["files_processed"] += 1
        stats["chunks_candidate"] += len(chunks)
        stats["chunks_added"] += len(added)
        stats["chunks_rejected"] += len(chunks) - len(added)
        log.info("ingest_opea: processed %s -> %s/%s chunks added", rel, len(added), len(chunks))

    total_after_flush = ingestor.flush()
    result: dict[str, object] = {
        "dataset_root": str(DATA_ROOT),
        "namespace": NAMESPACE,
        "source_name": SOURCE_NAME,
        "files_discovered": len(files),
        "files_processed": int(stats["files_processed"]),
        "chunks_candidate": int(stats["chunks_candidate"]),
        "chunks_added": int(stats["chunks_added"]),
        "chunks_rejected": int(stats["chunks_rejected"]),
        "empty_files": int(stats["empty_files"]),
        "empty_after_chunking": int(stats["empty_after_chunking"]),
        "ingestor_skip_stats": dict(ingestor.skip_stats),
        "total_chunks_after_flush": total_after_flush,
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run()
