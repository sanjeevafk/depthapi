"""
ingest_python_docs.py — Python 3.x HTML docs → chunks.json

Source: datasets/python-3.14-docs-html/ (already downloaded)
Parses local HTML files using stdlib html.parser (no network, no browser).
Filters to high-signal pages only (library ref, tutorial, howto).

Usage:
    python scripts/ingest_corpus/ingest_python_docs.py
    python scripts/ingest_corpus/ingest_python_docs.py --limit 100  # for testing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    log,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
    split_text_semantic,
)

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
DOCS_ROOT = DATASETS / "python-3.14-docs-html"

# Directories to include (high signal)
INCLUDE_DIRS = {
    "library",   # stdlib reference — P0 gold
    "tutorial",  # official tutorial — P0
    "howto",     # HOWTOs — P0
    "faq",       # FAQs — P1
    "c-api",     # C API — P2 (but ingest anyway)
}

# Pages to always skip
SKIP_FILES = {
    "404.html", "genindex-all.html", "download.html",
    "about.html", "bugs.html", "copyright.html", "contents.html",
}

TAGS = ["python", "stdlib", "official-docs", "P0"]


def html_to_chunks(html_path: Path, source_url: str) -> list[str]:
    """Parse a Python docs HTML file and extract clean text chunks."""
    return _parse_html(html_path)


def _parse_html(html_path: Path) -> list[str]:
    """Pure Python html.parser — fast, zero extra deps."""
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_main = False
            self.skip_depth = 0
            self.depth = 0
            self.texts: list[str] = []
            self._SKIP_TAGS = {"script", "style", "nav", "header", "footer"}

        def handle_starttag(self, tag, attrs):
            self.depth += 1
            attr_d = dict(attrs)
            if tag in self._SKIP_TAGS:
                self.skip_depth = self.depth
            role = attr_d.get("role", "")
            cls  = attr_d.get("class", "")
            if role == "main" or "body" in cls:
                self.in_main = True

        def handle_endtag(self, tag):
            if self.depth == self.skip_depth:
                self.skip_depth = 0
            self.depth -= 1

        def handle_data(self, data):
            if self.in_main and not self.skip_depth:
                stripped = data.strip()
                if stripped:
                    self.texts.append(stripped)

    raw = html_path.read_text(encoding="utf-8", errors="ignore")
    parser = _Extractor()
    parser.feed(raw)
    text = "\n".join(parser.texts)
    if len(text) < 100:
        return []
    return split_text_semantic(text, chunk_size=800, overlap_words=25)


def collect_html_files(limit: int | None = None) -> list[tuple[Path, str]]:
    """Return (path, source_url) pairs for high-signal pages."""
    files: list[tuple[Path, str]] = []

    for html_file in sorted(DOCS_ROOT.rglob("*.html")):
        if html_file.name in SKIP_FILES:
            continue
        if html_file.name.startswith("genindex"):
            continue

        # Check if this file is under a high-signal directory
        parts = html_file.relative_to(DOCS_ROOT).parts
        top_dir = parts[0] if len(parts) > 1 else None
        if top_dir and top_dir not in INCLUDE_DIRS:
            continue

        # Build canonical docs URL
        rel = html_file.relative_to(DOCS_ROOT)
        url = "https://docs.python.org/3/" + "/".join(rel.parts)
        files.append((html_file, url))

        if limit and len(files) >= limit:
            break

    return files


def run(limit: int | None = None) -> None:
    if not DOCS_ROOT.exists():
        log.error(f"Python docs not found at {DOCS_ROOT}")
        sys.exit(1)

    files = collect_html_files(limit=limit)
    log.info(f"Found {len(files)} HTML pages to ingest")

    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]
    ingestor = BaseIngestor(
        "Python 3 Official Docs",
        source_type="html",
        validators=validators,
    )
    total_order = 0

    for i, (html_path, source_url) in enumerate(files):
        chunks = html_to_chunks(html_path, source_url)
        if not chunks:
            continue

        ingestor.add(chunks, source_url=source_url, tags=TAGS, start_order=total_order)
        total_order += len(chunks)

        if i % 100 == 0:
            log.info(f"  Progress: {i}/{len(files)} files | {ingestor.new_count} chunks added")

    total = ingestor.flush()
    log.info(f"Python docs ingest complete. chunks.json total: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Python 3 HTML docs")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of files (for testing)")
    args = parser.parse_args()
    run(limit=args.limit)
