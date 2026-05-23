"""
error_handler.py — Error classification, DLQ, and retry logic.

Classifies ingestion errors by severity and action, writes failures
to a local JSONL Dead Letter Queue for inspection and replay.

DLQ format: data/dlq/<YYYYMMDD>_<dataset>_errors.jsonl
Each line is a JSON-serialized ErrorRecord.

Usage:
    handler = ErrorHandler(config, dlq_dir, dataset_name)
    handler.record(error_record)
    handler.flush()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from api.services.rag.pipeline.config_schema import DatasetConfig, ErrorPolicyConfig
from api.services.rag.pipeline.models import ErrorRecord

log = logging.getLogger(__name__)


# ─── Default error policies (if not specified in config) ─────────────────────

_DEFAULT_POLICIES: dict[str, dict] = {
    "token_count_too_low": {
        "severity": "WARN",
        "action": "skip_chunk",
        "dlq": True,
        "retry": False,
        "max_retries": 0,
    },
    "token_count_too_high": {
        "severity": "WARN",
        "action": "skip_chunk",
        "dlq": True,
        "retry": False,
        "max_retries": 0,
    },
    "extraction_failed": {
        "severity": "ERROR",
        "action": "skip_document",
        "dlq": True,
        "retry": True,
        "max_retries": 3,
    },
    "duplicate_content_hash": {
        "severity": "INFO",
        "action": "skip_chunk",
        "dlq": False,
        "retry": False,
        "max_retries": 0,
    },
    "pii_detected": {
        "severity": "ERROR",
        "action": "redact_and_continue",
        "dlq": True,
        "retry": False,
        "max_retries": 0,
    },
    "validation_failed": {
        "severity": "ERROR",
        "action": "skip_document",
        "dlq": True,
        "retry": False,
        "max_retries": 0,
    },
    "sink_connection_error": {
        "severity": "ERROR",
        "action": "retry",
        "dlq": True,
        "retry": True,
        "max_retries": 5,
    },
    "processing_failed": {
        "severity": "ERROR",
        "action": "skip_document",
        "dlq": True,
        "retry": True,
        "max_retries": 3,
    },
}


class ErrorHandler:
    """
    Classifies errors and writes failures to a local DLQ (JSONL file).

    DLQ entries are immutable ErrorRecord objects written one-per-line.
    The DLQ can be inspected with `less data/dlq/errors.jsonl`
    and replayed with the orchestrator in RESUME mode.
    """

    def __init__(
        self,
        config: DatasetConfig,
        dlq_dir: Path,
        dataset_name: str,
    ) -> None:
        self._config = config
        self._dlq_dir = dlq_dir
        self._dataset_name = dataset_name
        self._buffer: list[ErrorRecord] = []

        # Build effective policy map: config overrides defaults
        self._policies: dict[str, dict] = dict(_DEFAULT_POLICIES)
        for classification, policy in config.error_handling.items():
            self._policies[classification] = policy.model_dump()

        # DLQ file path: data/dlq/20260523_system_design_primer_errors.jsonl
        date_str = datetime.utcnow().strftime("%Y%m%d")
        safe_name = dataset_name.lower().replace(" ", "_")
        self._dlq_path = dlq_dir / f"{date_str}_{safe_name}_errors.jsonl"

    @property
    def dlq_path(self) -> Path:
        return self._dlq_path

    def get_policy(self, classification: str) -> dict:
        """Return the error policy for a classification (with defaults)."""
        return self._policies.get(
            classification,
            {
                "severity": "ERROR",
                "action": "skip_document",
                "dlq": True,
                "retry": False,
                "max_retries": 3,
            },
        )

    def should_retry(self, classification: str, retry_count: int) -> bool:
        """Return True if this error should be retried."""
        policy = self.get_policy(classification)
        return policy.get("retry", False) and retry_count < policy.get("max_retries", 3)

    def should_dlq(self, classification: str) -> bool:
        """Return True if this error should be written to DLQ."""
        return self.get_policy(classification).get("dlq", True)

    def record(self, error: ErrorRecord) -> None:
        """
        Buffer an error record for DLQ writing.

        Errors with dlq=False in their policy are only logged, not persisted.
        """
        policy = self.get_policy(error.classification)
        severity = policy.get("severity", "ERROR")

        log_fn = {
            "INFO": log.info,
            "WARN": log.warning,
            "ERROR": log.error,
            "FATAL": log.critical,
        }.get(severity, log.error)

        log_fn(
            "Ingestion error",
            extra={
                "classification": error.classification,
                "action": policy.get("action"),
                "source_uri": error.source_uri,
                "error": error.error_message,
            },
        )

        if policy.get("dlq", True):
            self._buffer.append(error)
            self._flush_to_dlq()  # Write immediately (no data loss on crash)

    def _flush_to_dlq(self) -> None:
        """Append buffered errors to DLQ file."""
        if not self._buffer:
            return
        self._dlq_path.parent.mkdir(parents=True, exist_ok=True)
        with self._dlq_path.open("a", encoding="utf-8") as f:
            for error in self._buffer:
                f.write(json.dumps(error.model_dump(mode="json"), default=str) + "\n")
        self._buffer.clear()

    def flush(self) -> None:
        """Explicitly flush any remaining buffered errors."""
        self._flush_to_dlq()

    def load_dlq(self) -> list[ErrorRecord]:
        """Load all ErrorRecords from the DLQ file for RESUME mode."""
        if not self._dlq_path.exists():
            return []
        records = []
        with self._dlq_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(ErrorRecord(**json.loads(line)))
                    except Exception as exc:
                        log.warning(f"Could not parse DLQ entry: {exc}")
        return records

    def error_rate(self, total_docs: int, failed_docs: int) -> float:
        """Compute error rate as fraction of total documents."""
        if total_docs == 0:
            return 0.0
        return round(failed_docs / total_docs, 4)
