"""Single, mode-free RAG query endpoint with OKF cognitive depth tuning."""
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.adapters.pg_adapter import execute_rpc, get_pool
from api.services.inference.inference import generate_response, generate_stream_response
from api.services.rag.context_processing import reorder_lost_in_the_middle
from api.services.rag.embeddings import embed_texts
from api.services.rag.graph.router import detect_graph_hops
from api.services.rag.reranker import get_reranker_service
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.wiki.vault_manager import get_vault_manager

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False

router = APIRouter(tags=["query"])

log = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    collection_id: str | None = None
    use_trusted_corpus: bool = True
    bypass_cache: bool = False
    rerank: bool = True
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    graph_hops: int | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Number of graph hops (0=disabled, 1=1-hop, 2=2-hop). If None, auto-detected from query intent or cognitive depth.",
    )
    depth: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Cognitive depth level (1-2: direct concept summaries, 3-4: scoped hybrid 1-hop, 5: deep 2-hop graph + rerank).",
    )
    save_to_wiki: bool = Field(
        default=False,
        description="Compounding Q&A loop: write synthesized insight back to Karpathy LLM-Wiki vault.",
    )


class QueryResponse(BaseModel):
    answer: str
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> QueryResponse:
    contexts, ordered_contexts, response_metadata, collection_filter, confidence = await _retrieve(req, _api_key)

    if confidence == "insufficient":
        answer = "I could not find sufficient matching documentation in your collection to answer this query reliably."
    else:
        answer = await generate_response(req.query, ordered_contexts, req.temperature)

    # Compounding Q&A loop: on save_to_wiki=True, save synthesized insight back to vault
    if req.save_to_wiki and answer and confidence != "insufficient":
        try:
            ref_concepts = [
                c.get("concept_name")
                for c in contexts
                if c.get("concept_name")
            ]
            get_vault_manager().save_qa_insight(
                query=req.query,
                answer=answer,
                collection_id=str(collection_filter) if collection_filter else None,
                referenced_concepts=ref_concepts,
            )
        except Exception as exc:
            log.warning("save_to_wiki failed: %s", exc)

    citations = [{"source": row.get("source_url") or str(row.get("document_id") or "unknown")} for row in contexts]
    return QueryResponse(answer=answer, contexts=contexts, citations=citations, metadata=response_metadata)


async def _retrieve(
    req: QueryRequest, _api_key: ApiKeyRecord
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], UUID | None, str]:
    """Shared retrieval/rerank/confidence pipeline for query + query/stream."""
    try:
        collection_filter = UUID(req.collection_id) if req.collection_id else None
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    # Determine graph hops and mode based on cognitive depth and request params
    if req.graph_hops is not None:
        effective_hops = req.graph_hops
        graph_mode = "manual"
    elif req.depth in (1, 2):
        effective_hops = 0
        graph_mode = "concept_direct"
    elif req.depth == 5:
        effective_hops = 2
        graph_mode = "auto"
    else:
        effective_hops = detect_graph_hops(req.query)
        graph_mode = "auto"

    contexts: list[dict[str, Any]] = []

    # Cognitive Depth 1-2: Direct concept summaries from knowledge_concepts (<200ms, ~95% token savings)
    if req.depth in (1, 2):
        max_concepts = 2 if req.depth == 1 else 4
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                escaped = req.query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                concept_rows = await conn.fetch(
                    """
                    SELECT c.id, c.name, c.concept_type, c.description, c.metadata
                    FROM knowledge_concepts c
                    JOIN knowledge_collections k ON k.id = c.collection_id
                    WHERE k.api_key_id = $1
                      AND ($2::uuid IS NULL OR c.collection_id = $2::uuid)
                      AND (c.name ILIKE $3 ESCAPE '\\' OR c.description ILIKE $3 ESCAPE '\\')
                    LIMIT $4
                    """,
                    UUID(_api_key.id),
                    collection_filter,
                    pattern,
                    max_concepts,
                )
                if concept_rows:
                    contexts = [
                        {
                            "content": f"### Concept: {r['name']} ({r['concept_type']})\n{r['description'] or ''}",
                            "document_id": str(r["id"]),
                            "source_url": f"concept:{r['name']}",
                            "concept_name": r["name"],
                            "score": 1.0,
                        }
                        for r in concept_rows
                    ]
        except Exception as exc:
            log.warning("Concept lookup failed, falling back to vault: %s", exc)

        # If DB had no concept hits, check local vault
        if not contexts:
            v_manager = get_vault_manager()
            v_concepts = v_manager.list_concepts()
            q_lower = req.query.lower()
            vault_matches = []
            for vc in v_concepts:
                name_lower = vc["name"].lower()
                if name_lower in q_lower or any(term in name_lower for term in q_lower.split() if len(term) > 3):
                    c_data = v_manager.read_concept(vc["slug"])
                    vault_matches.append({
                        "name": vc["name"],
                        "concept_type": vc.get("concept_type", "topic"),
                        "description": (c_data or {}).get("content", "")[:350],
                    })
                    if len(vault_matches) >= max_concepts:
                        break
            if vault_matches:
                contexts = [
                    {
                        "content": f"### Concept: {c['name']} ({c['concept_type']})\n{c['description']}",
                        "document_id": c["name"].lower(),
                        "source_url": f"vault:{c['name']}",
                        "concept_name": c["name"],
                        "score": 1.0,
                    }
                    for c in vault_matches
                ]

    # Cognitive Depth 3-5 or fallback when Depths 1-2 found no concept notes
    if not contexts:
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
            raw_contexts = await execute_rpc(rpc_fn, params)
            if req.depth in (1, 2):
                contexts = raw_contexts[:(1 if req.depth == 1 else 2)]
            else:
                contexts = raw_contexts
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

    # Reranking: Depths 1-2 bypass reranking for sub-200ms latency.
    # Depth 5 forces cross-encoder rerank; Depth 3-4 respects req.rerank.
    should_rerank = (req.depth == 5) or (req.rerank and req.depth >= 3)
    if should_rerank and contexts:
        try:
            top_n = 7 if req.depth == 5 else 5
            contexts = await get_reranker_service().rerank(req.query, contexts, top_n=top_n)
        except Exception as exc:
            log.warning("Rerank failed, using retrieval order: %s", exc)

    # Evaluate retrieval confidence (CRAG - Corrective RAG gating)
    confidence = "high"
    if not contexts:
        confidence = "insufficient"
    elif _HAS_DEPTH_ENGINE:
        try:
            eval_res = depth_engine.evaluate_confidence(contexts, False)
            confidence = eval_res.get("confidence", "high")
        except Exception:
            if any("rerank_score" in c for c in contexts):
                max_score = max(c.get("rerank_score", -999.0) for c in contexts)
                if max_score < -2.0:
                    confidence = "low"
                elif max_score < 0.0:
                    confidence = "medium"
            elif any("score" in c for c in contexts):
                max_score = max(c.get("score", 0.0) for c in contexts)
                if max_score < 0.012:
                    confidence = "low"
                elif max_score < 0.020:
                    confidence = "medium"
    elif any("rerank_score" in c for c in contexts):
        max_score = max(c.get("rerank_score", -999.0) for c in contexts)
        if max_score < -2.0:
            confidence = "low"
        elif max_score < 0.0:
            confidence = "medium"
    elif any("score" in c for c in contexts):
        max_score = max(c.get("score", 0.0) for c in contexts)
        if max_score < 0.012:
            confidence = "low"
        elif max_score < 0.020:
            confidence = "medium"

    # Apply Lost-in-the-Middle U-shaped ordering before prompt synthesis
    ordered_contexts = reorder_lost_in_the_middle(contexts)

    response_metadata = {
        "depth": req.depth,
        "cognitive_depth": req.depth,
        "saved_to_wiki": req.save_to_wiki,
        "graph_hops": effective_hops,
        "graph_mode": graph_mode,
        "confidence": confidence,
        "prompt_ordering": "lost_in_the_middle",
    }
    return contexts, ordered_contexts, response_metadata, collection_filter, confidence


@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    contexts, ordered_contexts, response_metadata, collection_filter, confidence = await _retrieve(req, _api_key)
    citations = [{"source": row.get("source_url") or str(row.get("document_id") or "unknown")} for row in contexts]

    async def events():
        # Heartbeat comment keeps proxies from closing idle streams.
        yield ": stream start\n\n"
        if confidence == "insufficient":
            answer = "I could not find sufficient matching documentation in your collection to answer this query reliably."
            yield f"data: {json.dumps({'delta': answer}, default=str)}\n\n"
            final = {"answer": answer, "contexts": contexts, "citations": citations, "metadata": response_metadata}
            yield f"data: {json.dumps(final, default=str)}\n\n"
        else:
            parts: list[str] = []
            async for token in generate_stream_response(req.query, ordered_contexts, req.temperature):
                parts.append(token)
                yield f"data: {json.dumps({'delta': token}, default=str)}\n\n"
                if await request.is_disconnected():
                    break
            answer = "".join(parts)
            if req.save_to_wiki and answer:
                try:
                    ref_concepts = [c.get("concept_name") for c in contexts if c.get("concept_name")]
                    get_vault_manager().save_qa_insight(
                        query=req.query,
                        answer=answer,
                        collection_id=str(collection_filter) if collection_filter else None,
                        referenced_concepts=ref_concepts,
                    )
                except Exception as exc:
                    log.warning("save_to_wiki failed: %s", exc)
            final = {"answer": answer, "contexts": contexts, "citations": citations, "metadata": response_metadata}
            yield f"data: {json.dumps(final, default=str)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
