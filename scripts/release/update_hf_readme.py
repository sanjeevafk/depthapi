import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from huggingface_hub import CommitOperationAdd, HfApi
from scripts.ingest_corpus.research_corpus.dataset_card import write_dataset_card


def main():
    load_dotenv(".env.local", override=True)
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Update HF Dataset README.md")
    parser.add_argument("--repo-id", default="sanjeevafk/depthapi_technical_corpus", help="Target HuggingFace repo ID")
    parser.add_argument("--commit-message", default="Update dataset card (README.md)", help="Commit message")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN not set in environment.")

    api = HfApi(token=token)

    repo_root = Path(__file__).resolve().parents[2]
    readme_path = repo_root / "datasets" / "depthapi_technical_corpus" / "README.md"
    readme_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate the dataset card
    write_dataset_card(readme_path)

    logging.info(f"Publishing README.md to {args.repo_id}...")
    api.create_commit(
        repo_id=args.repo_id,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(readme_path))
        ],
        commit_message=args.commit_message,
    )
    logging.info(f"Successfully updated README.md on {args.repo_id} with commit message: '{args.commit_message}'")

if __name__ == "__main__":
    main()
