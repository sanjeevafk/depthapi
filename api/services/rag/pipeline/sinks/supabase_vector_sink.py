"""
supabase_vector_sink.py — Sink plugin: Write chunks directly to Supabase.
"""

import asyncio
import hashlib
import logging
from typing import Any

from api.auth import get_supabase_admin
from api.services.rag.pipeline.interfaces import BaseSink
from api.services.rag.pipeline.models import Chunk

log = logging.getLogger(__name__)

_SINK_NAME = "SupabaseVectorSink"


def _sha256_16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class SupabaseVectorSink(BaseSink):
    """
    Sink: Upsert chunks to a Supabase PostgreSQL table with collection and document management.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._batch_size = int(cfg.get("batch_size", 100))
        self._api_key_id = cfg.get("api_key_id")
        self._collection_id_cache = {}
        self._document_id_cache = {}

    @property
    def name(self) -> str:
        return _SINK_NAME

    def validate_chunk(self, chunk: Chunk) -> bool:
        if not chunk.content or len(chunk.content.strip()) < 10:
            return False
        if chunk.token_count <= 0:
            return False
        if not chunk.content_hash:
            return False
        return True

    def write(self, chunks: list[Chunk]) -> int:
        """Write chunks to Supabase, executed synchronously."""
        if not chunks:
            return 0
            
        valid_chunks = [c for c in chunks if self.validate_chunk(c)]
        if not valid_chunks:
            return 0

        # We need an event loop since Supabase adapter uses httpx.AsyncClient
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError("Cannot run asyncio.run() inside a running loop.")
        except RuntimeError:
            pass

        return asyncio.run(self._write_async(valid_chunks))

    async def _write_async(self, chunks: list[Chunk]) -> int:
        supabase = get_supabase_admin()
        if not supabase:
            raise RuntimeError("Supabase admin client unavailable. Ensure SUPABASE_URL and SUPABASE_SECRET_KEY are set.")

        # Resolve api key id
        api_key_id = self._api_key_id
        if not api_key_id:
            # fetch the first api key if not provided
            res = await supabase.table("api_keys").select("id").limit(1).execute()
            if res.error or not res.data:
                raise RuntimeError(f"Could not fetch a default api_key_id: {res.error}")
            api_key_id = res.data[0]["id"]

        # Assume all chunks in this batch belong to the same document
        sample = chunks[0]
        collection_name = sample.dataset_namespace or "Default Collection"
        filename = sample.source_url or sample.source_name or "unknown"
        language = sample.metadata.get("language", "en")
        
        # 1. Get or Create Collection
        col_key = f"{api_key_id}:{collection_name}"
        if col_key not in self._collection_id_cache:
            existing = await supabase.table("knowledge_collections").select("id").eq("api_key_id", api_key_id).eq("name", collection_name).limit(1).execute()
            if existing.data:
                self._collection_id_cache[col_key] = existing.data[0]["id"]
            else:
                inserted = await supabase.table("knowledge_collections").insert({
                    "api_key_id": api_key_id,
                    "name": collection_name,
                    "metadata": {"source": "SupabaseVectorSink", "version": sample.dataset_version}
                }).execute()
                if inserted.error or not inserted.data:
                    raise RuntimeError(f"Failed to create collection: {inserted.error}")
                self._collection_id_cache[col_key] = inserted.data[0]["id"]
                
        collection_id = self._collection_id_cache[col_key]
        
        # 2. Get or Create Document
        doc_hash = _sha256_16(f"{collection_id}:{filename}")
        doc_key = f"{collection_id}:{doc_hash}"
        if doc_key not in self._document_id_cache:
            doc_meta = {
                "namespace": sample.dataset_namespace,
                "source_url": sample.source_url,
                "dataset_version": sample.dataset_version,
            }
            # Upsert document
            doc_payload = {
                "collection_id": collection_id,
                "filename": filename,
                "source_url": sample.source_url,
                "content_hash": doc_hash,
                "language_config": "english" if language == "en" else "simple",
                "metadata": doc_meta,
            }
            upserted = await supabase.table("knowledge_documents").upsert(doc_payload, on_conflict="collection_id,content_hash").execute()
            if upserted.error:
                raise RuntimeError(f"Failed to upsert document: {upserted.error}")
                
            doc_req = await supabase.table("knowledge_documents").select("id").eq("collection_id", collection_id).eq("content_hash", doc_hash).limit(1).execute()
            if doc_req.error or not doc_req.data:
                raise RuntimeError(f"Failed to fetch document id: {doc_req.error}")
            self._document_id_cache[doc_key] = doc_req.data[0]["id"]
            
        document_id = self._document_id_cache[doc_key]
        
        # 3. Upsert Chunks
        rows = []
        for ch in chunks:
            meta = dict(ch.metadata or {})
            meta.update({
                "schema_version": ch.schema_version,
                "chunker_version": ch.chunker_version,
                "original_chunk_id": ch.chunk_id,
                "source_url": ch.source_url,
            })
            
            rows.append({
                "document_id": document_id,
                "content": ch.content,
                "content_hash": ch.content_hash,
                "token_count": ch.token_count,
                "chunk_order": ch.chunk_order,
                "metadata": meta,
            })
            
        # Guarantee unique chunk_order within document
        rows.sort(key=lambda r: (r["chunk_order"], r["content_hash"]))
        for idx, row in enumerate(rows):
            row["chunk_order"] = idx

        written = 0
        for i in range(0, len(rows), self._batch_size):
            batch = rows[i:i + self._batch_size]
            result = await supabase.table("knowledge_chunks").upsert(batch, on_conflict="document_id,chunk_order").execute()
            if result.error:
                log.error(f"Failed to upsert chunks batch: {result.error}")
            else:
                written += len(batch)
                
        return written
