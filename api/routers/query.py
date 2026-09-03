"""Single, mode-free RAG query endpoint."""
import json
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from api.adapters.pg_adapter import execute_rpc
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.inference.inference import generate_response
from api.services.rag.embeddings import embed_texts
from api.services.rag.reranker import get_reranker_service
from api.services.rag.graph.router import detect_graph_hops

router = APIRouter(tags=["query"])

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    collection_id: str | None = None
    use_trusted_corpus: bool = True
    bypass_cache: bool = False
    rerank: bool = True
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    graph_hops: int | None = Field(default=None, ge=0, le=2, description="Number of graph hops (0=disabled, 1=1-hop, 2=2-hop). If None, auto-detected from query intent.")

class QueryResponse(BaseModel):
    answer: str
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> QueryResponse:
    try:
        collection_filter = UUID(req.collection_id) if req.collection_id else None
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    if req.graph_hops is None:
        effective_hops = detect_graph_hops(req.query)
        graph_mode = "auto"
    else:
        effective_hops = req.graph_hops
        graph_mode = "manual"

    params: dict[str, Any] = {
        "query_text": req.query,
        "query_embedding": (await embed_texts([req.query]))[0],
        "collection_filter": collection_filter,
        "api_key_filter": UUID(_api_key.id),
    }
    if effective_hops > 0:
        params["graph_hops"] = effective_hops
        rpc_fn = "hybrid_search_trusted_with_graph_v5" if req.use_trusted_corpus else "hybrid_search_with_graph_v5"
    else:
        rpc_fn = "hybrid_search_trusted_v5" if req.use_trusted_corpus else "hybrid_search_v5"

    try:
        contexts = await execute_rpc(rpc_fn, params)
    except Exception:
        if effective_hops > 0:
            fallback_params = {k: v for k, v in params.items() if k != "graph_hops"}
            fallback_fn = "hybrid_search_trusted_v5" if req.use_trusted_corpus else "hybrid_search_v5"
            try:
                contexts = await execute_rpc(fallback_fn, fallback_params)
            except Exception as exc:
                raise HTTPException(503, "PostgreSQL retrieval is unavailable") from exc
        else:
            raise HTTPException(503, "PostgreSQL retrieval is unavailable")

    if req.rerank and contexts:
        try:
            contexts = await get_reranker_service().rerank(req.query, contexts, top_n=5)
        except Exception:
            pass

    answer = await generate_response(req.query, contexts, req.temperature)
    citations = [{"source": row.get("source_url") or row.get("document_id")} for row in contexts]
    response_metadata = {
        "graph_hops": effective_hops,
        "graph_mode": graph_mode,
    }
    return QueryResponse(answer=answer, contexts=contexts, citations=citations, metadata=response_metadata)

@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    response = await query(req, request, _api_key)
    payload = json.dumps(response.model_dump(), default=str)
    async def events():
        yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
