from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ChunkingConfig:
    chunk_size: int = 1200
    overlap: int = 150
    tokenizer: str = "cl100k_base"
    semantic_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "min_section_chars": 300.0,
            "max_section_chars": 4800.0,
            "sentence_soft_limit": 0.82,
        }
    )
    chunker_version: str = "research-corpus-v1"


@dataclass
class DedupConfig:
    fuzzy_ngram_size: int = 5
    fuzzy_threshold: float = 0.92
    semantic_similarity_threshold: float = 0.97
    minhash_permutations: int = 64


@dataclass
class ValidationConfig:
    min_chars: int = 80
    max_chars: int = 8000
    boilerplate_threshold: float = 0.2


@dataclass
class BenchmarkConfig:
    top_k: list[int] = field(default_factory=lambda: [1, 3, 5, 10])
    embedding_models: list[str] = field(
        default_factory=lambda: ["bge", "e5", "gte", "jina", "nomic"]
    )
    rerankers: list[str] = field(
        default_factory=lambda: ["bge-reranker-base", "jina-reranker-v1"]
    )


@dataclass
class PublishConfig:
    repo_id: str = "sanjeevafk/depthapi_technical_corpus"
    split: str = "train"
    commit_message: str = "Refresh dataset from PostgreSQL research corpus pipeline"
    private: bool = False


@dataclass
class PipelineConfig:
    repo_root: Path
    workspace_dir: Path
    raw_export_path: Path
    normalized_path: Path
    deduped_path: Path
    duplicate_path: Path
    benchmark_dir: Path
    reports_dir: Path
    manifest_path: Path
    license_summary_path: Path
    dataset_card_path: Path
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)

    @classmethod
    def default(cls, repo_root: Path) -> "PipelineConfig":
        workspace = repo_root / "data" / "research_corpus"
        return cls(
            repo_root=repo_root,
            workspace_dir=workspace,
            raw_export_path=workspace / "raw_export.jsonl",
            normalized_path=workspace / "normalized_chunks.parquet",
            deduped_path=workspace / "deduped_chunks.parquet",
            duplicate_path=workspace / "removed_duplicates.parquet",
            benchmark_dir=workspace / "benchmarks",
            reports_dir=workspace / "reports",
            manifest_path=repo_root / "SOURCES_MANIFEST.yaml",
            license_summary_path=repo_root / "LICENSE_SUMMARY.md",
            dataset_card_path=repo_root / "datasets" / "depthapi_technical_corpus" / "README.md",
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.workspace_dir,
            self.benchmark_dir,
            self.reports_dir,
            self.dataset_card_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def to_json(self) -> str:
        data = asdict(self)
        data["repo_root"] = str(self.repo_root)
        data["workspace_dir"] = str(self.workspace_dir)
        data["raw_export_path"] = str(self.raw_export_path)
        data["normalized_path"] = str(self.normalized_path)
        data["deduped_path"] = str(self.deduped_path)
        data["duplicate_path"] = str(self.duplicate_path)
        data["benchmark_dir"] = str(self.benchmark_dir)
        data["reports_dir"] = str(self.reports_dir)
        data["manifest_path"] = str(self.manifest_path)
        data["license_summary_path"] = str(self.license_summary_path)
        data["dataset_card_path"] = str(self.dataset_card_path)
        return json.dumps(data, indent=2, sort_keys=True)
