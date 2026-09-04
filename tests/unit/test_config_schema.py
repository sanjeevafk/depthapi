"""
tests/unit/test_config_schema.py

Phase 0: Tests for YAML config schema validation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from api.services.rag.pipeline.config_schema import (
    DatasetConfig,
)

VALID_YAML = textwrap.dedent("""\
    name: "System Design Primer"
    version: "v1.0"
    description: "System design interview prep guide"
    namespace: "ai_ref_knowledge"

    source:
      type: "LocalDirSource"
      config:
        base_path: "datasets/system-design-primer"
        include: ["*.md"]

    routing:
      - mime_type: "text/markdown"
        parser: "MarkdownParser"
        middleware:
          - name: "TocStripper"
            config:
              depth: 3
          - name: "AsciiDiagramPreserver"
            config:
              preserve_box_drawings: true
        chunker:
          name: "SemanticChunker"
          config:
            max_tokens: 480
            min_tokens: 50

    sink:
      type: "LocalJsonSink"
      config:
        output_path: "data/rag/trusted/chunks.json"

    error_handling:
      token_count_too_low:
        severity: "WARN"
        action: "skip_chunk"
        dlq: true
        retry: false
      extraction_failed:
        severity: "ERROR"
        action: "skip_document"
        dlq: true
        retry: true
        max_retries: 3

    observability:
      log_level: "INFO"
      emit_metrics: true
      metrics_prefix: "depthapi.ingest"
""")


@pytest.fixture
def valid_config_file(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_YAML, encoding="utf-8")
    return config_path


# ─── DatasetConfig loading ────────────────────────────────────────────────────

class TestDatasetConfigFromYaml:
    def test_loads_valid_config(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        assert config.name == "System Design Primer"
        assert config.version == "v1.0"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            DatasetConfig.from_yaml(tmp_path / "nonexistent.yaml")

    def test_routing_has_one_rule(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        assert len(config.routing) == 1
        assert config.routing[0].mime_type == "text/markdown"

    def test_middleware_chain_loaded(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        mw_names = [m.name for m in config.routing[0].middleware]
        assert "TocStripper" in mw_names
        assert "AsciiDiagramPreserver" in mw_names

    def test_error_policies_loaded(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        assert "token_count_too_low" in config.error_handling
        assert config.error_handling["token_count_too_low"].severity == "WARN"

    def test_config_is_immutable(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        with pytest.raises(Exception):
            config.name = "mutated"  # type: ignore[misc]


class TestDatasetConfigGetters:
    def test_get_routing_rule_by_mime(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        rule = config.get_routing_rule("text/markdown")
        assert rule is not None
        assert rule.parser == "MarkdownParser"

    def test_get_routing_rule_unknown_mime_returns_none(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        rule = config.get_routing_rule("application/pdf")
        assert rule is None

    def test_get_error_policy_known(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        policy = config.get_error_policy("extraction_failed")
        assert policy is not None
        assert policy.action == "skip_document"
        assert policy.retry is True

    def test_get_error_policy_unknown_returns_none(self, valid_config_file: Path):
        config = DatasetConfig.from_yaml(valid_config_file)
        policy = config.get_error_policy("unknown_error")
        assert policy is None


# ─── Validation failures ──────────────────────────────────────────────────────

class TestConfigValidation:
    def _write_and_load(self, tmp_path: Path, yaml_text: str) -> DatasetConfig:
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        return DatasetConfig.from_yaml(p)

    def test_invalid_source_type_raises(self, tmp_path: Path):
        bad_yaml = VALID_YAML.replace(
            'type: "LocalDirSource"', 'type: "UnknownSource"'
        )
        with pytest.raises(Exception):
            self._write_and_load(tmp_path, bad_yaml)

    def test_invalid_sink_type_raises(self, tmp_path: Path):
        bad_yaml = VALID_YAML.replace(
            'type: "LocalJsonSink"', 'type: "UnknownSink"'
        )
        with pytest.raises(Exception):
            self._write_and_load(tmp_path, bad_yaml)

    def test_invalid_mime_type_raises(self, tmp_path: Path):
        bad_yaml = VALID_YAML.replace('mime_type: "text/markdown"', 'mime_type: "badmime"')
        with pytest.raises(Exception):
            self._write_and_load(tmp_path, bad_yaml)

    def test_invalid_severity_raises(self, tmp_path: Path):
        bad_yaml = VALID_YAML.replace('severity: "WARN"', 'severity: "CRITICAL"')
        with pytest.raises(Exception):
            self._write_and_load(tmp_path, bad_yaml)

    def test_extra_field_in_source_raises(self, tmp_path: Path):
        bad_yaml = VALID_YAML.replace(
            'type: "LocalDirSource"',
            'type: "LocalDirSource"\n  unknown_field: "bad"',
        )
        with pytest.raises(Exception):
            self._write_and_load(tmp_path, bad_yaml)
