"""
ingest_books.py — Notes for Professionals PDFs → chunks.json

Uses opendataloader-pdf to convert PDFs to Markdown, then header-splits.
Only ingests P0/P1 books (Python, JS, TS, SQL, Git, Bash, Linux, React, Node,
PostgreSQL, Algorithms, TypeScript, CSS).

Usage:
    python scripts/ingest_corpus/ingest_books.py
    python scripts/ingest_corpus/ingest_books.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    log,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
    split_by_header_semantic,
    split_text_semantic,
)

import opendataloader_pdf

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


def chunk_markdown_book(md_text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    """
    Split a book's markdown output into retrieval chunks.
    Strategy: split at ## (chapter) and ### (section) headers.
    """
    # First pass: split at chapter level
    chapters = split_by_header_semantic(
        md_text,
        header_prefix="##",
        chunk_size=chunk_size * 3,
        overlap_words=0,
    )
    chunks: list[str] = []
    for chapter in chapters:
        if len(chapter) <= chunk_size:
            chunks.append(chapter)
        else:
            # Second pass: split chapter at section level
            sections = split_by_header_semantic(
                chapter,
                header_prefix="###",
                chunk_size=chunk_size,
                overlap_words=25,
            )
            if sections:
                chunks.extend(sections)
            else:
                chunks.extend(split_text_semantic(chapter, chunk_size=chunk_size, overlap_words=25))
    return [c for c in chunks if len(c.strip()) >= 80]


def run(dry_run: bool = False) -> None:
    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]
    ingestor = BaseIngestor(
        "CS-Books-Notes-For-Professionals",
        source_type="pdf",
        validators=validators,
    )
    total_order = 0

    with tempfile.TemporaryDirectory(prefix="depthapi_books_") as tmpdir:
        tmp_path = Path(tmpdir)

        for rel_path, tags in P0_P1_BOOKS:
            pdf_path = BOOKS_ROOT / rel_path
            if not pdf_path.exists():
                log.warning(f"MISSING (skipping): {rel_path}")
                continue

            book_name = pdf_path.stem
            log.info(f"Converting: {book_name}")

            if dry_run:
                log.info(f"  [dry-run] would convert {pdf_path}")
                continue

            md_text = pdf_to_markdown(pdf_path, tmp_path)
            if not md_text:
                continue

            log.info(f"  Markdown: {len(md_text):,} chars")
            chunks = chunk_markdown_book(md_text)
            log.info(f"  Chunks: {len(chunks)}")

            # Source URL from goalkicker.com naming convention
            slug = book_name.replace("NotesForProfessionals", "").lower()
            source_url = f"https://goalkicker.com/books#{slug}"

            added = ingestor.add(
                chunks,
                source_url=source_url,
                tags=tags + ["notes-for-professionals", "goalkicker"],
                start_order=total_order,
            )
            total_order += len(chunks)
            log.info(f"  Added: {len(added)} new chunks")

    if not dry_run:
        total = ingestor.flush()
        log.info(f"Books ingest complete. chunks.json total: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Notes for Professionals PDFs")
    parser.add_argument("--dry-run", action="store_true", help="List books without converting")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
