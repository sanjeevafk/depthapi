from __future__ import annotations

from pathlib import Path

from .benchmarks import build_benchmark_assets, run_benchmark_harness
from .chunker import DeterministicChunker
from .config import PipelineConfig
from .dataset_card import write_dataset_card
from .deduplication import deduplicate_chunks
from .exporter import export_postgres_documents
from .governance import build_governance_artifacts
from .io_utils import export_parquet, read_jsonl, write_json
from .publisher import publish_to_hugging_face
from .validation import validate_chunks


def run_pipeline(config: PipelineConfig, stages: list[str] | None = None, limit: int | None = None) -> dict:
    config.ensure_dirs()
    enabled = stages or ["crawl", "chunk", "dedup", "validate", "export"]
    summary: dict[str, object] = {"stages": enabled}
    stage_status: dict[str, str] = {}

    if "crawl" in enabled:
        summary["crawl"] = export_postgres_documents(config.raw_export_path, limit=limit)
        stage_status["crawl"] = "completed"
    else:
        stage_status["crawl"] = "skipped"

    documents = read_jsonl(config.raw_export_path)
    summary["governance"] = build_governance_artifacts(
        documents,
        config.license_summary_path,
        config.manifest_path,
    )
    stage_status["governance"] = "completed"

    if "chunk" in enabled:
        chunker = DeterministicChunker(config.chunking)
        chunks, chunking_report = chunker.chunk_documents(documents)
        export_parquet(config.normalized_path, chunks)
        write_json(config.reports_dir / "chunking_report.json", chunking_report)
        summary["chunking"] = chunking_report
        stage_status["chunk"] = "completed"
    else:
        raise RuntimeError("chunk stage is required")
    chunk_rows = []
    try:
        import pandas as pd  # type: ignore[reportMissingImports]

        chunk_rows = pd.read_parquet(config.normalized_path).to_dict(orient="records")
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Unable to read normalized parquet: {exc}") from exc

    if "dedup" in enabled:
        kept, removed, stats = deduplicate_chunks(chunk_rows, config.dedup)
        export_parquet(config.deduped_path, kept)
        export_parquet(config.duplicate_path, removed)
        write_json(config.reports_dir / "dedup_stats.json", stats)
        summary["dedup"] = stats
        stage_status["dedup"] = "completed"
    else:
        kept = chunk_rows
        stage_status["dedup"] = "skipped"

    if "validate" in enabled:
        validation_report = validate_chunks(kept, config.validation)
        write_json(config.reports_dir / "validation_report.json", validation_report)
        summary["validation"] = validation_report
        stage_status["validate"] = "completed"
    else:
        stage_status["validate"] = "skipped"

    benchmark_stats = build_benchmark_assets(kept, config.benchmark_dir)
    benchmark_results = run_benchmark_harness(kept, config.benchmark_dir, config.benchmark)
    summary["benchmark_assets"] = benchmark_stats
    summary["benchmark_results"] = benchmark_results
    stage_status["benchmark"] = "completed"

    write_dataset_card(config.dataset_card_path)
    stage_status["export"] = "completed"

    if "publish" in enabled:
        summary["publish"] = publish_to_hugging_face(
            repo_id=config.publish.repo_id,
            split=config.publish.split,
            parquet_path=config.deduped_path,
            duplicate_path=config.duplicate_path,
            dataset_card_path=config.dataset_card_path,
            manifest_path=config.manifest_path,
            license_summary_path=config.license_summary_path,
            benchmark_dir=config.benchmark_dir,
            commit_message=config.publish.commit_message,
            private=config.publish.private,
        )
        stage_status["publish"] = "completed"
    else:
        stage_status["publish"] = "skipped"

    summary["status"] = {
        "state": "success",
        "stage_status": stage_status,
        "documents_exported": len(documents),
        "chunks_ready_for_publish": len(kept),
        "published_repo": config.publish.repo_id if "publish" in enabled else None,
    }
    write_json(config.reports_dir / "pipeline_summary.json", summary)
    return summary
