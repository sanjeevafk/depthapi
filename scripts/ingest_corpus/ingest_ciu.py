"""
ingest_ciu.py — Coding Interview University → chunks.json

Source: datasets/coding-interview-university/README.md
Strategy: Header-aware split on ### boundaries (~150 high-signal chunks expected)
Tags: ["cs-fundamentals", "algorithms", "system-design", "P1"]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow running as a script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import BaseIngestor, log, split_by_header

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
CIU_ROOT = DATASETS / "coding-interview-university"

# Files to ingest (ordered by signal quality)
SOURCES = [
    (CIU_ROOT / "README.md",                        "Coding Interview University – Study Roadmap"),
    (CIU_ROOT / "programming-language-resources.md","CIU – Language-Specific Resources"),
]

TAGS = ["cs-fundamentals", "algorithms", "system-design", "big-o", "data-structures", "P1"]

# Sections to skip (navigation/meta noise)
SKIP_PATTERNS = [
    r"^#+\s*(Table of Contents|Contents|Updates|About|Translations|Why use this|Language-specific)",
    r"^\s*-\s*\[[ x]\]",   # raw checklist items (no prose context)
    r"^#+\s*Sponsors",
    r"^#+\s*Follow me",
]
_SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.IGNORECASE)


def should_skip_section(text: str) -> bool:
    first_line = text.split("\n")[0].strip()
    return bool(_SKIP_RE.match(first_line))


def extract_prose_from_ciu(md_text: str) -> list[str]:
    """
    Split on ### headers, keep sections that have actual prose/content.
    CIU uses ### for topics (e.g. '### Arrays'), which is the right granularity.
    """
    # Split at H2 and H3 boundaries
    sections = re.split(r"(?m)^(?=#{2,3} )", md_text)
    results: list[str] = []

    for section in sections:
        section = section.strip()
        if not section or len(section) < 80:
            continue
        if should_skip_section(section):
            continue

        # For large sections, further split at H3
        if len(section) > 800:
            sub = split_by_header(section, header_prefix="###", chunk_size=600, overlap=80)
            results.extend(sub)
        else:
            results.append(section)

    return [s for s in results if len(s.strip()) >= 80]


def run() -> None:
    ingestor = BaseIngestor("Coding Interview University", source_type="markdown")
    total_order = 0

    for file_path, display_name in SOURCES:
        if not file_path.exists():
            log.warning(f"Missing: {file_path}")
            continue

        log.info(f"Processing: {display_name}")
        raw = file_path.read_text(encoding="utf-8")
        chunks = extract_prose_from_ciu(raw)
        log.info(f"  → extracted {len(chunks)} candidate chunks")

        added = ingestor.add(
            chunks,
            source_url="https://github.com/jwasham/coding-interview-university",
            tags=TAGS,
            start_order=total_order,
        )
        total_order += len(chunks)
        log.info(f"  → {len(added)} new (after dedup)")

    total = ingestor.flush()
    log.info(f"CIU ingest complete. chunks.json total: {total}")


if __name__ == "__main__":
    run()
