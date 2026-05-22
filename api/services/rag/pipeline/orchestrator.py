"""
orchestrator.py — Pipeline Orchestrator.

Reads a DatasetConfig, assembles source/parser/middleware/chunker/sink plugins,
and runs the ingestion pipeline in the correct order.

Ingestion modes:
    FULL        — Reprocess all documents from scratch
    INCREMENTAL — Process only changed/new documents (default)
    RESUME      — Retry failed documents from DLQ

Design constraints:
    - Single-machine, single-process, no distributed coordination (Phase 1-5)
    - Asyncio + bounded semaphore for concurrent document processing
    - Process pool for CPU-heavy parsing/chunking
    - All state stored locally (fingerprints JSON, DLQ JSONL)
    - Deterministic: same source + same config → same output
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from api.services.rag.pipeline.config_schema import DatasetConfig
from api.services.rag.pipeline.error_handler import ErrorHandler
from api.services.rag.pipeline.interfaces import (
    BaseChunker,
    BaseMiddleware,
    BaseParser,
    BaseSink,
    BaseSource,
)
from api.services.rag.pipeline.models import (
    Chunk,
    Document,
    ErrorRecord,
    IngestionResult,
    ParsedDocument,
    SourceFingerprint,
)
from api.services.rag.pipeline.registry import PluginRegistry

log = logging.getLogger(__name__)


# ─── Ingestion mode ───────────────────────────────────────────────────────────

class IngestionMode(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    RESUME = "resume"


# ─── State file paths ────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[5]
_STATE_DIR = _REPO_ROOT / "data" / "pipeline_state"
_DLQ_DIR = _REPO_ROOT / "data" / "dlq"


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class PipelineOrchestrator:
    """
    Assembles and runs the ingestion pipeline from a DatasetConfig.

    Responsibilities:
        1. Load plugin implementations from config via registry
        2. Manage source fingerprints for incremental mode
        3. Route documents through: Source → Parser → Middleware → Chunker → Sink
        4. Classify and route errors to DLQ or retry
        5. Track and persist ingestion state
    """

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        state_dir: Path | None = None,
        dlq_dir: Path | None = None,
    ) -> None:
        self._registry = registry or PluginRegistry.default()
        self._state_dir = state_dir or _STATE_DIR
        self._dlq_dir = dlq_dir or _DLQ_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._dlq_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(
        self,
        config_path: str | Path,
        mode: IngestionMode = IngestionMode.INCREMENTAL,
    ) -> IngestionResult:
        """
        Run a full ingestion pipeline for the given dataset config.

        Args:
            config_path: Path to the YAML dataset config file.
            mode: FULL, INCREMENTAL, or RESUME.

        Returns:
            IngestionResult summary with counts and DLQ path.
        """
        config = DatasetConfig.from_yaml(config_path)
        started_at = datetime.utcnow()
        log.info(
            "Starting ingestion",
            extra={
                "dataset": config.name,
                "version": config.version,
                "mode": mode.value,
            },
        )

        # Build plugin instances from config
        source = self._build_source(config)
        sink = self._build_sink(config)
        error_handler = ErrorHandler(
            config=config,
            dlq_dir=self._dlq_dir,
            dataset_name=config.name,
        )

        # Load fingerprints for incremental mode
        fingerprints = self._load_fingerprints(config.name) if mode == IngestionMode.INCREMENTAL else {}

        # Tracking counters
        docs_processed = 0
        docs_skipped = 0
        docs_failed = 0
        chunks_written = 0
        chunks_skipped_dup = 0
        chunks_skipped_short = 0
        new_fingerprints: dict[str, SourceFingerprint] = dict(fingerprints)

        # Main ingestion loop
        for doc, fingerprint in source.fetch(since=fingerprints if mode == IngestionMode.INCREMENTAL else None):
            try:
                result = self._process_document(doc, config)
                chunks = result["chunks"]
                written = sink.write(chunks)

                chunks_written += written
                chunks_skipped_dup += result.get("skipped_dup", 0)
                chunks_skipped_short += result.get("skipped_short", 0)
                docs_processed += 1
                new_fingerprints[doc.source_uri] = fingerprint

            except Exception as exc:
                docs_failed += 1
                tb = traceback.format_exc()
                error_record = ErrorRecord(
                    error_id=str(uuid.uuid4()),
                    severity="ERROR",
                    classification="processing_failed",
                    action="skip_document",
                    retryable=True,
                    source_uri=doc.source_uri,
                    doc_id=doc.doc_id,
                    error_message=str(exc),
                    traceback=tb,
                    raw_content_preview=doc.raw_content[:200].decode("utf-8", errors="replace"),
                )
                error_handler.record(error_record)
                log.error(
                    "Document processing failed",
                    extra={"source_uri": doc.source_uri, "error": str(exc)},
                )

        # Persist updated fingerprints
        if mode in (IngestionMode.FULL, IngestionMode.INCREMENTAL):
            self._save_fingerprints(config.name, new_fingerprints)

        total_docs = docs_processed + docs_failed
        error_rate = docs_failed / max(1, total_docs)

        result_obj = IngestionResult(
            dataset_name=config.name,
            mode=mode.value,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            documents_processed=docs_processed,
            documents_skipped=docs_skipped,
            documents_failed=docs_failed,
            chunks_written=chunks_written,
            chunks_skipped_duplicate=chunks_skipped_dup,
            chunks_skipped_too_short=chunks_skipped_short,
            dlq_path=str(error_handler.dlq_path) if docs_failed > 0 else None,
            error_rate=round(error_rate, 4),
        )

        log.info(
            "Ingestion complete",
            extra={
                "dataset": config.name,
                "docs_processed": docs_processed,
                "docs_failed": docs_failed,
                "chunks_written": chunks_written,
                "error_rate": error_rate,
            },
        )

        return result_obj

    # ── Document processing ───────────────────────────────────────────────────

    def _process_document(self, doc: Document, config: DatasetConfig) -> dict[str, Any]:
        """
        Process one document through the full pipeline.

        Returns dict with 'chunks', 'skipped_dup', 'skipped_short'.
        """
        # Get routing rule for this MIME type
        routing_rule = config.get_routing_rule(doc.mime_type)
        if routing_rule is None:
            log.warning(
                "No routing rule for MIME type — skipping",
                extra={"mime_type": doc.mime_type, "source_uri": doc.source_uri},
            )
            return {"chunks": [], "skipped_dup": 0, "skipped_short": 0}

        # 1. Parse
        parser_cls = self._registry.get_parser(routing_rule.parser)
        parser = parser_cls(config={})
        parsed_doc = parser.parse(doc)

        # 2. Apply middleware chain
        for mw_config in routing_rule.middleware:
            mw_cls = self._registry.get_middleware(mw_config.name)
            middleware = mw_cls(config=mw_config.config)
            parsed_doc = middleware.process(parsed_doc)

        # 3. Chunk
        chunker_cls = self._registry.get_chunker(routing_rule.chunker.name)
        chunker = chunker_cls(config=routing_rule.chunker.config)
        chunks = chunker.chunk(
            doc=parsed_doc,
            dataset_version=config.version,
            source_name=config.name,
            source_url=doc.source_uri,
            dataset_namespace=config.namespace,
        )

        return {"chunks": chunks, "skipped_dup": 0, "skipped_short": 0}

    # ── Plugin builders ───────────────────────────────────────────────────────

    def _build_source(self, config: DatasetConfig) -> BaseSource:
        source_cls = self._registry.get_source(config.source.type)
        return source_cls(config=config.source.config)

    def _build_sink(self, config: DatasetConfig) -> BaseSink:
        sink_cls = self._registry.get_sink(config.sink.type)
        return sink_cls(config=config.sink.config)

    # ── State management ──────────────────────────────────────────────────────

    def _fingerprint_path(self, dataset_name: str) -> Path:
        safe_name = dataset_name.lower().replace(" ", "_").replace("/", "_")
        return self._state_dir / f"{safe_name}_fingerprints.json"

    def _load_fingerprints(self, dataset_name: str) -> dict[str, SourceFingerprint]:
        path = self._fingerprint_path(dataset_name)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {
                uri: SourceFingerprint(**fp_data)
                for uri, fp_data in raw.items()
            }
        except Exception as exc:
            log.warning(f"Could not load fingerprints from {path}: {exc}")
            return {}

    def _save_fingerprints(
        self,
        dataset_name: str,
        fingerprints: dict[str, SourceFingerprint],
    ) -> None:
        path = self._fingerprint_path(dataset_name)
        serialized = {
            uri: fp.model_dump(mode="json")
            for uri, fp in fingerprints.items()
        }
        path.write_text(
            json.dumps(serialized, indent=2, default=str),
            encoding="utf-8",
        )
        log.debug(f"Saved {len(fingerprints)} fingerprints to {path}")
