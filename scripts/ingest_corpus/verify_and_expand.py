"""
verify_and_expand.py — One-command corpus expansion + verification pipeline.

Flow:
1) Audit current corpus
2) Detect missing configured doc sources
3) Scrape missing sources via ingest_docs (Scrapling)
4) Re-audit corpus quality
5) Build/update vector+BM25 index
6) Write JSON report

Usage:
    python3 scripts/ingest_corpus/verify_and_expand.py
    python3 scripts/ingest_corpus/verify_and_expand.py --max-pages 150
    python3 scripts/ingest_corpus/verify_and_expand.py --sources fastapi pydantic sqlalchemy
    python3 scripts/ingest_corpus/verify_and_expand.py --include-local --priority P0
    python3 scripts/ingest_corpus/verify_and_expand.py --skip-index
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import CHUNKS_FILE, log
from scripts.ingest_corpus.ingest_docs import TARGETS, run as run_docs_ingest
from scripts.ingest_corpus.ingest_local_repos import DEFAULT_MANIFEST, run as run_local_repos_ingest


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = REPO_ROOT / "data" / "rag" / "reports"


def _load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_FILE.exists():
        return []
    with CHUNKS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _snapshot(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    by_source = Counter(c.get("source_name", "Unknown") for c in chunks)
    by_tag = Counter(t for c in chunks for t in c.get("tags", []))
    by_type = Counter(c.get("source_type", "unknown") for c in chunks)
    by_namespace = Counter(
        ((c.get("metadata") or {}).get("namespace", "trusted"))
        if isinstance(c.get("metadata"), dict) else "trusted"
        for c in chunks
    )
    return {
        "total_chunks": len(chunks),
        "by_source": dict(by_source),
        "by_type": dict(by_type),
        "by_namespace": dict(by_namespace),
        "top_tags": dict(by_tag.most_common(25)),
    }


def _missing_doc_targets(chunks: list[dict[str, Any]], configured: list[str]) -> list[str]:
    existing_sources = {c.get("source_name", "") for c in chunks}
    missing = []
    for key in configured:
        source_name = TARGETS[key]["source_name"]
        if source_name not in existing_sources:
            missing.append(key)
    return missing


def _run_audit_script(script_rel_path: str) -> tuple[bool, str]:
    script = REPO_ROOT / script_rel_path
    cmd = [sys.executable, str(script)]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    ok = proc.returncode == 0
    out = (proc.stdout or "") + (proc.stderr or "")
    return ok, out.strip()


def _run_index_builder() -> tuple[bool, str]:
    script = REPO_ROOT / "scripts" / "ingest_corpus" / "build_index.py"
    proc = subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, capture_output=True, text=True)
    ok = proc.returncode == 0
    out = (proc.stdout or "") + (proc.stderr or "")
    return ok, out.strip()


def _compute_source_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    all_sources = set(before["by_source"]) | set(after["by_source"])
    for src in sorted(all_sources):
        out[src] = int(after["by_source"].get(src, 0)) - int(before["by_source"].get(src, 0))
    return out


def _compute_namespace_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    all_ns = set(before["by_namespace"]) | set(after["by_namespace"])
    for ns in sorted(all_ns):
        out[ns] = int(after["by_namespace"].get(ns, 0)) - int(before["by_namespace"].get(ns, 0))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand missing sources and verify ingestion/index quality")
    parser.add_argument("--sources", nargs="+", default=None, help="Subset of ingest_docs TARGETS keys")
    parser.add_argument("--max-pages", type=int, default=None, help="Per-source crawl cap override")
    parser.add_argument("--include-local", action="store_true", help="Run local repos ingestion phase")
    parser.add_argument("--manifest", type=str, default=DEFAULT_MANIFEST, help="Local repos manifest path")
    parser.add_argument("--priority", type=str, default="all", choices=["P0", "P1", "P2", "all"])
    parser.add_argument("--max-files", type=int, default=None, help="Local repos max files per source")
    parser.add_argument("--skip-index", action="store_true", help="Skip build_index.py stage")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue even if one stage fails")
    args = parser.parse_args()

    configured_sources = args.sources or list(TARGETS.keys())
    unknown = [s for s in configured_sources if s not in TARGETS]
    if unknown:
        raise SystemExit(f"Unknown sources: {unknown}. Valid keys: {list(TARGETS.keys())}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)

    # Pre-audit snapshot
    before_chunks = _load_chunks()
    before = _snapshot(before_chunks)
    missing = _missing_doc_targets(before_chunks, configured_sources)
    log.info(f"Configured sources: {configured_sources}")
    log.info(f"Missing sources to ingest now: {missing}")

    # Scripted audits (human-readable)
    pre_audit_ok, pre_audit_output = _run_audit_script("scripts/ingest_corpus/audit_corpus.py")
    pre_detailed_ok, pre_detailed_output = _run_audit_script("scripts/ingest_corpus/detailed_audit.py")

    ingest_ok = True
    ingest_error = ""
    local_ingest_ok = True
    local_ingest_error = ""
    local_ingest_result: dict[str, Any] | None = None
    if missing:
        try:
            run_docs_ingest(missing, max_pages=args.max_pages)
        except BaseException as exc:  # catches SystemExit from ingest_docs dependency checks
            ingest_ok = False
            ingest_error = str(exc)
            if not args.continue_on_error:
                raise
    if args.include_local:
        try:
            local_ingest_result = run_local_repos_ingest(
                manifest_path=args.manifest,
                priority=args.priority,
                max_files=args.max_files,
            )
        except BaseException as exc:
            local_ingest_ok = False
            local_ingest_error = str(exc)
            if not args.continue_on_error:
                raise

    # Post-audit snapshot
    after_chunks = _load_chunks()
    after = _snapshot(after_chunks)
    source_deltas = _compute_source_deltas(before, after)
    namespace_deltas = _compute_namespace_deltas(before, after)

    post_audit_ok, post_audit_output = _run_audit_script("scripts/ingest_corpus/audit_corpus.py")
    post_detailed_ok, post_detailed_output = _run_audit_script("scripts/ingest_corpus/detailed_audit.py")

    index_ok = None
    index_output = ""
    if not args.skip_index:
        index_ok, index_output = _run_index_builder()
        if not index_ok and not args.continue_on_error:
            raise SystemExit("Index build failed. Re-run with --continue-on-error to keep report generation.")

    ended_at = datetime.now(timezone.utc)
    report = {
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "configured_sources": configured_sources,
        "missing_sources_at_start": missing,
        "local_ingest": {
            "enabled": bool(args.include_local),
            "manifest": args.manifest,
            "priority": args.priority,
            "max_files": args.max_files,
        },
        "before": before,
        "after": after,
        "source_chunk_deltas": source_deltas,
        "namespace_chunk_deltas": namespace_deltas,
        "stages": {
            "pre_audit": {"ok": pre_audit_ok},
            "pre_detailed_audit": {"ok": pre_detailed_ok},
            "ingest_missing_docs": {"ok": ingest_ok, "error": ingest_error},
            "ingest_local_repos": {"ok": local_ingest_ok, "error": local_ingest_error},
            "post_audit": {"ok": post_audit_ok},
            "post_detailed_audit": {"ok": post_detailed_ok},
            "index_build": {"ok": index_ok, "skipped": bool(args.skip_index)},
        },
        "ingest_stats": {
            "local_repos": local_ingest_result,
            "skip_reasons": (local_ingest_result or {}).get("skip_reasons", {}),
        },
        "logs": {
            "pre_audit": pre_audit_output,
            "pre_detailed_audit": pre_detailed_output,
            "post_audit": post_audit_output,
            "post_detailed_audit": post_detailed_output,
            "index_build": index_output,
        },
    }

    ts = ended_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORT_DIR / f"verify_expand_report_{ts}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n=== Verify & Expand Report ===")
    print(f"Report: {report_path}")
    print(f"Total chunks: {before['total_chunks']} -> {after['total_chunks']}")
    print(f"Missing sources at start: {missing}")
    print("Source deltas:")
    for src, delta in source_deltas.items():
        if delta != 0:
            print(f"  {src}: {delta:+d}")
    if not any(v != 0 for v in source_deltas.values()):
        print("  (no net chunk changes)")
    print("Namespace deltas:")
    for ns, delta in namespace_deltas.items():
        if delta != 0:
            print(f"  {ns}: {delta:+d}")
    if not any(v != 0 for v in namespace_deltas.values()):
        print("  (no net namespace changes)")
    if index_ok is not None:
        print(f"Index build: {'OK' if index_ok else 'FAILED'}")


if __name__ == "__main__":
    main()
