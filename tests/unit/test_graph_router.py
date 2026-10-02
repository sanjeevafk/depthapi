"""Unit tests for intelligent graph router and auto-detected query hops."""
from __future__ import annotations

import pytest

from api.services.rag.graph.router import detect_graph_hops


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
