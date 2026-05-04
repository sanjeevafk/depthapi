"""
ingest_local_repos.py — Ingest English docs/code examples from local dataset repos.

Usage:
  python3 scripts/ingest_corpus/ingest_local_repos.py
  python3 scripts/ingest_corpus/ingest_local_repos.py --manifest scripts/ingest_corpus/local_ingest_manifest.json
  python3 scripts/ingest_corpus/ingest_local_repos.py --priority P0
  python3 scripts/ingest_corpus/ingest_local_repos.py --max-files 200
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    REPO_ROOT,
    log,
    make_link_ratio_validator,
    make_markdown_toc_validator,
    make_min_word_validator,
    split_text_semantic,
)

DEFAULT_MANIFEST = "scripts/ingest_corpus/local_ingest_manifest.json"
DEFAULT_ALLOWED_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".html"}
LOCALE_RE = re.compile(
    r"/(de|es|fr|ja|ko|pt|ru|tr|uk|zh|zh-hans|zh-hant|ar|az|fa|he|id|pl|ro|sq|sv|ta|te|ur|vi)(/|$)",
    re.IGNORECASE,
)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("YAML manifest requested but pyyaml is not installed.") from exc
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_any(value: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(value, pat):
            return True
        # fnmatch("file.md", "**/*.md") can fail; normalize this common pattern.
        if pat.startswith("**/") and fnmatch.fnmatch(value, pat[3:]):
            return True
    return False


def _is_english_path(rel_posix: str, english_allowlist: list[str]) -> bool:
    if _matches_any(rel_posix, english_allowlist):
        return True
    return not bool(LOCALE_RE.search("/" + rel_posix))


def _is_eligible_file(rel_posix: str, source: dict[str, Any]) -> tuple[bool, str]:
    allowed_extensions = set(source.get("allowed_extensions", [])) or DEFAULT_ALLOWED_EXTENSIONS
    ext = Path(rel_posix).suffix.lower()
    if ext not in allowed_extensions:
        return False, "binary_or_static"

    include_globs = source.get("include_globs", ["**/*"])
    exclude_globs = source.get("exclude_globs", [])
    if not _matches_any(rel_posix, include_globs):
        return False, "not_included"
    if _matches_any(rel_posix, exclude_globs):
        return False, "excluded_pattern"

    noisy_patterns = source.get("exclude_noisy", [])
    if noisy_patterns and _matches_any(rel_posix, noisy_patterns):
        return False, "noisy_page"
    return True, "ok"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _iter_candidate_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def _ingest_source(source: dict[str, Any], max_files: int | None = None) -> dict[str, Any]:
    root = REPO_ROOT / source["root"]
    if not root.exists():
        return {"source_name": source["name"], "error": f"missing root: {root}", "processed_files": 0}

    validators = [
        make_min_word_validator(30),
        make_link_ratio_validator(0.2),
        make_markdown_toc_validator(),
    ]
    ingestor = BaseIngestor(source["name"], source_type=source.get("source_type", "markdown"), validators=validators)

    english_allowlist = source.get("english_allowlist", [])
    namespace = source["namespace"]
    priority = source.get("priority", "P2")
    base_tags = list(source.get("tags", []))
    tags = base_tags + [priority]

    stats = Counter()
    chunk_order = 0
    per_source_cap = source.get("max_files")
    cap = max_files if max_files is not None else per_source_cap

    for file_path in _iter_candidate_files(root):
        rel_posix = file_path.relative_to(root).as_posix()
        if cap is not None and stats["processed_files"] >= int(cap):
            break

        ok, reason = _is_eligible_file(rel_posix, source)
        if not ok:
            stats[f"skip_{reason}"] += 1
            continue

        if not _is_english_path(rel_posix, english_allowlist):
            stats["skip_non_english"] += 1
            continue

        raw = _read_text(file_path)
        if not raw.strip():
            stats["skip_empty_file"] += 1
            continue

        chunks = split_text_semantic(raw, chunk_size=900, overlap_words=25)
        if not chunks:
            stats["skip_empty_after_chunking"] += 1
            continue

        metadata = [
            {
                "dataset_root": source["root"],
                "relative_path": rel_posix,
                "namespace": namespace,
                "language": "en",
            }
            for _ in chunks
        ]
        source_url = f"file://{source['root']}/{rel_posix}"
        added = ingestor.add(
            chunks,
            source_url=source_url,
            tags=tags,
            start_order=chunk_order,
            metadata=metadata,
        )
        chunk_order += len(chunks)
        stats["processed_files"] += 1
        stats["chunks_candidate"] += len(chunks)
        stats["chunks_added"] += len(added)
        stats["chunks_rejected"] += len(chunks) - len(added)

    total = ingestor.flush()
    result = {
        "source_name": source["name"],
        "namespace": namespace,
        "priority": priority,
        "root": source["root"],
        "processed_files": int(stats["processed_files"]),
        "chunks_candidate": int(stats["chunks_candidate"]),
        "chunks_added": int(stats["chunks_added"]),
        "chunks_rejected": int(stats["chunks_rejected"]),
        "skip_reasons": {k.replace("skip_", ""): int(v) for k, v in stats.items() if k.startswith("skip_")},
        "ingestor_skip_stats": dict(ingestor.skip_stats),
        "total_chunks_after_flush": total,
    }
    log.info(
        f"[{source['name']}] files={result['processed_files']} added={result['chunks_added']} "
        f"rejected={result['chunks_rejected']}"
    )
    return result


def run(
    manifest_path: str = DEFAULT_MANIFEST,
    priority: str = "all",
    max_files: int | None = None,
) -> dict[str, Any]:
    path = REPO_ROOT / manifest_path
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    manifest = _load_manifest(path)
    sources: list[dict[str, Any]] = manifest.get("sources", [])
    if priority != "all":
        sources = [s for s in sources if s.get("priority") == priority]

    results = []
    for source in sources:
        results.append(_ingest_source(source, max_files=max_files))

    by_namespace = Counter(r.get("namespace", "unknown") for r in results if not r.get("error"))
    skip_reasons = Counter()
    for r in results:
        for k, v in r.get("skip_reasons", {}).items():
            skip_reasons[k] += int(v)

    return {
        "manifest": manifest_path,
        "priority": priority,
        "sources_run": len(results),
        "namespace_counts": dict(by_namespace),
        "skip_reasons": dict(skip_reasons),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest local repo docs/code examples with English-only filters")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--priority", default="all", choices=["P0", "P1", "P2", "all"])
    parser.add_argument("--max-files", type=int, default=None)
    args = parser.parse_args()
    output = run(manifest_path=args.manifest, priority=args.priority, max_files=args.max_files)
    print(json.dumps(output, indent=2))
