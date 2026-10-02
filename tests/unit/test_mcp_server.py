"""
Unit tests for Agent Model Context Protocol (MCP) server.
"""
from __future__ import annotations

import json

import pytest

from api.mcp.server import DepthApiMcpServer


@pytest.mark.asyncio
async def test_mcp_initialize_and_ping():
    server = DepthApiMcpServer()
    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "test-client", "version": "1.0"}},
    }
    resp = await server.handle_message(init_msg)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "depthapi-mcp"

    ping_msg = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    ping_resp = await server.handle_message(ping_msg)
    assert ping_resp["id"] == 2
    assert ping_resp["result"] == {}


@pytest.mark.asyncio
async def test_mcp_list_tools():
    server = DepthApiMcpServer()
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = await server.handle_message(msg)

    tools = resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "depthapi_query",
        "depthapi_ingest",
    }
    assert expected_tools.issubset(tool_names)


@pytest.mark.asyncio
async def test_mcp_tool_query(monkeypatch):
    server = DepthApiMcpServer()

    async def fake_query(req, req_obj, key):
        from api.routers.query import QueryResponse
        return QueryResponse(
            answer="DepthAPI answers queries across depths 1 to 5.",
            citations=[{"source": "test_doc"}],
            metadata={"cognitive_depth": req.depth, "confidence": "high"},
        )

    monkeypatch.setattr("api.mcp.server.execute_query", fake_query)

    call_msg = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "depthapi_query",
            "arguments": {"query": "What is DepthAPI?", "depth": 4},
        },
    }
    resp = await server.handle_message(call_msg)
    assert resp["id"] == 4
    result = resp["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["cognitive_depth"] == 4
    assert "DepthAPI answers" in payload["answer"]


@pytest.mark.asyncio
async def test_mcp_tool_ingest():
    server = DepthApiMcpServer()

    raw_text = "# Machine Learning\n\nML is a branch of [[Artificial Intelligence]].\n"
    call_msg = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "depthapi_ingest",
            "arguments": {"raw_text": raw_text, "filename": "ml.md"},
        },
    }
    resp = await server.handle_message(call_msg)
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "ingested"
    assert "Machine Learning" in payload["concepts_extracted"]


@pytest.mark.asyncio
async def test_mcp_unknown_method():
    server = DepthApiMcpServer()
    msg = {"jsonrpc": "2.0", "id": 99, "method": "unknown_rpc"}
    resp = await server.handle_message(msg)
    assert "error" in resp
    assert resp["error"]["code"] == -32601
