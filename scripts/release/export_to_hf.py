import os
from typing import Any, cast

import pandas as pd  # type: ignore[reportMissingModuleSource]
from datasets import Dataset, load_dataset  # type: ignore[reportMissingModuleSource]
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv

load_dotenv(".env.local", override=True)
load_dotenv()  # Fallback for any missing keys

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing Supabase credentials in .env")
    exit(1)

supabase: Client = create_client(
    SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(postgrest_client_timeout=120.0)
)



def fetch_all_chunks() -> list[dict[str, Any]]:
    print(
        "Fetching chunks from Supabase using keyset pagination. This is fast and avoids timeouts..."
    )
    all_chunks: list[dict[str, Any]] = []
    limit = 1000
    last_id: int | None = None

    while True:
        try:
            # We select ONLY the required columns. Excluding the massive 'embedding' vector (1536 floats)
            # and 'fts_tokens' (tsvector) reduces the payload size by 99%, preventing PostgREST timeouts.
            query = (
                supabase.table("knowledge_chunks")
                .select("id,document_id,content,chunk_order,metadata")
                .order("id")
                .limit(limit)
            )
            if last_id is not None:
                query = query.gt("id", last_id)

            response = query.execute()
            data = cast(list[dict[str, Any]], response.data or [])

            if not data:
                break

            all_chunks.extend(data)
            last_id = cast(int, data[-1]["id"])

            if len(all_chunks) % 10000 == 0:
                print(f"Fetched {len(all_chunks)} chunks...")

            if len(data) < limit:
                break
        except Exception as e:
            print(f"Error fetching after id {last_id}: {e}")
            break

    return all_chunks


def main():
    chunks = fetch_all_chunks()

    if not chunks:
        print(
            "No chunks found. Check your table name (knowledge_chunks) or credentials."
        )
        return

    HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not HF_TOKEN:
        print("Missing Hugging Face token in HF_TOKEN or HUGGINGFACE_TOKEN env var")
        return
    from huggingface_hub import HfApi
    api = HfApi(token=HF_TOKEN)
    try:
        username = api.whoami()["name"]
        print(f"Authenticated as HF user: {username}")
    except Exception as e:
        print(f"Error authenticating with Hugging Face token: {e}")
        return

    REPO_ID = os.environ.get("HF_REPO_ID") or f"{username}/depthapi_technical_corpus"

    existing_chunk_ids: set[str] = set()
    try:
        existing_dataset = load_dataset(
            REPO_ID, split="train", streaming=True, token=HF_TOKEN
        )
        for row in existing_dataset:
            chunk_id = row.get("chunk_id")
            if chunk_id is not None:
                existing_chunk_ids.add(str(chunk_id))
        if existing_chunk_ids:
            print(f"Found {len(existing_chunk_ids)} existing chunks in HF repo.")
    except Exception as e:
        print(f"Warning: could not load existing dataset for dedup: {e}")

    hf_rows = []
    skipped = 0
    for c in chunks:
        chunk_id = c.get("id")
        if chunk_id is not None and str(chunk_id) in existing_chunk_ids:
            skipped += 1
            continue

        meta = c.get("metadata") or {}

        # Robustly extract metadata fields from either flat columns or nested JSONB metadata
        source_name = c.get("source_name") or meta.get("source_name") or ""
        source_url = c.get("source_url") or meta.get("source_url") or ""
        namespace = c.get("namespace") or meta.get("namespace") or "trusted"

        tags = c.get("tags") or meta.get("tags") or []
        if isinstance(tags, list):
            tags = ", ".join(tags)

        content = c.get("content") or ""

        hf_rows.append(
            {
                "chunk_id": chunk_id,
                "doc_id": c.get("document_id") or c.get("doc_id") or "",
                "namespace": namespace,
                "source_name": source_name,
                "source_url": source_url,
                "raw_text": content,
                "cleaned_text": content,
                "tags": tags,
                "chunk_order": c.get("chunk_order", 0),
            }
        )

    if skipped:
        print(f"Skipped {skipped} chunks already present in HF repo.")

    print("Converting to Hugging Face Dataset...")
    df = pd.DataFrame(hf_rows)
    hf_dataset = Dataset.from_pandas(df)

    print(f"Pushing to Hugging Face Hub at {REPO_ID}...")
    hf_dataset.push_to_hub(
        REPO_ID,
        private=False,
        token=HF_TOKEN,
        commit_message="Initial release of technical documentation corpus",
    )
    print("Pushed dataset successfully! Creating dataset card...")

    # Create dataset card (README.md)
    readme_content = f"""
---
language:
- en
license: mit
tags:
- rag
- technical-docs
- programming
- cs
- programming-books
size_categories:
- 100K<n<1M
---

# DepthAPI Technical Corpus

This dataset contains a comprehensive technical corpus optimized for Retrieval-Augmented Generation (RAG). It includes ~240k semantic chunks of high-quality, trusted technical documentation and books.

## Dataset Contents

- MDN Web Docs
- Kubernetes Documentation
- CPython Documentation
- Node.js API Docs
- React.dev Content
- Various Notes for Professionals (Java, Python, SQL, JS, TS, etc.)
- Algorithms and System Design Primers



## Schema

- `chunk_id`: Unique identifier for the chunk.
- `doc_id`: Identifier for the source document.
- `namespace`: Category/namespace (e.g., `trusted`).
- `source_name`: Human-readable name of the source (e.g., "CPython Docs").
- `source_url`: URL or source locator.
- `raw_text`: The raw text content of the chunk.
- `cleaned_text`: The cleaned, parsed markdown/text content ready for embedding.
- `tags`: Comma-separated list of tags (e.g., "python, stdlib, P0").
- `chunk_order`: Integer representing the sequential order of the chunk in the document.

## Intended Use

This dataset is ideal for training and evaluating Large Language Models (LLMs) on technical coding tasks, as well as serving as a high-quality knowledge base for hybrid search / RAG pipelines.
"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(readme_content.strip())
        temp_path = f.name

    api.upload_file(
        path_or_fileobj=temp_path,
        path_in_repo="README.md",
        repo_id=REPO_ID,
        repo_type="dataset",
        commit_message="Add dataset card",
    )
    os.unlink(temp_path)

    print("Dataset card uploaded successfully!")


if __name__ == "__main__":
    main()
