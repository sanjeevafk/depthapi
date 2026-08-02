"""Single, mode-free RAG query endpoint."""
import json
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from api.adapters.pg_adapter import execute_rpc
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.inference.inference import generate_response

router = APIRouter(tags=["query"])

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    collection_id: str | None = None
    use_trusted_corpus: bool = True
    bypass_cache: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

class QueryResponse(BaseModel):
    answer: str
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> QueryResponse:
    try:
        collection_filter = UUID(req.collection_id) if req.collection_id else None
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc
    params = {"query_text": req.query, "collection_filter": collection_filter}
    try:
        contexts = await execute_rpc("hybrid_search_trusted_v5" if req.use_trusted_corpus else "hybrid_search_v5", params)
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL retrieval is unavailable") from exc
    answer = generate_response(req.query, contexts, req.temperature)
    citations = [{"source": row.get("source_url") or row.get("document_id")} for row in contexts]
    return QueryResponse(answer=answer, contexts=contexts, citations=citations)

@router.post("/query/stream")
async def query_stream(req: QueryRequest, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    response = await query(req, _api_key)
    payload = json.dumps(response.model_dump(), default=str)
    async def events():
        yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
