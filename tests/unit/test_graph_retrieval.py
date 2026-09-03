"""Unit tests for graph-augmented retrieval and traversal hops."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


@pytest.mark.asyncio
async def test_query_default_graph_hops_uses_standard_rpc(monkeypatch):
    called_rpc = None
    called_params = None

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(function_name, params):
        nonlocal called_rpc, called_params
        called_rpc = function_name
        called_params = params
        return [{"content": "Chunk content", "document_id": str(uuid4()), "score": 0.05}]

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)

    key_id = uuid4()
    req = query_module.QueryRequest(query="Test query", graph_hops=0, rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    assert called_rpc == "hybrid_search_trusted_v5"
    assert "graph_hops" not in called_params
    assert len(res.contexts) == 1


@pytest.mark.asyncio
async def test_query_with_graph_hops_calls_graph_rpc(monkeypatch):
    called_rpc = None
    called_params = None

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(function_name, params):
        nonlocal called_rpc, called_params
        called_rpc = function_name
        called_params = params
        return [
            {"content": "Direct seed chunk", "document_id": str(uuid4()), "score": 0.06},
            {"content": "Graph-connected chunk", "document_id": str(uuid4()), "score": 0.04},
        ]

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)

    key_id = uuid4()
    req = query_module.QueryRequest(query="Lineage query", graph_hops=2, rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    assert called_rpc == "hybrid_search_trusted_with_graph_v5"
    assert called_params["graph_hops"] == 2
    assert len(res.contexts) == 2


@pytest.mark.asyncio
async def test_query_graph_rpc_graceful_fallback_on_failure(monkeypatch):
    rpc_calls = []

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(function_name, params):
        rpc_calls.append(function_name)
        if function_name == "hybrid_search_trusted_with_graph_v5":
            raise RuntimeError("Graph procedure unmigrated")
        return [{"content": "Fallback standard chunk", "document_id": str(uuid4()), "score": 0.03}]

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)

    key_id = uuid4()
    req = query_module.QueryRequest(query="Fallback query", graph_hops=1, rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    assert rpc_calls == ["hybrid_search_trusted_with_graph_v5", "hybrid_search_trusted_v5"]
    assert len(res.contexts) == 1
    assert res.contexts[0]["content"] == "Fallback standard chunk"
