from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.research_corpus import PipelineConfig, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the research-grade DepthAPI corpus")
    parser.add_argument(
        "--stage",
        nargs="+",
        default=["crawl", "chunk", "dedup", "validate", "export"],
        choices=["crawl", "normalize", "dedup", "chunk", "validate", "export", "publish"],
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit exported Supabase rows for local iteration")
    parser.add_argument("--publish", action="store_true", help="Publish the rebuilt dataset to Hugging Face")
    parser.add_argument("--hf-repo-id", default="sanjeevafk/depthapi_technical_corpus")
    parser.add_argument("--hf-split", default="train")
    parser.add_argument(
        "--commit-message",
        default="Refresh dataset from local Supabase research corpus pipeline",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    config = PipelineConfig.default(repo_root)
    config.publish.repo_id = args.hf_repo_id
    config.publish.split = args.hf_split
    config.publish.commit_message = args.commit_message
    stages = list(args.stage)
    if args.publish and "publish" not in stages:
        stages.append("publish")
    run_pipeline(config=config, stages=stages, limit=args.limit)


if __name__ == "__main__":
    main()
