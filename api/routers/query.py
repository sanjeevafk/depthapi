"""Single, mode-free RAG query endpoint with OKF cognitive depth tuning."""
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.adapters.pg_adapter import execute_rpc, get_pool
from api.config import get_settings
from api.services import cache as query_cache
from api.services.inference.inference import (
    generate_response,
    generate_stream_response,
    has_citation_markers,
)
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


def _confidence_from_scores(contexts: list[dict[str, Any]]) -> str:
    """Map retrieval scores to a confidence band.

    Dense cosine similarity (~0.3-1.0) and hybrid RRF-fused scores (<0.05)
    live on different scales, so each uses its own thresholds.
    """
    if any(c.get("match_source") == "dense" for c in contexts):
        top = max((c.get("score") or 0.0) for c in contexts)
        if top < 0.55:
            return "low"
        if top < 0.7:
            return "medium"
        return "high"
    top = max(c.get("score", 0.0) for c in contexts)
    if top < 0.012:
        return "low"
    if top < 0.020:
        return "medium"
    return "high"


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
        description="Cognitive depth level (1-2: direct concept summaries, 3-4: dense-first hybrid, 5: deep retrieval + forced rerank; graph hops only on detected intent or manual override).",
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
    settings = get_settings()
    model = settings.llm_model
    # save_to_wiki has a side effect, so it never reads or populates the cache.
    use_cache = not req.bypass_cache and not req.save_to_wiki
    ckey = query_cache.cache_key(
        _api_key.id, req.query, req.collection_id, req.depth, req.temperature,
        req.rerank, req.use_trusted_corpus, req.graph_hops, model,
    )
    if settings.quota_enabled:
        query_cache.check_quota(_api_key.id, _api_key.is_pro, query_cache.count_tokens(req.query, model))
    if use_cache:
        hit = query_cache.get_cached(ckey)
        if hit is not None:
            try:
                cached_resp = QueryResponse(**hit)
            except Exception as exc:
                log.warning("Cached payload invalid, treating as miss: %s", exc)
                cached_resp = None
            if cached_resp is not None:
                if settings.quota_enabled:
                    query_cache.consume_quota(_api_key.id, query_cache.count_tokens(req.query, model))
                cached_resp.cached = True
                return cached_resp

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
    response_metadata["citations_enforced"] = (
        not contexts or confidence == "insufficient" or has_citation_markers(answer)
    )
    resp = QueryResponse(answer=answer, contexts=contexts, citations=citations, metadata=response_metadata)
    if settings.quota_enabled:
        query_cache.consume_quota(
            _api_key.id,
            query_cache.count_tokens(req.query, model) + query_cache.count_tokens(answer, model),
        )
    if use_cache:
        query_cache.put_cached(ckey, resp.model_dump())
    return resp


async def _retrieve(
    req: QueryRequest, _api_key: ApiKeyRecord
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], UUID | None, str]:
    """Shared retrieval/rerank/confidence pipeline for query + query/stream."""
    try:
        collection_filter = UUID(req.collection_id) if req.collection_id else None
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    # Determine graph hops: explicit override wins; depths 1-2 never traverse;
    # otherwise follow the intent router. Graph expansion is off by default and
    # only engages on detected lineage/dependency intent or manual override.
    if req.graph_hops is not None:
        effective_hops = req.graph_hops
        graph_mode = "manual"
    elif req.depth in (1, 2):
        effective_hops = 0
        graph_mode = "concept_direct"
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

    # Cognitive Depth 3-5 or fallback when Depths 1-2 found no concept notes.
    # Dense-first: a strong dense hit is used directly; the hybrid (dense +
    # lexical RRF) functions serve as the backstop when dense coverage is weak.
    retrieval_mode = "concept" if contexts else "dense"
    if not contexts:
        settings = get_settings()
        query_embedding = (await embed_texts([req.query]))[0]
        base_params: dict[str, Any] = {
            "collection_filter": collection_filter,
            "api_key_filter": UUID(_api_key.id),
        }
        dense_contexts: list[dict[str, Any]] = []
        if effective_hops == 0:
            try:
                dense_contexts = await execute_rpc(
                    "dense_search_v5", {"query_embedding": query_embedding, **base_params}
                )
                for row in dense_contexts:
                    row["match_source"] = "dense"
            except Exception as exc:
                log.warning("Dense search failed, falling back to hybrid: %s", exc)
                dense_contexts = []

        dense_top = max((c.get("score", 0.0) or 0.0) for c in dense_contexts) if dense_contexts else 0.0
        if len(dense_contexts) >= settings.dense_hit_min_results and dense_top >= settings.dense_hit_min_similarity:
            contexts = dense_contexts[:10]
            retrieval_mode = "dense"
        else:
            retrieval_mode = "hybrid"
            params: dict[str, Any] = {
                "query_text": req.query,
                "query_embedding": query_embedding,
                **base_params,
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

    # Reranking: depths 1-2 skip for sub-200ms latency; depth 5 forces it.
    # At depths 3-4 the cross-encoder runs as a rescue for marginal hits, but
    # is skipped on clear dense matches (cosine similarity at/above the
    # threshold) where reordering adds latency without measurable gain.
    def _clear_dense_hit(rows: list[dict[str, Any]]) -> bool:
        threshold = get_settings().rerank_skip_similarity
        return any(
            (row.get("match_source") == "dense") and ((row.get("score") or 0.0) >= threshold)
            for row in rows
        )

    rerank_applied = False
    should_rerank = (req.depth == 5) or (req.rerank and req.depth >= 3 and not _clear_dense_hit(contexts))
    if should_rerank and contexts:
        try:
            top_n = 7 if req.depth == 5 else 5
            contexts = await get_reranker_service().rerank(req.query, contexts, top_n=top_n)
            rerank_applied = True
        except Exception as exc:
            log.warning("Rerank failed, using retrieval order: %s", exc)

    # Evaluate retrieval confidence (CRAG - Corrective RAG gating).
    # Score scales differ by source: cosine similarity (~0.3-1.0) for dense
    # hits versus small RRF-fused scores for hybrid/graph retrieval.
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
                confidence = _confidence_from_scores(contexts)
    elif any("rerank_score" in c for c in contexts):
        max_score = max(c.get("rerank_score", -999.0) for c in contexts)
        if max_score < -2.0:
            confidence = "low"
        elif max_score < 0.0:
            confidence = "medium"
    elif any("score" in c for c in contexts):
        confidence = _confidence_from_scores(contexts)

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
        "retrieval_mode": retrieval_mode,
        "rerank_applied": rerank_applied,
    }
    return contexts, ordered_contexts, response_metadata, collection_filter, confidence


@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    settings = get_settings()
    model = settings.llm_model
    use_cache = not req.bypass_cache and not req.save_to_wiki
    ckey = query_cache.cache_key(
        _api_key.id, req.query, req.collection_id, req.depth, req.temperature,
        req.rerank, req.use_trusted_corpus, req.graph_hops, model,
    )
    if settings.quota_enabled:
        query_cache.check_quota(_api_key.id, _api_key.is_pro, query_cache.count_tokens(req.query, model))
    replay: QueryResponse | None = None
    if use_cache:
        hit = query_cache.get_cached(ckey)
        if hit is not None:
            try:
                replay = QueryResponse(**hit)
                replay.cached = True
            except Exception as exc:
                log.warning("Cached payload invalid, treating as miss: %s", exc)
                replay = None
        if replay is not None:
            if settings.quota_enabled:
                query_cache.consume_quota(_api_key.id, query_cache.count_tokens(req.query, model))

            async def replay_events():
                yield ": stream start\n\n"
                yield f"data: {json.dumps({'delta': replay.answer}, default=str)}\n\n"
                final = {
                    "answer": replay.answer,
                    "contexts": replay.contexts,
                    "citations": replay.citations,
                    "cached": True,
                    "metadata": replay.metadata,
                }
                yield f"data: {json.dumps(final, default=str)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                replay_events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    contexts, ordered_contexts, response_metadata, collection_filter, confidence = await _retrieve(req, _api_key)
    citations = [{"source": row.get("source_url") or str(row.get("document_id") or "unknown")} for row in contexts]

    async def events():
        # Heartbeat comment keeps proxies from closing idle streams.
        yield ": stream start\n\n"
        if confidence == "insufficient":
            answer = "I could not find sufficient matching documentation in your collection to answer this query reliably."
            response_metadata["citations_enforced"] = True
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
            # No retry on the streaming path (it would double latency and repeat
            # deltas); enforcement retry lives on POST /api/query. Flag it here.
            response_metadata["citations_enforced"] = (
                not contexts or confidence == "insufficient" or has_citation_markers(answer)
            )
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
        if settings.quota_enabled:
            query_cache.consume_quota(
                _api_key.id,
                query_cache.count_tokens(req.query, model) + query_cache.count_tokens(answer, model),
            )
        if use_cache:
            query_cache.put_cached(ckey, final)
        yield "data: [DONE]\n\n"
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
