"""
test_metrics.py — Unit tests for PipelineMetrics.

Tests counter increments, latency recording, percentile calculations,
and summary emission structure.
"""

from __future__ import annotations

import time

import pytest

from api.services.rag.pipeline.metrics import LatencyStats, PipelineMetrics


class TestLatencyStats:
    def test_empty_stats_return_none(self):
        stats = LatencyStats()
        assert stats.min_ms is None
        assert stats.max_ms is None
        assert stats.avg_ms is None
        assert stats.percentile(50) is None
        assert stats.count == 0

    def test_single_sample(self):
        stats = LatencyStats()
        stats.record(100.0)
        assert stats.count == 1
        assert stats.min_ms == 100.0
        assert stats.max_ms == 100.0
        assert stats.avg_ms == 100.0
        assert stats.percentile(50) == 100.0

    def test_percentiles(self):
        stats = LatencyStats()
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            stats.record(float(v))
        assert stats.min_ms == 10.0
        assert stats.max_ms == 100.0
        p50 = stats.percentile(50)
        assert p50 is not None
        assert 40.0 <= p50 <= 60.0

    def test_to_dict_keys(self):
        stats = LatencyStats()
        stats.record(42.0)
        d = stats.to_dict()
        assert "count" in d
        assert "avg_ms" in d
        assert "p50_ms" in d
        assert "p95_ms" in d
        assert "p99_ms" in d


class TestPipelineMetrics:
    def test_counter_increments(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        assert m.get("docs_processed") == 0
        m.inc("docs_processed")
        assert m.get("docs_processed") == 1
        m.inc("docs_processed", 5)
        assert m.get("docs_processed") == 6

    def test_custom_counter(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        m.inc("custom_counter", 3)
        assert m.get("custom_counter") == 3

    def test_time_context_manager_records_latency(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        with m.time("doc_parse_ms"):
            time.sleep(0.01)  # 10ms
        stats = m._latencies["doc_parse_ms"]
        assert stats.count == 1
        assert stats.avg_ms is not None
        assert stats.avg_ms >= 5.0  # At least 5ms

    def test_time_records_multiple_samples(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        for _ in range(3):
            with m.time("doc_parse_ms"):
                time.sleep(0.005)
        assert m._latencies["doc_parse_ms"].count == 3

    def test_record_latency_direct(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        m.record_latency("doc_chunk_ms", 123.4)
        assert m._latencies["doc_chunk_ms"].count == 1
        assert m._latencies["doc_chunk_ms"].min_ms == 123.4

    def test_emit_summary_structure(self):
        m = PipelineMetrics(dataset_name="MyDataset", run_id="run123")
        m.inc("docs_processed", 10)
        m.inc("docs_failed", 1)
        m.inc("chunks_written", 50)
        m.record_latency("doc_total_ms", 200.0)
        summary = m.emit_summary()

        assert summary["dataset"] == "MyDataset"
        assert summary["run_id"] == "run123"
        assert summary["counters"]["docs_processed"] == 10
        assert summary["counters"]["chunks_written"] == 50
        assert "doc_total_ms" in summary["latencies"]
        assert summary["error_rate"] == pytest.approx(1 / 11, abs=0.001)

    def test_error_rate_zero_when_no_failures(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        m.inc("docs_processed", 5)
        summary = m.emit_summary()
        assert summary["error_rate"] == 0.0

    def test_run_duration_positive(self):
        m = PipelineMetrics(dataset_name="test", run_id="r1")
        time.sleep(0.01)
        summary = m.emit_summary()
        assert summary["run_duration_ms"] > 0
