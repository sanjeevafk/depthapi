"""
cleanup_chunks.py - Clean noisy chunks.json entries.

Default behavior:
- Apply markdown noise filters (details blocks, TOC anchor lists)
- Drop chunks that fail basic quality validators
- Write to chunks.cleaned.json (or in-place with backup)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    CHUNKS_FILE,
    clean_text,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
)


def strip_markdown_noise(text: str) -> str:
    """Remove repo metadata blocks and TOC-only lists."""
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^\s*-\s*\[[^\]]+\]\(#.+?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*-\s*\[[^\]]+\]\(translations/.+?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_chunk_content(content: str, mode: str) -> str:
    cleaned = clean_text(content)
    if mode in {"ciu", "markdown"}:
        cleaned = strip_markdown_noise(cleaned)
    return cleaned


def run(input_path: Path, output_path: Path, mode: str, in_place: bool) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))

    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]

    kept = []
    dropped = 0
    modified = 0

    for item in data:
        original = item.get("content", "")
        cleaned = clean_chunk_content(original, mode)

        if cleaned != original:
            modified += 1

        if not cleaned:
            dropped += 1
            continue

        if any(not v(cleaned) for v in validators):
            dropped += 1
            continue

        item["content"] = cleaned
        kept.append(item)

    if in_place:
        backup_path = input_path.with_suffix(input_path.suffix + ".bak")
        backup_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        output_path = input_path

    output_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Cleaned {input_path.name}: total={len(data)} kept={len(kept)} "
        f"dropped={dropped} modified={modified} output={output_path}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean noisy chunk content in chunks.json")
    parser.add_argument("--input", type=Path, default=CHUNKS_FILE)
    parser.add_argument("--output", type=Path, default=CHUNKS_FILE.with_name("chunks.cleaned.json"))
    parser.add_argument("--mode", choices=["ciu", "markdown", "generic"], default="ciu")
    parser.add_argument("--in-place", action="store_true", help="Rewrite input file with .bak backup")
    args = parser.parse_args()

    run(args.input, args.output, args.mode, args.in_place)
