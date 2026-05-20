"""
ingest_ciu_pdfs.py — Ingest all CIU cheat sheet PDFs into cs_fundamentals_knowledgeset.

Uses opendataloader-pdf for text extraction, BaseIngestor for deduplication
and persistence to data/rag/trusted/chunks.json.

Usage:
  python3 scripts/ingest_corpus/ingest_ciu_pdfs.py
  python3 scripts/ingest_corpus/ingest_ciu_pdfs.py --dry-run   # extract + print, no write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    REPO_ROOT,
    log,
    make_min_word_validator,
    split_text_semantic,
)

import tempfile
import glob

try:
    from opendataloader_pdf import convert
except ImportError:
    convert = None  # Handled gracefully in run()


# ── Config ────────────────────────────────────────────────────────────────────

CHEAT_SHEET_DIR = REPO_ROOT / "datasets" / "coding-interview-university" / "extras" / "cheat sheets"

NAMESPACE = "cs_fundamentals_knowledgeset"

# All 10 PDFs with their per-file tags to improve retrieval precision
PDF_SOURCES: list[tuple[str, list[str]]] = [
    ("big-o-cheatsheet.pdf",                       ["big-o", "complexity", "algorithms", "cs-fundamentals"]),
    ("system-design.pdf",                          ["system-design", "architecture", "cs-fundamentals"]),
    ("Coding Interview Python Language Essentials.pdf", ["python", "language-reference", "cheat-sheet"]),
    ("python-cheat-sheet-v1.pdf",                  ["python", "language-reference", "cheat-sheet"]),
    ("git-cheat-sheet-education.pdf",              ["git", "version-control", "devops", "cheat-sheet"]),
    ("Java Fundamentals Cheatsheet.pdf",           ["java", "language-reference", "cheat-sheet"]),
    ("STL Quick Reference 1.29.pdf",               ["cpp", "stl", "language-reference", "data-structures", "cheat-sheet"]),
    ("Cpp_reference.pdf",                          ["cpp", "language-reference", "cheat-sheet"]),
    ("C Reference Card (ANSI) 2.2.pdf",            ["c", "language-reference", "cheat-sheet"]),
    ("bits-cheat-sheet.pdf",                       ["bitwise", "bit-manipulation", "cs-fundamentals", "cheat-sheet"]),
]

BASE_TAGS = ["P1", "coding-interview", "ciu"]

# Relaxed: PDF cheat sheets have dense tables, not flowing prose
VALIDATORS = [
    make_min_word_validator(8),  # very relaxed — some table rows are 8–15 words
]


# ── PDF text cleaning ─────────────────────────────────────────────────────────

_PAGE_NUMBER_RE = re.compile(r"^\s*\d+\s*$", re.MULTILINE)
_GARBLED_RE = re.compile(r"[^\x09\x0a\x0d\x20-\x7e\u00a0-\ufffd]")
_EXCESS_NL_RE = re.compile(r"\n{4,}")


def clean_pdf_text(text: str) -> str:
    """Remove page numbers, control chars, and excessive blank lines from PDF extraction."""
    text = _PAGE_NUMBER_RE.sub("", text)
    text = _GARBLED_RE.sub("", text)
    text = _EXCESS_NL_RE.sub("\n\n\n", text)
    return text.strip()


def is_ocr_noise(text: str) -> bool:
    """Heuristic: if >40% of chars are non-ASCII in extracted text, likely garbled OCR."""
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (non_ascii / len(text)) > 0.40


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: Path) -> str | None:
    """
    Extract full text from a PDF using opendataloader-pdf.
    Returns None if extraction failed or text is OCR noise.
    """
    if convert is None:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            convert(
                input_path=str(pdf_path),
                output_dir=tmpdir,
                format="markdown",
                quiet=True
            )
        except Exception as e:
            log.warning(f"  [{pdf_path.name}] convert() failed: {e}")
            return None

        md_files = glob.glob(f"{tmpdir}/*.md")
        if not md_files:
            log.warning(f"  [{pdf_path.name}] opendataloader returned no markdown files")
            return None

        full_text = ""
        for md_file in md_files:
            with open(md_file, "r", encoding="utf-8", errors="replace") as f:
                full_text += f.read() + "\n\n"

    if not full_text.strip():
        log.warning(f"  [{pdf_path.name}] extracted text is empty after joining pages")
        return None

    cleaned = clean_pdf_text(full_text)

    if is_ocr_noise(cleaned):
        log.warning(f"  [{pdf_path.name}] extraction looks like OCR noise (>40% non-ASCII) — skipping")
        return None

    return cleaned


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict[str, Any]:
    if convert is None:
        log.error(
            "opendataloader-pdf is not installed. "
            "Run: pip install opendataloader-pdf"
        )
        sys.exit(1)

    source_name = "Coding Interview University - Cheat Sheets"
    ingestor = BaseIngestor(
        source_name,
        source_type="pdf",
        validators=VALIDATORS,
    )

    total_order = 0
    stats: dict[str, int] = {
        "pdfs_found": 0,
        "pdfs_skipped_missing": 0,
        "pdfs_skipped_ocr_noise": 0,
        "chunks_extracted": 0,
        "chunks_added": 0,
    }

    for filename, extra_tags in PDF_SOURCES:
        pdf_path = CHEAT_SHEET_DIR / filename
        tags = BASE_TAGS + extra_tags

        if not pdf_path.exists():
            log.warning(f"Missing PDF: {pdf_path}")
            stats["pdfs_skipped_missing"] += 1
            continue

        stats["pdfs_found"] += 1
        log.info(f"Processing: {filename}  ({pdf_path.stat().st_size // 1024}KB)")

        text = extract_text_from_pdf(pdf_path)
        if text is None:
            stats["pdfs_skipped_ocr_noise"] += 1
            continue

        chunks = split_text_semantic(text, chunk_size=800, overlap_words=25)
        log.info(f"  → extracted {len(chunks)} candidate chunks")

        if dry_run:
            # Print first chunk per PDF for spot-checking
            if chunks:
                preview = chunks[0][:300].replace("\n", " ")
                log.info(f"  [DRY RUN] first chunk preview: {preview!r}")
            continue

        added = ingestor.add(
            chunks,
            source_url="https://github.com/jwasham/coding-interview-university",
            tags=tags,
            start_order=total_order,
            metadata=[
                {
                    "namespace": NAMESPACE,
                    "source_file": filename,
                    "pdf_path": f"datasets/coding-interview-university/extras/cheat sheets/{filename}",
                }
                for _ in chunks
            ],
        )
        total_order += len(chunks)
        stats["chunks_extracted"] += len(chunks)
        stats["chunks_added"] += len(added)
        log.info(f"  → {len(added)} new chunks added")

    if not dry_run:
        total = ingestor.flush()
        log.info(
            f"\n[{source_name}] complete:\n"
            f"  PDFs found: {stats['pdfs_found']}\n"
            f"  PDFs skipped (missing): {stats['pdfs_skipped_missing']}\n"
            f"  PDFs skipped (OCR noise): {stats['pdfs_skipped_ocr_noise']}\n"
            f"  Chunks extracted: {stats['chunks_extracted']}\n"
            f"  Chunks added: {stats['chunks_added']}\n"
            f"  Total corpus size: {total}"
        )
    else:
        log.info("[DRY RUN] No changes written.")

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest CIU cheat sheet PDFs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and preview text only — do not write to chunks.json",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
