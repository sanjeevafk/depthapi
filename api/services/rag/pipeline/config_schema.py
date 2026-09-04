"""
config_schema.py — Pydantic validators for declarative YAML dataset configs.

YAML configs are strictly declarative — no Python code, conditionals,
or DSLs allowed. This schema enforces that constraint at load time.

Usage:
    config = DatasetConfig.from_yaml("datasets/system_design_primer/config.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

# ─── Sub-schemas ──────────────────────────────────────────────────────────────

class SourceConfig(BaseModel):
    """Source plugin configuration."""

    type: str  # e.g. "LocalDirSource", "GitRepoSource", "HTTPSource"
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_source_type(self) -> SourceConfig:
        allowed_sources = {
            "LocalDirSource",
            "GitRepoSource",
            "HTTPSource",
            "S3BucketSource",
        }
        if self.type not in allowed_sources:
            raise ValueError(
                f"Unknown source type '{self.type}'. "
                f"Allowed: {sorted(allowed_sources)}"
            )
        return self


class MiddlewareConfig(BaseModel):
    """Single middleware step in the pipeline routing."""

    name: str  # e.g. "TocStripper", "AsciiDiagramPreserver"
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True, "extra": "forbid"}


class ChunkerConfig(BaseModel):
    """Chunker plugin configuration for a routing rule."""

    name: str  # e.g. "SemanticChunker", "ASTAwareChunker"
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True, "extra": "forbid"}


class RoutingRule(BaseModel):
    """
    One routing rule: maps a MIME type to parser + middleware + chunker.

    The pipeline matches each document's mime_type to the first matching rule.
    """

    mime_type: str  # e.g. "text/markdown"
    parser: str  # e.g. "MarkdownParser"
    middleware: list[MiddlewareConfig] = Field(default_factory=list)
    chunker: ChunkerConfig

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_mime_type(self) -> RoutingRule:
        if "/" not in self.mime_type:
            raise ValueError(f"Invalid MIME type: '{self.mime_type}' (must contain '/')")
        return self


class SinkConfig(BaseModel):
    """Sink plugin configuration."""

    type: str  # e.g. "PostgresVectorSink", "LocalJsonSink"
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_sink_type(self) -> SinkConfig:
        allowed_sinks = {"PostgresVectorSink", "LocalJsonSink"}
        if self.type not in allowed_sinks:
            raise ValueError(
                f"Unknown sink type '{self.type}'. "
                f"Allowed: {sorted(allowed_sinks)}"
            )
        return self


class ErrorPolicyConfig(BaseModel):
    """Error handling policy for a specific error classification."""

    severity: str  # "INFO" | "WARN" | "ERROR" | "FATAL"
    action: str  # "skip_chunk" | "skip_document" | "retry" | "redact_and_continue"
    dlq: bool = True
    retry: bool = False
    max_retries: int = 3

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_severity(self) -> ErrorPolicyConfig:
        allowed = {"INFO", "WARN", "ERROR", "FATAL"}
        if self.severity not in allowed:
            raise ValueError(f"Invalid severity '{self.severity}'. Allowed: {allowed}")
        return self


class ObservabilityConfig(BaseModel):
    """Logging and metrics configuration."""

    log_level: str = "INFO"
    emit_metrics: bool = True
    metrics_prefix: str = "depthapi.ingest"

    model_config = {"frozen": True, "extra": "forbid"}


# ─── Top-level dataset config ─────────────────────────────────────────────────

class DatasetConfig(BaseModel):
    """
    Complete declarative configuration for one dataset ingestion run.

    Loaded from a YAML file. All processing logic lives in plugin code;
    this schema enforces the boundary between config (WHAT) and code (HOW).
    """

    name: str
    version: str  # e.g. "v1.0" — used in lineage tracking
    description: str = ""
    namespace: str | None = None  # e.g. "ai_ref_knowledge"

    source: SourceConfig
    routing: list[RoutingRule] = Field(min_length=1)
    sink: SinkConfig
    error_handling: dict[str, ErrorPolicyConfig] = Field(default_factory=dict)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_routing_not_empty(self) -> DatasetConfig:
        if not self.routing:
            raise ValueError("'routing' must contain at least one rule.")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> DatasetConfig:
        """Load and validate a dataset config from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Dataset config not found: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Dataset config must be a YAML mapping, got: {type(raw)}")

        return cls.model_validate(raw)

    def get_routing_rule(self, mime_type: str) -> RoutingRule | None:
        """Return the first routing rule matching the given MIME type."""
        for rule in self.routing:
            if rule.mime_type == mime_type:
                return rule
        return None

    def get_error_policy(self, classification: str) -> ErrorPolicyConfig | None:
        """Return the error policy for a specific error classification."""
        return self.error_handling.get(classification)
