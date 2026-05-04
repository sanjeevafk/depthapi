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

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    log,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
    split_by_header_semantic,
)

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


def strip_markdown_noise(text: str) -> str:
    """Remove CIU repo metadata blocks and TOC-only lists."""
    # Drop <details> blocks (translations, misc repo metadata)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Drop anchor-only list items (TOC/navigation)
    text = re.sub(r"^\s*-\s*\[[^\]]+\]\(#.+?\)\s*$", "", text, flags=re.MULTILINE)
    # Drop bare translation lists like '- [Bahasa](translations/README-id.md)'
    text = re.sub(r"^\s*-\s*\[[^\]]+\]\(translations/.+?\)\s*$", "", text, flags=re.MULTILINE)
    # Collapse extra blank lines after removals
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_prose_from_ciu(md_text: str) -> list[str]:
    """
    Split on ### headers, keep sections that have actual prose/content.
    CIU uses ### for topics (e.g. '### Arrays'), which is the right granularity.
    """
    # Split at H2 and H3 boundaries
    md_text = strip_markdown_noise(md_text)
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
            sub = split_by_header_semantic(
                section,
                header_prefix="###",
                chunk_size=800,
                overlap_words=25,
            )
            results.extend(sub)
        else:
            results.append(section)

    return [s for s in results if len(s.strip()) >= 80]


def run() -> None:
    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]
    ingestor = BaseIngestor(
        "Coding Interview University",
        source_type="markdown",
        validators=validators,
    )
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
