"""Unit tests for intelligent graph router and auto-detected query hops."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from api.routers.query import QueryRequest, query
from api.services.rag.graph.router import detect_graph_hops
from api.services.security.api_key_auth import ApiKeyRecord


@pytest.mark.parametrize(
    "query_text,expected_hops",
    [
        ("What is Python?", 0),
        ("Capital of France", 0),
        ("When was Alabama founded?", 0),
        ("Who directed Inception?", 0),
        ("Explain quantum mechanics", 0),
        ("How to install docker on ubuntu", 0),
        ("What does the ingest pipeline depend on?", 1),
        ("Show the lineage of depth_engine", 1),
        ("What services are connected to postgres?", 1),
        ("Who calls execute_rpc?", 1),
        ("Upstream dependencies of the parser", 1),
        ("Downstream consumers of events", 1),
        ("Impact analysis if auth fails", 1),
        ("System architecture of the worker", 1),
        ("How does the lexer interact with the parser?", 1),
        ("Relationship between Alabama and Tennessee", 1),
        ("Trace the path between nodes", 1),
    ],
)
def test_detect_graph_hops_intent(query_text: str, expected_hops: int):
    assert detect_graph_hops(query_text) == expected_hops


@pytest.mark.asyncio
async def test_query_endpoint_auto_detection_factual():
    req = QueryRequest(query="What is Python?")
    assert req.graph_hops is None

    mock_rpc = AsyncMock(return_value=[{"document_id": "doc1", "content": "Python is a language"}])
    mock_embed = AsyncMock(return_value=[[0.1] * 768])
    mock_gen = AsyncMock(return_value="Python is interpreted.")
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="free", is_pro=False)

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.generate_response", mock_gen):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["graph_hops"] == 0
    assert res.metadata["graph_mode"] == "auto"
    assert mock_rpc.call_args[0][0] == "hybrid_search_trusted_v5"
    assert "graph_hops" not in mock_rpc.call_args[0][1]


@pytest.mark.asyncio
async def test_query_endpoint_auto_detection_dependency():
    req = QueryRequest(query="What does the auth service depend on?")
    assert req.graph_hops is None

    mock_rpc = AsyncMock(return_value=[{"document_id": "doc2", "content": "Auth depends on DB"}])
    mock_embed = AsyncMock(return_value=[[0.1] * 768])
    mock_gen = AsyncMock(return_value="Auth requires DB.")
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="free", is_pro=False)

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.generate_response", mock_gen):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["graph_hops"] == 1
    assert res.metadata["graph_mode"] == "auto"
    assert mock_rpc.call_args[0][0] == "hybrid_search_trusted_with_graph_v5"
    assert mock_rpc.call_args[0][1]["graph_hops"] == 1


@pytest.mark.asyncio
async def test_query_endpoint_manual_override():
    # Force graph_hops=2 on a simple factual query
    req = QueryRequest(query="When was Alabama founded?", graph_hops=2)

    mock_rpc = AsyncMock(return_value=[{"document_id": "doc3", "content": "Founded in 1819"}])
    mock_embed = AsyncMock(return_value=[[0.1] * 768])
    mock_gen = AsyncMock(return_value="1819.")
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="free", is_pro=False)

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.generate_response", mock_gen):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["graph_hops"] == 2
    assert res.metadata["graph_mode"] == "manual"
    assert mock_rpc.call_args[0][0] == "hybrid_search_trusted_with_graph_v5"
    assert mock_rpc.call_args[0][1]["graph_hops"] == 2
