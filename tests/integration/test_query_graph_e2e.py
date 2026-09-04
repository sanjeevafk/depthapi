"""End-to-end integration tests for FastAPI /api/query endpoint with intelligent graph routing."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key


@pytest.fixture
def client():
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)
    app.dependency_overrides[verify_api_key] = lambda: fake_key
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "query_text,req_hops,expected_hops,expected_mode,expected_rpc",
    [
        ("When was Alabama founded?", None, 0, "auto", "hybrid_search_trusted_v5"),
        ("What does the ingest pipeline depend on?", None, 1, "auto", "hybrid_search_trusted_with_graph_v5"),
        ("Show the lineage of depth_engine parser", None, 1, "auto", "hybrid_search_trusted_with_graph_v5"),
        ("Capital of France", 1, 1, "manual", "hybrid_search_trusted_with_graph_v5"),
        ("What does the ingest pipeline depend on?", 0, 0, "manual", "hybrid_search_trusted_v5"),
    ],
)
def test_query_endpoint_e2e_routing(
    client, query_text: str, req_hops: int | None, expected_hops: int, expected_mode: str, expected_rpc: str
):
    mock_rpc = AsyncMock(return_value=[{"document_id": "doc1", "content": "Sample context"}])
    mock_embed = AsyncMock(return_value=[[0.01] * 768])
    mock_gen = AsyncMock(return_value="Generated answer.")

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.generate_response", mock_gen):
        payload = {"query": query_text}
        if req_hops is not None:
            payload["graph_hops"] = req_hops

        res = client.post("/api/query", json=payload, headers={"Authorization": "Bearer test-key"})
        assert res.status_code == 200, res.text
        data = res.json()

        assert data["metadata"]["graph_hops"] == expected_hops
        assert data["metadata"]["graph_mode"] == expected_mode
        assert mock_rpc.call_args[0][0] == expected_rpc
