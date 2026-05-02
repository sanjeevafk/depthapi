"""
build_index.py — Build FAISS and BM25 index from chunks.json.
Uses Gemini text-embedding-004.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add REPO_ROOT to sys.path to allow importing from api/
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT))

from api.services.embeddings import get_embedding_service
from api.services.filesystem_rag_store import FilesystemRAGStore

async def main():
    chunks_file = REPO_ROOT / "data" / "rag" / "trusted" / "chunks.json"
    if not chunks_file.exists():
        print(f"Error: {chunks_file} not found.")
        return

    with open(chunks_file, "r", encoding="utf-8") as f:
        all_chunks = json.load(f)

    print(f"Loaded {len(all_chunks)} chunks from {chunks_file}")
    
    # Filesystem store
    store = FilesystemRAGStore(base_path=str(REPO_ROOT / "data" / "rag"))
    namespace = "trusted"
    
    # Check what's already in the manifest
    paths = store._get_ns_paths(namespace)
    processed_count = 0
    if paths["manifest"].exists():
        with open(paths["manifest"], "r") as f:
            manifest = json.load(f)
            processed_count = manifest.get("total_chunks", 0)
    
    new_chunks = all_chunks[processed_count:]
    if not new_chunks:
        print("Everything already indexed.")
        return

    print(f"Processing {len(new_chunks)} new chunks...")
    
    embed_service = get_embedding_service()
    
    # Process in batches
    batch_size = 50
    for i in range(0, len(new_chunks), batch_size):
        batch = new_chunks[i:i+batch_size]
        texts = [c["content"] for c in batch]
        
        print(f"  Embedding batch {i//batch_size + 1}/{(len(new_chunks)-1)//batch_size + 1}...")
        try:
            embeddings = await embed_service.create_embeddings(texts)
            
            # Prepare for ingestion
            chunks_to_ingest = [c["content"] for c in batch]
            metadata = [
                {
                    "source_name": c["source_name"],
                    "source_url": c.get("source_url"),
                    "chunk_order": c["chunk_order"],
                    "token_count": c["token_count"]
                }
                for c in batch
            ]
            
            await store.ingest(
                namespace=namespace,
                chunks=chunks_to_ingest,
                embeddings=embeddings,
                metadata=metadata
            )
            print(f"    Indexed {len(batch)} chunks.")
        except Exception as e:
            print(f"    Error processing batch: {e}")
            # Continue to next batch? or stop?
            # For MVP, we stop.
            break

    print("Indexing complete.")

if __name__ == "__main__":
    asyncio.run(main())
