"""
Unit tests for OKF Cognitive Depth (Levels 1-5).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


@pytest.mark.asyncio
async def test_cognitive_depth_1_direct_concept_summary(monkeypatch):
    """Depth 1 delivers direct concept summaries with high token efficiency."""
    concept_row = {
        "id": uuid4(),
        "name": "VectorSearch",
        "concept_type": "retrieval",
        "description": "Dense vector similarity search using embeddings.",
        "metadata": {},
    }

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [concept_row]
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = None
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_ctx
    monkeypatch.setattr(query_module, "get_pool", lambda: mock_pool)

    async def fake_generate(q, ctxs, temp):
        return "VectorSearch performs dense nearest neighbor search."

    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="VectorSearch", depth=1)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 1
    assert res.metadata["depth"] == 1
    assert res.metadata["graph_hops"] == 0
    assert len(res.contexts) <= 2
    assert "VectorSearch" in res.contexts[0]["concept_name"]
    # Total characters in context are small (< 400 chars) -> ~95% token reduction vs full multi-chunk RAG
    assert len(res.contexts[0]["content"]) < 400


@pytest.mark.asyncio
async def test_cognitive_depth_3_scoped_hybrid(monkeypatch):
    """Depth 3 uses standard hybrid retrieval with intent-based graph hops."""
    called_rpc = None

    async def fake_rpc(fn_name, params):
        nonlocal called_rpc
        called_rpc = fn_name
        return [{"content": "Context chunk 1", "document_id": str(uuid4()), "score": 0.05}]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_generate(q, ctxs, temp):
        return "Answer at depth 3"

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="Explain the system components", depth=3)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 3
    assert res.metadata["depth"] == 3
    assert called_rpc in ("hybrid_search_trusted_v5", "hybrid_search_trusted_with_graph_v5")


@pytest.mark.asyncio
async def test_cognitive_depth_5_deep_graph_and_forced_rerank(monkeypatch):
    """Depth 5 with manual graph_hops=2 traverses the graph and forces rerank."""
    rpc_params = None
    rerank_executed = False

    async def fake_rpc(fn_name, params):
        nonlocal rpc_params
        rpc_params = params
        return [
            {"content": f"Context chunk {i}", "document_id": str(uuid4()), "score": 0.05}
            for i in range(5)
        ]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    class FakeReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 7):
            nonlocal rerank_executed
            rerank_executed = True
            return candidates[:top_n]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FakeReranker())
    monkeypatch.setattr(query_module, "generate_response", AsyncMock(return_value="Deep answer"))

    # Even with rerank=False in request, depth=5 forces rerank
    req = query_module.QueryRequest(
        query="Complete system architecture deep dive", depth=5, rerank=False, graph_hops=2
    )
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 5
    assert res.metadata["graph_hops"] == 2
    assert res.metadata["graph_mode"] == "manual"
    assert rpc_params.get("graph_hops") == 2
    assert rerank_executed is True
    assert res.metadata["prompt_ordering"] == "lost_in_the_middle"


@pytest.mark.asyncio
async def test_cognitive_depth_5_graph_off_without_intent(monkeypatch):
    """Depth 5 no longer forces graph traversal; intent-free queries stay flat."""
    rpc_fns: list[str] = []

    async def fake_rpc(fn_name, params):
        rpc_fns.append(fn_name)
        return [{"content": "Flat context", "document_id": str(uuid4()), "score": 0.05}]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    class FakeReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 7):
            return candidates[:top_n]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FakeReranker())
    monkeypatch.setattr(query_module, "generate_response", AsyncMock(return_value="Deep answer"))

    req = query_module.QueryRequest(query="Explain quantum mechanics", depth=5, rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["graph_hops"] == 0
    assert res.metadata["graph_mode"] == "auto"
    assert "dense_search_v5" in rpc_fns
    assert not any("graph" in fn for fn in rpc_fns)

