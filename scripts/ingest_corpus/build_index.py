"""
build_index.py — Build FAISS and BM25 indexes from chunks.json grouped by namespace.
Uses configured embedding provider.
"""

import asyncio
import argparse
import json
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add REPO_ROOT to sys.path to allow importing from api/
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT))

from api.services.rag.embeddings import get_embedding_service
from api.services.rag.filesystem_rag_store import FilesystemRAGStore

CHECKPOINTS_DIR = REPO_ROOT / "data" / "rag" / "checkpoints"


def _group_by_namespace(chunks: list[dict], namespaces: set[str] | None = None) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        meta = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        ns = meta.get("namespace", "trusted")
        if namespaces and ns not in namespaces:
            continue
        grouped[ns].append(chunk)
    return grouped


def _checkpoint_path(namespace: str) -> Path:
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    return CHECKPOINTS_DIR / f"{namespace}.index_checkpoint.json"


def _load_checkpoint(namespace: str) -> int:
    path = _checkpoint_path(namespace)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("processed_chunks", 0))
    except Exception:
        return 0


def _save_checkpoint(namespace: str, processed: int) -> None:
    path = _checkpoint_path(namespace)
    path.write_text(json.dumps({"processed_chunks": processed}), encoding="utf-8")


def _extract_retry_after_seconds(error_text: str) -> float:
    # Handles patterns like "Please retry in 45.467046468s."
    match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", error_text, re.IGNORECASE)
    if match:
        return max(1.0, float(match.group(1)))
    return 20.0


async def _index_namespace(
    store: FilesystemRAGStore,
    namespace: str,
    chunks: list[dict],
    batch_size: int,
    sleep_between_batches: float,
    max_retries_per_batch: int,
    rebuild: bool,
) -> None:
    print(f"\nIndexing namespace '{namespace}' with {len(chunks)} chunks")
    if not chunks:
        print(f"  Skipping namespace '{namespace}' (no chunks)")
        return

    paths = store._get_ns_paths(namespace)
    if rebuild and paths["dir"].exists():
        shutil.rmtree(paths["dir"])
        _save_checkpoint(namespace, 0)
    paths = store._get_ns_paths(namespace)

    embed_service = get_embedding_service()
    start_at = _load_checkpoint(namespace) if not rebuild else 0
    if start_at > 0:
        print(f"  Resuming '{namespace}' from chunk offset {start_at}")
    for i in range(start_at, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["content"] for c in batch]
        print(f"  Embedding batch {i // batch_size + 1}/{(len(chunks)-1)//batch_size + 1}...")
        embeddings = None
        last_error: Exception | None = None
        for attempt in range(1, max_retries_per_batch + 1):
            try:
                embeddings = await embed_service.create_embeddings(texts)
                break
            except Exception as exc:
                last_error = exc
                delay = _extract_retry_after_seconds(str(exc))
                print(
                    f"    Embedding failed (attempt {attempt}/{max_retries_per_batch}): {exc}\n"
                    f"    Sleeping {delay:.1f}s before retry..."
                )
                time.sleep(delay)
        if embeddings is None:
            raise RuntimeError(f"Embedding failed for namespace '{namespace}' at batch offset {i}: {last_error}")

        metadata = [
            {
                "source_name": c["source_name"],
                "source_url": c.get("source_url"),
                "chunk_order": c["chunk_order"],
                "token_count": c["token_count"],
            }
            for c in batch
        ]
        await store.ingest(namespace=namespace, chunks=texts, embeddings=embeddings, metadata=metadata)
        print(f"    Indexed {len(batch)} chunks.")
        _save_checkpoint(namespace, i + len(batch))
        if sleep_between_batches > 0:
            time.sleep(sleep_between_batches)


async def main(
    namespaces: list[str] | None = None,
    batch_size: int = 50,
    sleep_between_batches: float = 0.0,
    max_retries_per_batch: int = 6,
    rebuild: bool = False,
):
    chunks_file = REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"
    if not chunks_file.exists():
        print(f"Error: {chunks_file} not found.")
        return

    with open(chunks_file, "r", encoding="utf-8") as f:
        all_chunks = json.load(f)

    print(f"Loaded {len(all_chunks)} chunks from {chunks_file}")

    store = FilesystemRAGStore(base_path=str(REPO_ROOT / "data" / "rag"))
    requested = set(namespaces) if namespaces else None
    grouped = _group_by_namespace(all_chunks, requested)
    if not grouped:
        print("No chunks matched requested namespaces.")
        return

    for namespace, ns_chunks in sorted(grouped.items(), key=lambda x: x[0]):
        try:
            await _index_namespace(
                store=store,
                namespace=namespace,
                chunks=ns_chunks,
                batch_size=batch_size,
                sleep_between_batches=sleep_between_batches,
                max_retries_per_batch=max_retries_per_batch,
                rebuild=rebuild,
            )
        except Exception as e:
            print(f"  Error indexing namespace '{namespace}': {e}")
            raise

    print("Indexing complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build namespace indexes from trusted chunks.json")
    parser.add_argument("namespaces", nargs="*", default=None, help="Optional list of namespaces to index")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--sleep-between-batches", type=float, default=0.0)
    parser.add_argument("--max-retries-per-batch", type=int, default=6)
    parser.add_argument("--rebuild", action="store_true", help="Delete existing namespace index and start fresh")
    args = parser.parse_args()
    requested_namespaces = args.namespaces if args.namespaces else None
    asyncio.run(
        main(
            namespaces=requested_namespaces,
            batch_size=args.batch_size,
            sleep_between_batches=args.sleep_between_batches,
            max_retries_per_batch=args.max_retries_per_batch,
            rebuild=args.rebuild,
        )
    )
