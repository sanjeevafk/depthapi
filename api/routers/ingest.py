"""Document ingestion into local PostgreSQL."""
import json
import hashlib
from typing import Any
from uuid import uuid4
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from api.adapters.pg_adapter import get_pool
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.rag.embeddings import embed_texts

router = APIRouter(tags=["ingest"])
class IngestRequest(BaseModel):
    collection_id: str | None = None
    collection_name: str | None = None
    filename: str | None = None
    source_url: str | None = None
    raw_text: str | None = Field(default=None, max_length=100000)
    metadata: dict[str, Any] | None = None
class IngestResponse(BaseModel):
    collection_id: str
    document_id: str
    queue_id: str
    status: str

@router.post("/ingest", response_model=IngestResponse)
async def ingest(req: IngestRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> IngestResponse:
    if not req.raw_text:
        raise HTTPException(400, "raw_text is required")
    try:
        collection_id = UUID(req.collection_id) if req.collection_id else uuid4()
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc
    document_id, queue_id = uuid4(), uuid4()
    try:
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                encoded_metadata = json.dumps(req.metadata or {})
                owner_id = UUID(_api_key.id)
                collection = await conn.fetchrow(
                    """INSERT INTO knowledge_collections (id, api_key_id, name, metadata)
                       VALUES ($1, $2, $3, $4::jsonb)
                       ON CONFLICT (id) DO UPDATE SET name = knowledge_collections.name
                       WHERE knowledge_collections.api_key_id = EXCLUDED.api_key_id
                       RETURNING id""",
                    collection_id,
                    owner_id,
                    req.collection_name or "default",
                    encoded_metadata,
                )
                if collection is None:
                    raise HTTPException(404, "Collection not found")
                content_hash = hashlib.sha256(req.raw_text.encode("utf-8")).hexdigest()
                embedding = (await embed_texts([req.raw_text]))[0]
                await conn.execute("INSERT INTO knowledge_documents (id, collection_id, filename, source_url, content, content_hash, metadata) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)", document_id, collection_id, req.filename, req.source_url, req.raw_text, content_hash, encoded_metadata)
                await conn.execute("INSERT INTO knowledge_chunks (document_id, chunk_order, content, token_count, embedding, metadata) VALUES ($1, 0, $2, $3, $4::vector, $5::jsonb)", document_id, req.raw_text, len(req.raw_text.split()), embedding, encoded_metadata)
                await conn.execute("INSERT INTO knowledge_ingestion_queue (id, document_id, status) VALUES ($1, $2, 'complete')", queue_id, document_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL is unavailable") from exc
    return IngestResponse(collection_id=str(collection_id), document_id=str(document_id), queue_id=str(queue_id), status="complete")
