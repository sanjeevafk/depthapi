"""Document ingestion into local PostgreSQL."""
import json
from typing import Any
from uuid import uuid4
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.adapters.pg_adapter import get_pool
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

@router.post("/ingest", response_model=IngestResponse)
async def ingest(req: IngestRequest, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> IngestResponse:
    if not req.raw_text:
        raise HTTPException(400, "raw_text is required")
    collection_id = UUID(req.collection_id) if req.collection_id else uuid4()
    document_id, queue_id = uuid4(), uuid4()
    try:
        async with get_pool().acquire() as conn:
            encoded_metadata = json.dumps(req.metadata or {})
            await conn.execute("INSERT INTO knowledge_collections (id, name, metadata) VALUES ($1, $2, $3::jsonb) ON CONFLICT (id) DO NOTHING", collection_id, req.collection_name or "default", encoded_metadata)
            await conn.execute("INSERT INTO knowledge_documents (id, collection_id, filename, source_url, content, metadata) VALUES ($1, $2, $3, $4, $5, $6::jsonb)", document_id, collection_id, req.filename, req.source_url, req.raw_text, encoded_metadata)
            await conn.execute("INSERT INTO knowledge_chunks (document_id, chunk_order, content, token_count, metadata) VALUES ($1, 0, $2, $3, $4::jsonb)", document_id, req.raw_text, len(req.raw_text.split()), encoded_metadata)
            await conn.execute("INSERT INTO knowledge_ingestion_queue (id, document_id, status) VALUES ($1, $2, 'queued')", queue_id, document_id)
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL is unavailable") from exc
    return IngestResponse(collection_id=str(collection_id), document_id=str(document_id), queue_id=str(queue_id), status="queued")
