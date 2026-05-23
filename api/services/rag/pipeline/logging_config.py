"""
logging_config.py — Structured Logging Configuration.

Configures structlog for JSON-output structured logging throughout the pipeline.
All log events are JSON objects with correlation IDs for cross-event tracing.

Usage:
    from api.services.rag.pipeline.logging_config import configure_logging, get_logger

    configure_logging(log_file=Path("data/logs/ingest_20260523.jsonl"))
    log = get_logger(__name__)
    log.info("chunk_written", chunk_id="abc123", dataset="system-design-primer")

Log parsing (operational):
    jq 'select(.event == "chunk_written")' data/logs/ingest_20260523.jsonl | wc -l
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import structlog


# ─── Public API ───────────────────────────────────────────────────────────────


def configure_logging(
    log_level: str = "INFO",
    log_file: Optional[Path] = None,
    json_output: bool = True,
) -> None:
    """
    Configure structlog for JSON structured logging.

    Args:
        log_level: Minimum log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to a JSONL log file. Writes to stdout if None.
        json_output: Use JSON renderer (True) or console renderer (False).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Build handler(s)
    handlers: list[logging.Handler] = []

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    handlers.append(stdout_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(level)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(message)s",  # structlog formats the full string
    )

    renderer: structlog.types.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog logger bound to the given name."""
    return structlog.get_logger(name)
