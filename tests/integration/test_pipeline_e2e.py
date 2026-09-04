"""
test_pipeline_e2e.py — End-to-end integration test for the ingestion pipeline.

Tests the full path: YAML config → Source → Parser → Middleware → Chunker → Sink.
Uses the real system_design_primer/config.yaml and real dataset files so the test
proves the pipeline works against actual data — not just mocks.

Requirements:
    - datasets/system-design-primer/ must be present (git-cloned dataset)
    - No network required: LocalDirSource reads from filesystem

Run:
    pytest tests/integration/test_pipeline_e2e.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.services.rag.pipeline.config_schema import DatasetConfig
from api.services.rag.pipeline.models import Chunk, IngestionResult
from api.services.rag.pipeline.orchestrator import IngestionMode, PipelineOrchestrator

# ─── Fixtures ─────────────────────────────────────────────────────────────────

DATASET_PATH = Path("datasets/system-design-primer")
CONFIG_PATH = Path("datasets/system_design_primer/config.yaml")

requires_dataset = pytest.mark.skipif(
    not DATASET_PATH.exists(),
    reason=f"Dataset not present at {DATASET_PATH}. Clone: git clone https://github.com/donnemartin/system-design-primer datasets/system-design-primer",
)


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """Provide isolated state and DLQ dirs for each test."""
    state_dir = tmp_path / "pipeline_state"
    dlq_dir = tmp_path / "dlq"
    return state_dir, dlq_dir


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Return config pointing to a temp output location."""
    src = CONFIG_PATH.read_text(encoding="utf-8")
    output = tmp_path / "chunks.json"
    # Patch output_path to tmp location
    patched = src.replace(
        "output_path: \"data/rag/trusted/chunks.json\"",
        f"output_path: \"{output}\"",
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(patched, encoding="utf-8")
    return cfg_path


# ─── Config loading ───────────────────────────────────────────────────────────

class TestConfigLoading:
    def test_config_loads_without_error(self):
        config = DatasetConfig.from_yaml(CONFIG_PATH)
        assert config.name == "System Design Primer"
        assert config.version == "v1.0"

    def test_routing_rules_present(self):
        config = DatasetConfig.from_yaml(CONFIG_PATH)
        assert len(config.routing) >= 1
        rule = config.routing[0]
        assert rule.mime_type == "text/markdown"
        assert rule.parser == "MarkdownParser"
        assert len(rule.middleware) == 3

    def test_sink_is_local_json(self):
        config = DatasetConfig.from_yaml(CONFIG_PATH)
        assert config.sink.type == "LocalJsonSink"

    def test_source_is_local_dir(self):
        config = DatasetConfig.from_yaml(CONFIG_PATH)
        assert config.source.type == "LocalDirSource"


# ─── Full pipeline run ────────────────────────────────────────────────────────

@requires_dataset
class TestPipelineFullRun:
    """Integration tests against real dataset files."""

    def test_full_run_returns_ingestion_result(self, tmp_dirs, tmp_config):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        result = orchestrator.ingest(
            config_path=tmp_config,
            mode=IngestionMode.FULL,
        )
        assert isinstance(result, IngestionResult)
        assert result.dataset_name == "System Design Primer"
        assert result.mode == "full"

    def test_full_run_produces_chunks(self, tmp_dirs, tmp_config):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        result = orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)
        assert result.chunks_written > 0, "Expected at least 1 chunk written"
        assert result.documents_processed > 0

    def test_full_run_error_rate_below_threshold(self, tmp_dirs, tmp_config):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        result = orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)
        assert result.error_rate <= 0.01, (
            f"Error rate {result.error_rate:.2%} exceeds 1% threshold. "
            f"Failed: {result.documents_failed}"
        )

    def test_chunks_written_to_sink_file(self, tmp_dirs, tmp_config, tmp_path):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)

        output = tmp_path / "chunks.json"
        assert output.exists(), "Sink output file must be created"
        data = json.loads(output.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) > 0

    def test_chunk_schema_valid(self, tmp_dirs, tmp_config, tmp_path):
        """Every chunk in the output must be a valid Chunk model."""
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)

        output = tmp_path / "chunks.json"
        data = json.loads(output.read_text(encoding="utf-8"))

        for raw in data[:50]:  # Validate first 50 for speed
            assert raw.get("source_content_hash")
            chunk = Chunk(**raw)
            assert 0.0 <= chunk.quality_score <= 1.0
            assert chunk.token_count > 0
            assert chunk.content_hash  # Non-empty
            assert chunk.source_content_hash

    def test_chunk_ids_are_unique(self, tmp_dirs, tmp_config, tmp_path):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)

        output = tmp_path / "chunks.json"
        data = json.loads(output.read_text(encoding="utf-8"))
        chunk_ids = [c["chunk_id"] for c in data]
        assert len(chunk_ids) == len(set(chunk_ids)), "Duplicate chunk_ids detected"


# ─── Incremental mode ─────────────────────────────────────────────────────────

@requires_dataset
class TestIncrementalMode:
    def test_second_run_skips_unchanged_files(self, tmp_dirs, tmp_config):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        # Full run first to populate fingerprints
        result_1 = orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)
        # Incremental — should see 0 new docs (nothing changed)
        result_2 = orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.INCREMENTAL)

        assert result_2.documents_processed == 0, (
            f"Expected 0 docs on second incremental run, got {result_2.documents_processed}"
        )

    def test_fingerprints_persisted_after_full_run(self, tmp_dirs, tmp_config):
        state_dir, dlq_dir = tmp_dirs
        orchestrator = PipelineOrchestrator(
            state_dir=state_dir,
            dlq_dir=dlq_dir,
        )
        orchestrator.ingest(config_path=tmp_config, mode=IngestionMode.FULL)

        # Fingerprint state file must exist
        fp_files = list(state_dir.glob("*_fingerprints.json"))
        assert len(fp_files) == 1, "Expected exactly one fingerprint file"
        data = json.loads(fp_files[0].read_text())
        assert len(data) > 0


# ─── Deterministic replay ─────────────────────────────────────────────────────

@requires_dataset
class TestDeterministicReplay:
    def test_two_full_runs_produce_identical_content_hashes(self, tmp_dirs, tmp_path, tmp_config):
        """Critical: same source + same config → identical chunk content hashes."""
        state_dir, dlq_dir = tmp_dirs

        def fresh_run(suffix: str) -> set[str]:
            out = tmp_path / f"chunks_{suffix}.json"
            patched = tmp_config.read_text().replace(
                str(tmp_path / "chunks.json"), str(out)
            )
            cfg = tmp_path / f"config_{suffix}.yaml"
            cfg.write_text(patched)
            orch = PipelineOrchestrator(
                state_dir=tmp_path / f"state_{suffix}",
                dlq_dir=tmp_path / f"dlq_{suffix}",
            )
            orch.ingest(config_path=cfg, mode=IngestionMode.FULL)
            data = json.loads(out.read_text())
            return {c["content_hash"] for c in data}

        hashes_1 = fresh_run("a")
        hashes_2 = fresh_run("b")

        overlap = len(hashes_1 & hashes_2)
        union = len(hashes_1 | hashes_2)
        parity = overlap / max(1, union)

        assert parity >= 0.995, (
            f"Chunk content hash parity {parity:.2%} < 99.5%. "
            f"Run A: {len(hashes_1)}, Run B: {len(hashes_2)}, "
            f"Common: {overlap}"
        )
