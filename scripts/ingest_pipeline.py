#!/usr/bin/env python
"""
ingest_pipeline.py — CLI entrypoint for the declarative ingestion pipeline.

Usage:
    # Full re-ingest
    .venv/bin/python scripts/ingest_pipeline.py \\
        --config datasets/system_design_primer/config.yaml \\
        --mode full

    # Incremental (default) — only changed files
    .venv/bin/python scripts/ingest_pipeline.py \\
        --config datasets/system_design_primer/config.yaml

    # Resume from DLQ
    .venv/bin/python scripts/ingest_pipeline.py \\
        --config datasets/system_design_primer/config.yaml \\
        --mode resume

    # Async concurrent mode with custom concurrency
    .venv/bin/python scripts/ingest_pipeline.py \\
        --config datasets/system_design_primer/config.yaml \\
        --async-mode \\
        --max-concurrent 10

    # Inspect DLQ after a run
    jq '.classification' data/dlq/*.jsonl | sort | uniq -c

    # Count chunks written
    jq 'select(.event == "metric" and .name == "chunks_written") | .value' \\
        data/logs/ingest_*.jsonl | tail -1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DepthAPI declarative ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to YAML dataset config (e.g. datasets/system_design_primer/config.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental", "resume"],
        default="incremental",
        help="Ingestion mode (default: incremental)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to JSONL log output file (default: stdout only)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--async-mode",
        action="store_true",
        help="Use async concurrent orchestrator (default: sync)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=10,
        help="Max concurrent documents in async mode (default: 10)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Correlation ID for log tracing (auto-generated if omitted)",
    )
    return parser.parse_args()


def _run_sync(args: argparse.Namespace) -> int:
    from api.services.rag.pipeline.orchestrator import IngestionMode, PipelineOrchestrator

    orchestrator = PipelineOrchestrator()
    mode = IngestionMode(args.mode)
    result = orchestrator.ingest(config_path=args.config, mode=mode)
    _print_result(result)
    return 0 if result.error_rate <= 0.01 else 1


def _run_async(args: argparse.Namespace) -> int:
    from api.services.rag.pipeline.concurrent_orchestrator import ConcurrentPipelineOrchestrator
    from api.services.rag.pipeline.orchestrator import IngestionMode

    orchestrator = ConcurrentPipelineOrchestrator(
        max_concurrent_docs=args.max_concurrent,
    )
    mode = IngestionMode(args.mode)
    result = asyncio.run(
        orchestrator.ingest_async(
            config_path=args.config,
            mode=mode,
            run_id=args.run_id,
        )
    )
    _print_result(result)
    return 0 if result.error_rate <= 0.01 else 1


def _print_result(result) -> None:
    """Print a human-readable summary of the ingestion result."""
    print("\n" + "─" * 60)
    print(f"  Dataset      : {result.dataset_name}")
    print(f"  Mode         : {result.mode}")
    print(f"  Docs OK      : {result.documents_processed}")
    print(f"  Docs Failed  : {result.documents_failed}")
    print(f"  Chunks Out   : {result.chunks_written}")
    print(f"  Chunks Dup   : {result.chunks_skipped_duplicate}")
    print(f"  Chunks Short : {result.chunks_skipped_too_short}")
    print(f"  Error Rate   : {result.error_rate:.2%}")
    if result.dlq_path:
        print(f"  DLQ          : {result.dlq_path}")
    print("─" * 60 + "\n")


def main() -> None:
    args = _parse_args()

    # Configure logging first
    from api.services.rag.pipeline.logging_config import configure_logging
    configure_logging(
        log_level=args.log_level,
        log_file=args.log_file,
        json_output=True,
    )

    if not args.config.exists():
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(2)

    exit_code = _run_async(args) if args.async_mode else _run_sync(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
