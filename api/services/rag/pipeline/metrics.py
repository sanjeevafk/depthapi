"""
metrics.py — Pipeline Metrics Emitter.

Emits structured metrics as JSON log events so they can be parsed with jq.
No external metrics infrastructure required for Phase 1-5.

All counters are scoped to a single ingestion run. A new PipelineMetrics
instance is created per run by the orchestrator.

Metric events can be extracted from log files:
    # Count chunks written for a specific run
    jq 'select(.event == "metric" and .name == "chunks_written")' \
        data/logs/ingest_20260523.jsonl

    # Get timing percentiles
    jq 'select(.event == "metric" and .name == "doc_latency_ms") | .value' \
        data/logs/ingest_20260523.jsonl | sort -n
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)


# ─── Latency tracking ─────────────────────────────────────────────────────────


@dataclass
class LatencyStats:
    """Running statistics for a latency series."""

    _samples: list[float] = field(default_factory=list)

    def record(self, value_ms: float) -> None:
        self._samples.append(value_ms)

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def total_ms(self) -> float:
        return sum(self._samples)

    @property
    def min_ms(self) -> float | None:
        return min(self._samples) if self._samples else None

    @property
    def max_ms(self) -> float | None:
        return max(self._samples) if self._samples else None

    @property
    def avg_ms(self) -> float | None:
        return self.total_ms / self.count if self._samples else None

    def percentile(self, p: float) -> float | None:
        """Return the p-th percentile (0-100) from samples."""
        if not self._samples:
            return None
        sorted_samples = sorted(self._samples)
        idx = int(len(sorted_samples) * p / 100)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "total_ms": round(self.total_ms, 2),
            "min_ms": round(self.min_ms, 2) if self.min_ms is not None else None,
            "max_ms": round(self.max_ms, 2) if self.max_ms is not None else None,
            "avg_ms": round(self.avg_ms, 2) if self.avg_ms is not None else None,
            "p50_ms": round(p50, 2) if (p50 := self.percentile(50)) is not None else None,
            "p95_ms": round(p95, 2) if (p95 := self.percentile(95)) is not None else None,
            "p99_ms": round(p99, 2) if (p99 := self.percentile(99)) is not None else None,
        }


# ─── Per-run metrics ──────────────────────────────────────────────────────────


class PipelineMetrics:
    """
    Collects and emits metrics for a single pipeline ingestion run.

    All metrics are logged as structured JSON events so they can be
    parsed from JSONL log files using jq. No external infrastructure needed.

    Usage:
        metrics = PipelineMetrics(dataset_name="system-design-primer", run_id="abc123")
        metrics.inc("docs_processed")
        with metrics.time("doc_latency_ms"):
            process_document(doc)
        metrics.emit_summary()
    """

    def __init__(self, dataset_name: str, run_id: str, prefix: str = "depthapi.ingest") -> None:
        self.dataset_name = dataset_name
        self.run_id = run_id
        self.prefix = prefix

        # Counters
        self._counters: dict[str, int] = {
            "docs_fetched": 0,
            "docs_processed": 0,
            "docs_skipped": 0,
            "docs_failed": 0,
            "chunks_written": 0,
            "chunks_skipped_duplicate": 0,
            "chunks_skipped_too_short": 0,
            "dlq_entries": 0,
        }

        # Latency series (in ms)
        self._latencies: dict[str, LatencyStats] = {
            "doc_parse_ms": LatencyStats(),
            "doc_chunk_ms": LatencyStats(),
            "doc_total_ms": LatencyStats(),
            "sink_write_ms": LatencyStats(),
        }

        self._run_start = time.monotonic()

    # ── Counter API ───────────────────────────────────────────────────────────

    def inc(self, counter: str, amount: int = 1) -> None:
        """Increment a named counter."""
        if counter not in self._counters:
            self._counters[counter] = 0
        self._counters[counter] += amount
        log.debug(
            "metric",
            name=counter,
            value=self._counters[counter],
            delta=amount,
            dataset=self.dataset_name,
            run_id=self.run_id,
        )

    def get(self, counter: str) -> int:
        return self._counters.get(counter, 0)

    # ── Latency API ───────────────────────────────────────────────────────────

    @contextmanager
    def time(self, series: str) -> Generator[None, None, None]:
        """Context manager that records elapsed time in ms for a named series."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            if series not in self._latencies:
                self._latencies[series] = LatencyStats()
            self._latencies[series].record(elapsed_ms)
            log.debug(
                "metric",
                name=series,
                value=round(elapsed_ms, 2),
                dataset=self.dataset_name,
                run_id=self.run_id,
            )

    def record_latency(self, series: str, value_ms: float) -> None:
        """Record a latency value directly (for externally-timed operations)."""
        if series not in self._latencies:
            self._latencies[series] = LatencyStats()
        self._latencies[series].record(value_ms)

    # ── Summary emission ─────────────────────────────────────────────────────

    def emit_summary(self) -> dict:
        """
        Emit and return a summary of all metrics for this run.

        The summary is logged as a single JSON event for easy parsing.
        """
        run_duration_ms = (time.monotonic() - self._run_start) * 1000

        total_docs = self._counters.get("docs_processed", 0) + self._counters.get("docs_failed", 0)
        error_rate = (
            self._counters.get("docs_failed", 0) / max(1, total_docs)
        )

        summary = {
            "event": "ingestion_summary",
            "dataset": self.dataset_name,
            "run_id": self.run_id,
            "run_duration_ms": round(run_duration_ms, 2),
            "error_rate": round(error_rate, 4),
            "counters": dict(self._counters),
            "latencies": {
                series: stats.to_dict()
                for series, stats in self._latencies.items()
                if stats.count > 0
            },
        }

        log.info(
            "ingestion_summary",
            dataset=self.dataset_name,
            run_id=self.run_id,
            run_duration_ms=summary["run_duration_ms"],
            error_rate=summary["error_rate"],
            counters=summary["counters"],
            latencies=summary["latencies"],
        )

        return summary
