"""Single, mode-free RAG query endpoint with OKF cognitive depth tuning."""
import asyncio
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

router = APIRouter(tags=["query"])

log = logging.getLogger(__name__)


_NEGATION_KEYWORDS = frozenset({"not", "without", "except", "sans"})


def parse_query_negations(query: str, explicit_negatives: list[str] | None = None) -> tuple[str, list[str]]:
    """Extract negative query terms (e.g. 'NOT term', '-term', 'without term') and return sanitized query + negative terms."""
    negatives: list[str] = [n.strip().lower() for n in (explicit_negatives or []) if n.strip()]
    seen = set(negatives)
    kept: list[str] = []
    tokens = query.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.lower() in _NEGATION_KEYWORDS and i + 1 < len(tokens):
            term = tokens[i + 1].strip(",.;:!?").lower()
            if term and term not in seen:
                seen.add(term)
                negatives.append(term)
            i += 2
            continue
        if len(tok) > 1 and tok.startswith("-") and all(ch.isalnum() or ch in "_-" for ch in tok[1:]):
            term = tok[1:].lower()
            if term not in seen:
                seen.add(term)
                negatives.append(term)
            i += 1
            continue
        kept.append(tok)
        i += 1

    clean_query = " ".join(kept) or query.strip()
    return clean_query, negatives


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


def _build_citations(ordered_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build citations aligned with the 1-based [N] numbering passed to the LLM."""
    return [
        {"source": row.get("source_url") or str(row.get("document_id") or "unknown")}
        for row in ordered_contexts
    ]


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
    use_quality_signals: bool = Field(
        default=False,
        description=(
            "Blend recency decay and quality_score into the BM25 first-stage scorer "
            "(turbopuffer rank-by-attribute pattern). Requires migration 005. "
            "Off by default until you run recall evals comparing v5 vs v6."
        ),
    )
    recency_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=5.0,
        description="Weight for the Robertson sigmoid recency decay (midpoint=30 days). Used when use_quality_signals=True.",
    )
    quality_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=5.0,
        description="Weight for chunk quality_score [0,1] contribution. Used when use_quality_signals=True.",
    )
    explain: bool = Field(
        default=False,
        description="When True, include detailed scoring breakdowns (dense/lexical/RRF/graph) and execution diagnostics in response metadata.",
    )
    negative_terms: list[str] = Field(
        default_factory=list,
        description="Terms to penalize in retrieval ranking via Mosaic negative algebra (e.g. ['memcached', 'flask']).",
    )


class QueryResponse(BaseModel):
    answer: str
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _prepare_query(
    req: QueryRequest,
    _api_key: ApiKeyRecord,
) -> tuple[Any, str, bool, str, QueryResponse | None, list[float] | None]:
    """Check quota and concurrently evaluate cache, embed query, and warm DB pool."""
    settings = get_settings()
    model = settings.llm_model
    use_cache = not req.bypass_cache
    clean_query, negative_terms = parse_query_negations(req.query, req.negative_terms)
    ckey = query_cache.cache_key(
        _api_key.id, req.query, req.collection_id, req.depth, req.temperature,
        req.rerank, req.use_trusted_corpus, req.graph_hops, model, req.explain,
        negative_terms=negative_terms,
    )
    if settings.quota_enabled:
        await query_cache.check_quota(_api_key.id, _api_key.is_pro, query_cache.count_tokens(req.query, model))

    if use_cache:
        hit = await query_cache.get_cached(ckey)
        if hit is not None:
            try:
                cached_resp = QueryResponse(**hit)
                cached_resp.cached = True
                if settings.quota_enabled:
                    await query_cache.consume_quota(_api_key.id, query_cache.count_tokens(req.query, model))
                return settings, model, use_cache, ckey, cached_resp, None
            except Exception as exc:
                log.warning("Cached payload invalid, treating as miss: %s", exc)

    async def _embed_query() -> list[float] | None:
        if req.depth in (1, 2):
            return None
        try:
            embs = await embed_texts([clean_query])
            return embs[0] if embs else None
        except Exception as exc:
            log.warning("Concurrent embedding failed: %s", exc)
            return None

    async def _checkout_conn() -> None:
        try:
            pool = get_pool()
            conn = await pool.acquire()
            await pool.release(conn)
        except Exception:
            pass

    precomputed_embedding, _ = await asyncio.gather(
        _embed_query(),
        _checkout_conn(),
    )

    return settings, model, use_cache, ckey, None, precomputed_embedding


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> QueryResponse:
    settings, model, use_cache, ckey, cached_resp, precomputed_embedding = await _prepare_query(req, _api_key)
    if cached_resp is not None:
        return cached_resp

    contexts, ordered_contexts, response_metadata, collection_filter, confidence = await _retrieve(
        req, _api_key, query_embedding=precomputed_embedding
    )

    if confidence == "insufficient":
        answer = "I could not find sufficient matching documentation in your collection to answer this query reliably."
    else:
        answer = await generate_response(req.query, ordered_contexts, req.temperature)

    citations = _build_citations(ordered_contexts)
    response_metadata["citations_enforced"] = (
        not contexts or confidence == "insufficient" or has_citation_markers(answer)
    )
    resp = QueryResponse(answer=answer, contexts=contexts, citations=citations, metadata=response_metadata)
    if settings.quota_enabled:
        await query_cache.consume_quota(
            _api_key.id,
            query_cache.count_tokens(req.query, model) + query_cache.count_tokens(answer, model),
        )
    if use_cache:
        await query_cache.put_cached(ckey, resp.model_dump())
    return resp


async def _retrieve(
    req: QueryRequest,
    _api_key: ApiKeyRecord,
    query_embedding: list[float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], UUID | None, str]:
    """Shared retrieval/rerank/confidence pipeline for query + query/stream."""
    try:
        collection_filter = UUID(req.collection_id) if req.collection_id else None
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    clean_query, negative_terms = parse_query_negations(req.query, req.negative_terms)

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
        effective_hops = detect_graph_hops(clean_query)
        graph_mode = "auto"

    contexts: list[dict[str, Any]] = []

    # Cognitive Depth 1-2: Direct concept summaries from knowledge_concepts (<200ms, ~95% token savings)
    if req.depth in (1, 2):
        max_concepts = 2 if req.depth == 1 else 4
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                escaped = clean_query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
            log.warning("Concept lookup failed: %s", exc)

    # Cognitive Depth 3-5 or fallback when Depths 1-2 found no concept notes.
    # Dense-first: a strong dense hit is used directly; the hybrid (dense +
    # lexical RRF) functions serve as the backstop when dense coverage is weak.
    retrieval_mode = "concept" if contexts else "dense"
    if not contexts:
        settings = get_settings()
        if query_embedding is None:
            async def _acquire_conn() -> None:
                try:
                    pool = get_pool()
                    conn = await pool.acquire()
                    await pool.release(conn)
                except Exception:
                    pass

            embed_res, _ = await asyncio.gather(
                embed_texts([clean_query]),
                _acquire_conn(),
            )
            query_embedding = embed_res[0]
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
                "query_text": clean_query,
                "query_embedding": query_embedding,
                **base_params,
            }
            if effective_hops > 0:
                params["graph_hops"] = effective_hops
                params["graph_weight"] = 0.25
                params["dense_weight"] = settings.rrf_dense_weight
                params["lexical_weight"] = settings.rrf_lexical_weight
                rpc_fn = (
                    "hybrid_search_trusted_with_graph_v7"
                    if req.use_trusted_corpus
                    else "hybrid_search_with_graph_v7"
                )
            else:
                params["recency_weight"] = req.recency_weight if req.use_quality_signals else 0.0
                params["quality_weight"] = req.quality_weight if req.use_quality_signals else 0.0
                params["dense_weight"] = settings.rrf_dense_weight
                params["lexical_weight"] = settings.rrf_lexical_weight
                rpc_fn = "hybrid_search_trusted_v7" if req.use_trusted_corpus else "hybrid_search_v7"

            try:
                raw_contexts = await execute_rpc(rpc_fn, params)
                if req.depth in (1, 2):
                    contexts = raw_contexts[:(1 if req.depth == 1 else 2)]
                else:
                    contexts = raw_contexts
            except Exception:
                # Graceful fallback to v5/v6 if 007_weighted_rrf.sql has not been applied yet
                fallback_params = {
                    "query_text": clean_query,
                    "query_embedding": query_embedding,
                    **base_params,
                }
                if req.use_quality_signals and effective_hops == 0:
                    fallback_params["recency_weight"] = req.recency_weight
                    fallback_params["quality_weight"] = req.quality_weight
                    fallback_fn = "hybrid_search_trusted_v6" if req.use_trusted_corpus else "hybrid_search_v6"
                else:
                    fallback_fn = "hybrid_search_trusted_v5" if req.use_trusted_corpus else "hybrid_search_v5"
                try:
                    raw_contexts = await execute_rpc(fallback_fn, fallback_params)
                    contexts = raw_contexts[:(1 if req.depth == 1 else 2)] if req.depth in (1, 2) else raw_contexts
                except Exception as exc:
                    raise HTTPException(503, "PostgreSQL retrieval is unavailable") from exc

    # Negative query algebra: penalize candidates containing negative terms
    if negative_terms and contexts:
        # Penalize in place so each context keeps its native score scale
        # (cosine for dense, RRF for hybrid); confidence gating depends on it.
        norm_negs = [t.lower() for t in negative_terms]
        for ctx in contexts:
            if any(neg in ctx.get("content", "").lower() for neg in norm_negs):
                ctx["score"] = (ctx.get("score") or 0.0) * 0.5
        contexts = sorted(contexts, key=lambda c: c.get("score") or 0.0, reverse=True)

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
            contexts = await get_reranker_service().rerank(clean_query, contexts, top_n=top_n)
            rerank_applied = True
            if negative_terms and contexts:
                norm_negs = [t.lower() for t in negative_terms]
                for ctx in contexts:
                    content_lower = ctx.get("content", "").lower()
                    if any(neg in content_lower for neg in norm_negs):
                        if "rerank_score" in ctx:
                            ctx["rerank_score"] = ctx["rerank_score"] - 2.0
                        if "score" in ctx:
                            ctx["score"] = (ctx["score"] or 0.0) * 0.5
                contexts.sort(key=lambda c: c.get("rerank_score", c.get("score", 0.0)), reverse=True)
        except Exception as exc:
            log.warning("Rerank failed, using retrieval order: %s", exc)

    # Evaluate retrieval confidence (CRAG - Corrective RAG gating).
    # Score scales differ by source: cosine similarity (~0.3-1.0) for dense
    # hits versus small RRF-fused scores for hybrid/graph retrieval.
    confidence = "high"
    if not contexts:
        confidence = "insufficient"
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
        "graph_hops": effective_hops,
        "graph_mode": graph_mode,
        "confidence": confidence,
        "prompt_ordering": "lost_in_the_middle",
        "retrieval_mode": retrieval_mode,
        "rerank_applied": rerank_applied,
    }
    if negative_terms:
        response_metadata["negative_terms"] = negative_terms
    if req.explain:
        response_metadata["explanation"] = {
            "retrieval_mode": retrieval_mode,
            "effective_hops": effective_hops,
            "graph_mode": graph_mode,
            "rerank_applied": rerank_applied,
            "confidence": confidence,
            "total_chunks_retrieved": len(contexts),
            "chunks": [
                {
                    "document_id": str(c.get("document_id") or ""),
                    "source_url": c.get("source_url"),
                    "score": c.get("score"),
                    "match_source": c.get("match_source") or retrieval_mode,
                }
                for c in contexts
            ],
        }
        if negative_terms:
            response_metadata["explanation"]["negative_terms"] = negative_terms
    return contexts, ordered_contexts, response_metadata, collection_filter, confidence


@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    settings, model, use_cache, ckey, replay, precomputed_embedding = await _prepare_query(req, _api_key)
    if replay is not None:
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

    contexts, ordered_contexts, response_metadata, collection_filter, confidence = await _retrieve(
        req, _api_key, query_embedding=precomputed_embedding
    )
    citations = _build_citations(ordered_contexts)

    async def events():
        # Heartbeat comment keeps proxies from closing idle streams.
        yield ": stream start\n\n"
        disconnected = False
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
                    disconnected = True
                    break
            answer = "".join(parts)
            # No retry on the streaming path (it would double latency and repeat
            # deltas); enforcement retry lives on POST /api/query. Flag it here.
            response_metadata["citations_enforced"] = (
                not contexts or confidence == "insufficient" or has_citation_markers(answer)
            )
            final = {"answer": answer, "contexts": contexts, "citations": citations, "metadata": response_metadata}
            yield f"data: {json.dumps(final, default=str)}\n\n"
        if settings.quota_enabled:
            await query_cache.consume_quota(
                _api_key.id,
                query_cache.count_tokens(req.query, model) + query_cache.count_tokens(answer, model),
            )
        if use_cache and not disconnected:
            await query_cache.put_cached(ckey, final)
        yield "data: [DONE]\n\n"
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
