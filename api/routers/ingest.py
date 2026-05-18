"""RAG ingestion endpoints for DepthAPI."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_supabase_admin
from api.logging_config import anonymize_user_id, logger
from api.services.rag.embeddings import get_embedding_service
from api.services.rag.local_collection_registry import LocalCollectionRegistry
from api.services.rag.knowledge_ingestion import IngestionWorker
from api.services.rag.rag_backend_router import get_rag_backend
from api.services.rag.filesystem_rag_store import FilesystemRAGStore
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key

router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    collection_id: str | None = None
    collection_name: str | None = None
    filename: str | None = None
    source_url: str | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] | None = None


class IngestResponse(BaseModel):
    collection_id: str
    document_id: str
    queue_id: str
    status: str


class CollectionResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: datetime | None = None


def _hash_content(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _registry() -> LocalCollectionRegistry:
    return LocalCollectionRegistry(base_path=os.getenv("RAG_DATA_PATH", "data/rag"))


async def _local_content_and_chunks(req: IngestRequest) -> tuple[str, list[str]]:
    worker = IngestionWorker(worker_id="local-ingest")
    content = await worker.fetch_content(
        {
            "source_url": req.source_url,
            "metadata": {"raw_text": req.raw_text} if req.raw_text else {},
        }
    )
    if not content:
        raise HTTPException(status_code=400, detail="Document content is empty")
    chunks = worker.chunk_text(content)
    if not chunks:
        raise HTTPException(status_code=400, detail="Document produced no valid chunks")
    return content, chunks


async def _local_ingest(
    *,
    req: IngestRequest,
    api_key: ApiKeyRecord,
) -> IngestResponse:
    registry = _registry()
    try:
        collection = registry.get_or_create_collection(
            api_key_id=api_key.id,
            collection_id=req.collection_id,
            collection_name=req.collection_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Collection not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    backend = get_rag_backend()
    if not isinstance(backend, FilesystemRAGStore):
        raise HTTPException(status_code=500, detail="Local ingestion requires filesystem RAG backend")

    content, chunks = await _local_content_and_chunks(req)
    embed_service = get_embedding_service()
    embeddings = await embed_service.create_embeddings(chunks)
    if len(embeddings) != len(chunks):
        raise HTTPException(status_code=500, detail="Embedding response count mismatch")

    filename = (req.filename or "document").strip() or "document"
    content_hash = _hash_content(req.raw_text or req.source_url or "")
    metadata: dict[str, Any] = dict(req.metadata or {})
    if req.raw_text:
        metadata.setdefault("raw_text", req.raw_text)

    document, job = registry.create_document_and_job(
        api_key_id=api_key.id,
        collection_id=str(collection["id"]),
        filename=filename,
        source_url=req.source_url,
        content_hash=content_hash,
        metadata=metadata,
        status="completed",
    )

    namespace = f"{api_key.id}/{collection['id']}"
    chunk_metadata = [
        {
            "source_name": filename,
            "source_url": req.source_url,
            "chunk_order": idx,
            "token_count": len(chunk.split()),
            "document_id": document["id"],
        }
        for idx, chunk in enumerate(chunks)
    ]
    await backend.ingest(
        namespace=namespace,
        chunks=chunks,
        embeddings=embeddings,
        metadata=chunk_metadata,
    )

    return IngestResponse(
        collection_id=str(collection["id"]),
        document_id=str(document["id"]),
        queue_id=str(job["id"]),
        status="completed",
    )


async def _get_or_create_collection(
    *,
    api_key_id: str,
    collection_id: str | None,
    collection_name: str | None,
) -> dict[str, Any]:
    supabase = get_supabase_admin()
    if not supabase:
        try:
            return _registry().get_or_create_collection(
                api_key_id=api_key_id,
                collection_id=collection_id,
                collection_name=collection_name,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Collection not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if collection_id:
        res = await (
            supabase.table("knowledge_collections")
            .select("id, name, description, created_at")
            .eq("id", collection_id)
            .eq("api_key_id", api_key_id)
            .single()
            .execute()
        )
        data = getattr(res, "data", None)
        if not isinstance(data, dict) or not data.get("id"):
            raise HTTPException(status_code=404, detail="Collection not found")
        return data

    if not collection_name:
        raise HTTPException(status_code=400, detail="collection_id or collection_name is required")

    res = await (
        supabase.table("knowledge_collections")
        .select("id, name, description, created_at")
        .eq("api_key_id", api_key_id)
        .eq("name", collection_name)
        .single()
        .execute()
    )
    data = getattr(res, "data", None)
    if isinstance(data, dict) and data.get("id"):
        return data

    created = await (
        supabase.table("knowledge_collections")
        .insert({"api_key_id": api_key_id, "name": collection_name})
        .execute()
    )
    created_data = getattr(created, "data", None)
    if not created_data or not isinstance(created_data, list):
        raise HTTPException(status_code=500, detail="Failed to create collection")
    return created_data[0]


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(
    req: IngestRequest,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> IngestResponse:
    if not req.raw_text and not req.source_url:
        raise HTTPException(status_code=400, detail="raw_text or source_url is required")

    if get_supabase_admin() is None:
        return await _local_ingest(req=req, api_key=api_key)

    collection = await _get_or_create_collection(
        api_key_id=api_key.id,
        collection_id=req.collection_id,
        collection_name=req.collection_name,
    )
    collection_id = str(collection["id"])

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")

    content_hash_source = req.raw_text or req.source_url or ""
    content_hash = _hash_content(content_hash_source)
    metadata: dict[str, Any] = dict(req.metadata or {})
    if req.raw_text:
        metadata.setdefault("raw_text", req.raw_text)

    filename = (req.filename or "document").strip() or "document"

    doc_insert = await (
        supabase.table("knowledge_documents")
        .insert(
            {
                "collection_id": collection_id,
                "filename": filename,
                "source_url": req.source_url,
                "content_hash": content_hash,
                "metadata": metadata,
            }
        )
        .execute()
    )
    doc_data = getattr(doc_insert, "data", None)
    if not doc_data or not isinstance(doc_data, list):
        logger.error("ingest_document_insert_failed", user_id_hash=anonymize_user_id(api_key.id))
        raise HTTPException(status_code=500, detail="Failed to create document")

    document_id = str(doc_data[0]["id"])

    queue_insert = await (
        supabase.table("knowledge_ingestion_queue")
        .insert(
            {
                "api_key_id": api_key.id,
                "document_id": document_id,
                "status": "queued",
            }
        )
        .execute()
    )
    queue_data = getattr(queue_insert, "data", None)
    if not queue_data or not isinstance(queue_data, list):
        raise HTTPException(status_code=500, detail="Failed to enqueue ingestion job")

    return IngestResponse(
        collection_id=collection_id,
        document_id=document_id,
        queue_id=str(queue_data[0]["id"]),
        status="queued",
    )


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> list[CollectionResponse]:
    supabase = get_supabase_admin()
    if not supabase:
        rows = _registry().list_collections(api_key.id)
        return [CollectionResponse(**row) for row in rows]

    res = await (
        supabase.table("knowledge_collections")
        .select("id, name, description, created_at")
        .eq("api_key_id", api_key.id)
        .is_("deleted_at", None)
        .order("created_at", desc=True)
        .execute()
    )
    data = getattr(res, "data", None)
    if not isinstance(data, list):
        return []
    return [CollectionResponse(**row) for row in data]


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: str,
    api_key: ApiKeyRecord = Depends(verify_api_key),
) -> dict[str, str]:
    supabase = get_supabase_admin()
    if not supabase:
        found = _registry().mark_collection_deleted(
            api_key_id=api_key.id,
            collection_id=collection_id,
            base_path=os.getenv("RAG_DATA_PATH", "data/rag"),
        )
        if not found:
            raise HTTPException(status_code=404, detail="Collection not found")
        return {"status": "deleted"}

    now = datetime.now(timezone.utc).isoformat()
    await (
        supabase.table("knowledge_collections")
        .update({"deleted_at": now})
        .eq("id", collection_id)
        .eq("api_key_id", api_key.id)
        .execute()
    )
    return {"status": "deleted"}
