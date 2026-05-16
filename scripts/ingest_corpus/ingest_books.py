"""
ingest_books.py — Notes for Professionals PDFs → chunks.json

Uses opendataloader-pdf to convert PDFs to Markdown, then header-splits.
Only ingests P0/P1 books (Python, JS, TS, SQL, Git, Bash, Linux, React, Node,
PostgreSQL, Algorithms, TypeScript, CSS).

Usage:
    python scripts/ingest_corpus/ingest_books.py
    python scripts/ingest_corpus/ingest_books.py --dry-run        # list books + conversion stats
    python scripts/ingest_corpus/ingest_books.py --dry-run --histogram  # image-to-text ratio report
    python scripts/ingest_corpus/ingest_books.py --exclude-ratio 0.4    # custom exclusion threshold
"""

from __future__ import annotations

import argparse
import concurrent.futures
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path
from collections import defaultdict

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    log,
    make_doc_id,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
)
from scripts.ingest_corpus.semantic_chunker import HierarchicalSemanticChunker
from api.services.rag.utils import get_conversion_quality_report

# opendataloader_pdf is imported lazily inside pdf_to_markdown() so this
# module remains importable in environments without the PDF library.
# (Also required for ProcessPoolExecutor worker picklability.)

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
BOOKS_ROOT = DATASETS / "CS-and-Programming-Books"

# ─── P0/P1 books to ingest ────────────────────────────────────────────────────
# Format: (relative path from BOOKS_ROOT, tags)
P0_P1_BOOKS: list[tuple[str, list[str]]] = [
    # P0 - Languages & Databases
    ("Python/PythonNotesForProfessionals.pdf",         ["python", "stdlib", "P0"]),
    ("Javascript/JavaScriptNotesForProfessionals.pdf", ["javascript", "P0"]),
    ("Typescript/TypeScriptNotesForProfessionals.pdf", ["typescript", "P0"]),
    ("Sql/PostgreSQLNotesForProfessionals.pdf",        ["postgresql", "sql", "P0"]),
    ("Sql/SQLNotesForProfessionals.pdf",               ["sql", "P0"]),
    ("Interview Specific/SQL Tutorial.pdf",            ["sql", "P0"]),
    
    # P1 - Runtimes & Frameworks
    ("React/ReactJSNotesForProfessionals.pdf",         ["react", "frontend", "P1"]),
    ("Nodejs/NodeJSNotesForProfessionals.pdf",         ["nodejs", "javascript", "P1"]),
    ("html-css/CSSNotesForProfessionals.pdf",          ["css", "frontend", "P1"]),
    ("Javascript/Eloquent_JavaScript.pdf",             ["javascript", "P1"]),
    ("Java/JavaNotesForProfessionals.pdf",             ["java", "P1"]),
    
    # P1 - Core Tools & Infra
    ("Bash-shell-git/GitNotesForProfessionals.pdf",    ["git", "P1"]),
    ("Bash-shell-git/BashNotesForProfessionals.pdf",   ["bash", "shell", "P1"]),
    ("Bash-shell-git/LinuxNotesForProfessionals.pdf",  ["linux", "P1"]),
    ("dev-ops/Apache Maven 3 Cookbook.pdf",            ["maven", "devops", "P1"]),

    # P2 - Algorithms & System Design
    ("Algorithm/AlgorithmsNotesForProfessionals.pdf",  ["algorithms", "data-structures", "P2"]),
    ("Algorithm/Introduction to Algorithms.pdf",       ["algorithms", "theory", "P2"]),
    ("System Design 💻/System Design Handbook - Aman Barnwal.pdf", ["system-design", "architecture", "P2"]),
    ("Interview Specific/Cracking the coding interview 6th edition.pdf", ["interview", "coding", "P2"]),

    # P2 - Data & ML
    ("Machine-Learning/Python Data Science Handbook.pdf", ["python", "data-science", "P2"]),
    ("Machine-Learning/Data Engineering Cookbook.pdf",    ["data-engineering", "P2"]),
]


def pdf_to_markdown(pdf_path: Path, output_dir: Path) -> str | None:
    """Convert a single PDF to Markdown via opendataloader-pdf. Returns markdown text."""
    try:
        import opendataloader_pdf  # lazy — allows module import without Java/PDF deps
        opendataloader_pdf.convert(
            input_path=[str(pdf_path)],
            output_dir=str(output_dir),
            format="markdown",
        )
        # Output file is <stem>.md in output_dir
        md_file = output_dir / (pdf_path.stem + ".md")
        if md_file.exists():
            return md_file.read_text(encoding="utf-8")
        # Some versions use a subdirectory
        for candidate in output_dir.rglob("*.md"):
            return candidate.read_text(encoding="utf-8")
        log.warning(f"No markdown output found for {pdf_path.name}")
        return None
    except Exception as e:
        log.error(f"opendataloader-pdf failed for {pdf_path.name}: {e}")
        return None


# ─── Conversion quality helpers (Moved to api.services.rag.utils) ────────────────


def print_histogram(ratios: list[tuple[str, float]], threshold: float) -> None:
    """
    Print a distribution of image-to-text ratios across the corpus.
    Helps calibrate --exclude-ratio before committing to exclusion.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    for name, ratio in ratios:
        if ratio < 0.10:
            buckets["0-10%"].append(name)
        elif ratio < 0.25:
            buckets["10-25%"].append(name)
        elif ratio < 0.50:
            buckets["25-50%"].append(name)
        elif ratio < 0.75:
            buckets["50-75%"].append(name)
        else:
            buckets["75%+"].append(name)

    print("\n" + "=" * 60)
    print(f"  IMAGE-TO-TEXT RATIO DISTRIBUTION  (threshold={threshold:.0%})")
    print("=" * 60)
    for label in ["0-10%", "10-25%", "25-50%", "50-75%", "75%+"]:
        files = buckets.get(label, [])
        marker = " <-- candidate for exclusion" if label in ["50-75%", "75%+"] else ""
        print(f"  {label:8}:  {len(files):3} files{marker}")
        if files and label in ["50-75%", "75%+"]:
            for f in files:
                print(f"             {f}")
    print("=" * 60 + "\n")


# v2: chunk_markdown_book replaced by HierarchicalSemanticChunker (see run())
_CHUNKER = HierarchicalSemanticChunker(max_tokens=512, version="v2", source_type="pdf")


# ─── Phase 4.1: Top-level worker (must be picklable for ProcessPoolExecutor) ───
def _convert_pdf_worker(args: tuple) -> dict:
    """
    Convert a single PDF to Markdown inside a worker process.

    Must be a module-level function to be picklable by ProcessPoolExecutor.
    Each worker uses its own sub-directory of the shared temp dir to avoid
    file-system races between concurrent jobs.

    Args:
        args: (pdf_path_str, book_name, tmp_dir_str, tags)

    Returns:
        dict with keys: book_name, tags, md_text (str|None), report (dict|None), error (str|None)
    """
    pdf_path_str, book_name, tmp_dir_str, tags, rel_path = args
    pdf_path = Path(pdf_path_str)
    # Per-worker subdirectory to prevent concurrent .md filename collisions
    worker_tmp = Path(tmp_dir_str) / f"w{os.getpid()}_{book_name[:24]}"
    worker_tmp.mkdir(parents=True, exist_ok=True)

    try:
        md_text = pdf_to_markdown(pdf_path, worker_tmp)
    except Exception as exc:
        return {"book_name": book_name, "tags": tags, "rel_path": rel_path,
                "md_text": None, "report": None, "error": str(exc)}

    if not md_text:
        return {"book_name": book_name, "tags": tags, "rel_path": rel_path,
                "md_text": None, "report": None, "error": "empty output"}

    report = get_conversion_quality_report(md_text)
    report["book"] = book_name  # Maintain compatibility with local reporting format
    report["image_ratio"] = report["image_density"]
    # Re-calculate status using local logic for now
    report["status"] = (
        "healthy" if report["image_ratio"] < 0.25 else "degraded" if report["image_ratio"] < 0.5 else "failed"
    )
    # Estimate heading count (not in utils yet)
    report["heading_count"] = len([line for line in md_text.splitlines() if line.strip().startswith("#")])
    report["code_block_count"] = md_text.count("```") // 2
    return {"book_name": book_name, "tags": tags, "rel_path": rel_path,
            "md_text": md_text, "report": report, "error": None}


def _resolve_source_url(book_name: str, rel_path: str) -> str:
    """Resolve the canonical source URL for a given book."""
    if "NotesForProfessionals" in book_name:
        slug = book_name.replace("NotesForProfessionals", "").lower()
        return f"https://goalkicker.com/books#{slug}"
    return f"https://github.com/sanjeevafk/CS-and-Programming-Books/blob/main/{rel_path}"


def run(
    dry_run: bool = False,
    histogram: bool = False,
    exclude_ratio: float = 0.5,
    workers: int = 4,
    near_dup_threshold: int | None = 4,
) -> None:
    """
    Ingest all P0/P1 books from CS-and-Programming-Books.

    Args:
        dry_run:            List books / conversion stats without writing chunks.
        histogram:          Show image-to-text ratio distribution and exit.
        exclude_ratio:      Drop books with image ratio >= this value (0.0-1.0).
        workers:            Number of parallel PDF conversion processes (default 4).
        near_dup_threshold: Hamming distance for SimHash dedup (None = disabled).
    """
    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]
    ingestor = BaseIngestor(
        "CS-Books-Notes-For-Professionals",
        source_type="pdf",
        validators=validators,
        near_dup_threshold=near_dup_threshold,
    )
    img_ratios: list[tuple[str, float]] = []

    # ── Build work list ─────────────────────────────────────────────────────
    work_items: list[tuple[str, str, list[str], str]] = []
    for rel_path, tags in P0_P1_BOOKS:
        pdf_path = BOOKS_ROOT / rel_path
        if not pdf_path.exists():
            log.warning(f"MISSING (skipping): {rel_path}")
            continue
        if dry_run and not histogram:
            log.info(f"  [dry-run] would convert {pdf_path}")
            continue
        work_items.append((str(pdf_path), pdf_path.stem, tags, rel_path))

    if not work_items:
        if not dry_run:
            log.warning("No PDFs found to process. Check BOOKS_ROOT.")
        return

    actual_workers = min(workers, len(work_items), multiprocessing.cpu_count())
    log.info(
        f"Starting parallel PDF conversion: {len(work_items)} books, "
        f"{actual_workers} workers"
    )

    # ── Parallel PDF → Markdown conversion ─────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="depthapi_books_") as tmpdir:
        tmp_path = Path(tmpdir)
        start_t = time.monotonic()

        # mp_context="fork" (Linux default) is fastest; workers inherit the
        # parent's Python state including opendataloader_pdf.
        # Use "spawn" if opendataloader-pdf's JVM behaves unexpectedly.
        mp_ctx = multiprocessing.get_context("fork")
        worker_args = [
            (pdf_str, book_name, str(tmp_path), tags, rel_path)
            for pdf_str, book_name, tags, rel_path in work_items
        ]

        conversion_results: list[dict] = []
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=actual_workers,
            mp_context=mp_ctx,
        ) as executor:
            future_to_book = {
                executor.submit(_convert_pdf_worker, arg): arg[1]
                for arg in worker_args
            }
            done = 0
            for future in concurrent.futures.as_completed(future_to_book):
                done += 1
                book_name = future_to_book[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.error(f"  Worker crash for {book_name!r}: {exc}")
                    continue

                if result["error"]:
                    log.warning(f"  FAILED {book_name}: {result['error']}")
                    continue

                report = result["report"]
                img_ratio = report["image_ratio"]
                img_ratios.append((book_name, img_ratio))
                log.info(
                    f"  [{done}/{len(work_items)}] {book_name}: "
                    f"status={report['status']} "
                    f"headings={report['heading_count']} "
                    f"code_blocks={report['code_block_count']} "
                    f"img_ratio={img_ratio:.1%}"
                )

                if img_ratio >= exclude_ratio:
                    log.warning(
                        f"  EXCLUDED (img_ratio={img_ratio:.1%} >= {exclude_ratio:.0%}): {book_name}"
                    )
                    continue

                if not dry_run:
                    conversion_results.append(result)
                else:
                    log.info(
                        f"  [dry-run] would ingest {book_name} "
                        f"({len(result['md_text']):,} chars)"
                    )

        elapsed = time.monotonic() - start_t
        log.info(
            f"Conversion complete in {elapsed:.1f}s "
            f"({len(conversion_results)} books passed quality gate)"
        )

        # ── Sequential chunking + ingestion (ingestor is not process-safe) ───
        for result in conversion_results:
            book_name = result["book_name"]
            tags      = result["tags"]
            md_text   = result["md_text"]
            rel_path  = result["rel_path"]

            source_url = _resolve_source_url(book_name, rel_path)
            doc_id = make_doc_id(book_name, source_url)

            chunks = _CHUNKER.chunk_document(
                text        = md_text,
                doc_id      = doc_id,
                source_name = book_name,
                source_url  = source_url,
                tags        = tags + ["notes-for-professionals", "goalkicker"],
                breadcrumbs = [book_name],
            )
            log.info(f"  {book_name}: {len(chunks)} chunks")
            added = ingestor.add_chunks(chunks)
            log.info(
                f"    accepted={len(added)} "
                f"near_dup={ingestor.skip_stats['near_duplicate']}"
            )

    if histogram and img_ratios:
        print_histogram(img_ratios, threshold=exclude_ratio)

    if not dry_run:
        total = ingestor.flush()
        log.info(f"Books ingest complete. chunks.json total: {total}")


if __name__ == "__main__":
    # ProcessPoolExecutor fork safety: guard entry point
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(
        description="Ingest Notes for Professionals PDFs (parallel, near-dup aware)"
    )
    parser.add_argument("--dry-run",      action="store_true",
                        help="List books / show stats without writing chunks")
    parser.add_argument("--histogram",    action="store_true",
                        help="Convert PDFs and show image-to-text ratio histogram")
    parser.add_argument("--exclude-ratio", type=float, default=0.5,
                        help="Exclude books with image ratio >= threshold (default 0.5)")
    parser.add_argument("--workers",       type=int,   default=4,
                        help="Number of parallel PDF conversion workers (default 4)")
    parser.add_argument("--no-neardup",   action="store_true",
                        help="Disable SimHash near-duplicate detection")
    args = parser.parse_args()
    run(
        dry_run            = args.dry_run,
        histogram          = args.histogram,
        exclude_ratio      = args.exclude_ratio,
        workers            = args.workers,
        near_dup_threshold = None if args.no_neardup else 4,
    )
