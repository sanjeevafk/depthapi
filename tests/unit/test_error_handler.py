"""
tests/unit/test_error_handler.py

Phase 3: Tests for error classification, DLQ writing, and retry logic.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from api.services.rag.pipeline.config_schema import DatasetConfig
from api.services.rag.pipeline.error_handler import ErrorHandler
from api.services.rag.pipeline.models import ErrorRecord


MINIMAL_YAML = textwrap.dedent("""\
    name: "Test Dataset"
    version: "v1.0"
    source:
      type: "LocalDirSource"
      config:
        base_path: "."
    routing:
      - mime_type: "text/markdown"
        parser: "MarkdownParser"
        middleware: []
        chunker:
          name: "SemanticChunker"
          config: {}
    sink:
      type: "LocalJsonSink"
      config: {}
    error_handling:
      extraction_failed:
        severity: "ERROR"
        action: "skip_document"
        dlq: true
        retry: true
        max_retries: 3
      token_count_too_low:
        severity: "WARN"
        action: "skip_chunk"
        dlq: true
        retry: false
        max_retries: 0
      duplicate_content_hash:
        severity: "INFO"
        action: "skip_chunk"
        dlq: false
        retry: false
        max_retries: 0
""")


@pytest.fixture
def dataset_config(tmp_path: Path) -> DatasetConfig:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(MINIMAL_YAML)
    return DatasetConfig.from_yaml(config_path)


@pytest.fixture
def error_handler(dataset_config: DatasetConfig, tmp_path: Path) -> ErrorHandler:
    return ErrorHandler(
        config=dataset_config,
        dlq_dir=tmp_path / "dlq",
        dataset_name="Test Dataset",
    )


def make_error(classification: str = "extraction_failed") -> ErrorRecord:
    return ErrorRecord(
        error_id="err-001",
        severity="ERROR",
        classification=classification,
        action="skip_document",
        retryable=True,
        source_uri="file:///test.md",
        doc_id="abc123",
        error_message="Test error",
    )


class TestErrorHandler:
    def test_get_policy_extraction_failed(self, error_handler: ErrorHandler):
        policy = error_handler.get_policy("extraction_failed")
        assert policy["severity"] == "ERROR"
        assert policy["action"] == "skip_document"
        assert policy["retry"] is True
        assert policy["max_retries"] == 3

    def test_get_policy_token_count_too_low(self, error_handler: ErrorHandler):
        policy = error_handler.get_policy("token_count_too_low")
        assert policy["severity"] == "WARN"
        assert policy["retry"] is False

    def test_get_policy_unknown_returns_default(self, error_handler: ErrorHandler):
        policy = error_handler.get_policy("unknown_error_type")
        assert "severity" in policy
        assert "action" in policy

    def test_should_retry_respects_max_retries(self, error_handler: ErrorHandler):
        assert error_handler.should_retry("extraction_failed", retry_count=0) is True
        assert error_handler.should_retry("extraction_failed", retry_count=2) is True
        assert error_handler.should_retry("extraction_failed", retry_count=3) is False

    def test_should_retry_non_retryable(self, error_handler: ErrorHandler):
        assert error_handler.should_retry("token_count_too_low", retry_count=0) is False

    def test_should_dlq_true_for_retryable_error(self, error_handler: ErrorHandler):
        assert error_handler.should_dlq("extraction_failed") is True

    def test_should_dlq_false_for_duplicate(self, error_handler: ErrorHandler):
        assert error_handler.should_dlq("duplicate_content_hash") is False

    def test_record_writes_to_dlq_file(
        self, error_handler: ErrorHandler, tmp_path: Path
    ):
        err = make_error("extraction_failed")
        error_handler.record(err)

        dlq_files = list((tmp_path / "dlq").glob("*.jsonl"))
        assert len(dlq_files) == 1

        lines = dlq_files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["classification"] == "extraction_failed"
        assert record["error_message"] == "Test error"

    def test_record_no_dlq_for_duplicate(
        self, error_handler: ErrorHandler, tmp_path: Path
    ):
        err = make_error("duplicate_content_hash")
        error_handler.record(err)

        dlq_dir = tmp_path / "dlq"
        dlq_files = list(dlq_dir.glob("*.jsonl")) if dlq_dir.exists() else []
        # Either no files, or files but with 0 lines about this error
        if dlq_files:
            content = dlq_files[0].read_text().strip()
            assert "duplicate_content_hash" not in content

    def test_multiple_errors_appended(
        self, error_handler: ErrorHandler, tmp_path: Path
    ):
        for i in range(3):
            err = ErrorRecord(
                error_id=f"err-{i:03d}",
                severity="ERROR",
                classification="extraction_failed",
                action="skip_document",
                retryable=True,
                source_uri=f"file:///test_{i}.md",
                error_message=f"Error {i}",
            )
            error_handler.record(err)

        dlq_files = list((tmp_path / "dlq").glob("*.jsonl"))
        lines = [l for l in dlq_files[0].read_text().strip().split("\n") if l]
        assert len(lines) == 3

    def test_load_dlq_returns_error_records(
        self, error_handler: ErrorHandler, tmp_path: Path
    ):
        err = make_error("extraction_failed")
        error_handler.record(err)

        loaded = error_handler.load_dlq()
        assert len(loaded) == 1
        assert loaded[0].classification == "extraction_failed"

    def test_error_rate_calculation(self, error_handler: ErrorHandler):
        assert error_handler.error_rate(100, 5) == 0.05
        assert error_handler.error_rate(0, 0) == 0.0
        assert error_handler.error_rate(100, 0) == 0.0
