"""
ingest_ciu.py — Coding Interview University → chunks.json

Source: datasets/coding-interview-university/README.md
Strategy: Header-aware split on ### boundaries (~200-280 chunks expected)
Tags: ["cs-fundamentals", "algorithms", "system-design", "P1"]

Fixes:
- Skip intro/preamble personal narrative sections
- Link ratio relaxed (0.6): CIU is a curated resource guide; link density is intentional
- Strip bare video-only checklist lines before validation; preserve prose + implementation specs
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

# Sections to skip (navigation/meta noise + personal narrative)
SKIP_PATTERNS = [
    r"^#+\s*(Table of Contents|Contents|Updates|About|Translations|Why use this|Language-specific)",
    r"^\s*-\s*\[[ x]\]",   # raw checklist items (no prose context)
    r"^#+\s*Sponsors",
    r"^#+\s*Follow me",
    # Personal narrative / intro preamble (not CS knowledge)
    r"^#\s*Coding Interview University\s*$",
    r"^#+\s*(What is it|Why use it|How to use it|Don't feel you|A Note About Video|Don't Make My Mistakes|What you won't see|The Daily Plan|Coding Question Practice|Coding Problems|Let's Get Started|Once You've Got|Find a Job|Update Your Resume|Interview Process|Be thinking of|Have questions|Getting the Job)\b",
    # Git/usage navigation subsections
    r"^#+\s*(If you don't want to use git|If you're comfortable with git|For this Study Plan|For your Coding Interview)\b",
    # Book/resource recommendation lists without technical prose
    r"^#+\s*(Additional Books|Interview Prep Books|Books for Data Structures|Video Series|Computer Science Courses|Papers)\b",
]
_SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.IGNORECASE)

# Patterns for bare video/link-only checklist lines to strip before validation
# These add no prose value — just curated link lists
_BARE_LINK_RE = re.compile(
    r"^\s*-\s*\[[ x]?\]?\s*"
    r"(?:\[\[Review\]\s+[^\]]+\]\([^)]+\)|\[[^\]]+\]\([^)]+\)(?:\s*\(video\))?)"
    r"\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def should_skip_section(text: str) -> bool:
    first_line = text.split("\n")[0].strip()
    return bool(_SKIP_RE.match(first_line))


def strip_bare_links(text: str) -> str:
    """Remove lines that are only a checklist link to a video/article.

    Preserves:
    - Prose paragraphs and notes
    - Implementation task items (e.g. '- [ ] size() - number of items')
    - Lines with explanatory text beyond just a URL
    """
    cleaned = _BARE_LINK_RE.sub("", text)
    # Also strip raw back-to-top navigation markers
    cleaned = re.sub(r"\*\*\[.*?back to top.*?\]\(.*?\)\*\*", "", cleaned)
    # Collapse excessive blank lines created by removal
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Chatty opener patterns to strip from section text (non-technical preamble sentences)
_CHATTY_OPENER_RE = re.compile(
    r"^[-*]?\s*(?:Nothing to implement here[^\n]+|Alright, enough talk[^\n]+|Don't forget[^\n]+)\n",
    re.IGNORECASE | re.MULTILINE,
)


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


def is_structural_fragment(text: str) -> bool:
    """Return True if the chunk is a structural/TOC-only fragment with no prose.

    Detects chunks where >75% of non-empty lines are bare list items
    (no sentence-ending punctuation, no explanatory words >3 chars beyond the item itself).
    These are outline fragments from the Table of Contents, not knowledge chunks.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return True
    # Count lines that are bare bullet items (no clause-level prose)
    bare = sum(
        1 for ln in lines
        if re.match(r"^[-*]\s", ln)
        and not re.search(r"[.!?:]\s", ln)       # no sentence punctuation
        and len(ln.split()) <= 8                  # short item labels only
    )
    return len(lines) >= 3 and (bare / len(lines)) > 0.75


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
        if is_structural_fragment(section):
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
        make_min_word_validator(20),          # relaxed: CIU has short but dense spec items
        make_link_ratio_validator(0.6),        # relaxed: CIU is a curated resource guide
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
        # Strip bare link-only checklist lines before chunking to reduce noise
        # and avoid false positives from the link-ratio validator
        raw = strip_bare_links(raw)
        # Strip chatty non-technical opener sentences
        raw = _CHATTY_OPENER_RE.sub("", raw)
        chunks = extract_prose_from_ciu(raw)
        log.info(f"  → extracted {len(chunks)} candidate chunks")

        added = ingestor.add(
            chunks,
            source_url="https://github.com/jwasham/coding-interview-university",
            tags=TAGS,
            start_order=total_order,
            metadata=[
                {"namespace": "cs_fundamentals_knowledgeset", "source_file": file_path.name}
                for _ in chunks
            ],
        )
        total_order += len(chunks)
        log.info(f"  → {len(added)} new (after dedup)")

    total = ingestor.flush()
    log.info(f"CIU ingest complete. chunks.json total: {total}")


if __name__ == "__main__":
    run()
