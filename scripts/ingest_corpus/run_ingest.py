"""
run_ingest.py — Master orchestrator for corpus ingestion.

Runs all ingest phases in order and prints a final summary.

Usage:
    python scripts/ingest_corpus/run_ingest.py                    # full pipeline
    python scripts/ingest_corpus/run_ingest.py --phase ciu        # just CIU
    python scripts/ingest_corpus/run_ingest.py --phase ciu books python_docs
    python scripts/ingest_corpus/run_ingest.py --phase docs --source fastapi pydantic
    python scripts/ingest_corpus/run_ingest.py --phase docs --source all --max-pages 50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ingest_corpus.base_ingestor import CHUNKS_FILE, log

PHASES = ["ciu", "books", "python_docs", "docs"]


def phase_ciu() -> None:
    from scripts.ingest_corpus.ingest_ciu import run
    run()


def phase_books(dry_run: bool = False) -> None:
    from scripts.ingest_corpus.ingest_books import run
    run(dry_run=dry_run)


def phase_python_docs(limit: int | None = None) -> None:
    from scripts.ingest_corpus.ingest_python_docs import run
    run(limit=limit)


def phase_docs(sources: list[str] | None = None, max_pages: int | None = None) -> None:
    from scripts.ingest_corpus.ingest_docs import run
    run(sources or ["fastapi", "pydantic", "sqlalchemy"], max_pages=max_pages)


def print_summary() -> None:
    if not CHUNKS_FILE.exists():
        log.warning("chunks.json not found — nothing ingested yet.")
        return
    with CHUNKS_FILE.open() as f:
        chunks = json.load(f)

    from collections import Counter
    by_source = Counter(c["source_name"] for c in chunks)
    by_type   = Counter(c["source_type"] for c in chunks)
    total_tokens = sum(c.get("token_count", 0) for c in chunks)

    print("\n" + "="*60)
    print(f"  CORPUS SUMMARY  —  {len(chunks):,} chunks")
    print("="*60)
    print(f"  Est. tokens: {total_tokens:,}  (~{total_tokens//1000}K)")
    print(f"  Types: {dict(by_type)}")
    print("\n  By source:")
    for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"    {src:<40} {count:>6} chunks")
    print("="*60 + "\n")


def run(
    phases: list[str],
    dry_run: bool = False,
    limit: int | None = None,
    sources: list[str] | None = None,
    max_pages: int | None = None,
) -> None:
    t0 = time.time()
    log.info(f"Running phases: {phases}")

    if "ciu" in phases:
        log.info("── Phase: CIU ──")
        phase_ciu()

    if "books" in phases:
        log.info("── Phase: Books (PDF) ──")
        phase_books(dry_run=dry_run)

    if "python_docs" in phases:
        log.info("── Phase: Python Docs (local HTML) ──")
        phase_python_docs(limit=limit)

    if "docs" in phases:
        log.info("── Phase: Live Doc Sites (Scrapling) ──")
        phase_docs(sources=sources, max_pages=max_pages)

    print_summary()
    log.info(f"Total wall time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DepthAPI corpus ingest orchestrator")
    parser.add_argument("--phase", nargs="+", default=PHASES,
                        help=f"Phase(s) to run: {PHASES}")
    parser.add_argument("--dry-run", action="store_true", help="Books: list without converting")
    parser.add_argument("--limit", type=int, default=None, help="Python docs: max HTML files")
    parser.add_argument("--source", nargs="+", default=None, help="Docs: sources to scrape")
    parser.add_argument("--max-pages", type=int, default=None, help="Docs: pages per source")
    args = parser.parse_args()
    run(
        phases=args.phase,
        dry_run=args.dry_run,
        limit=args.limit,
        sources=args.source,
        max_pages=args.max_pages,
    )
