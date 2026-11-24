"""Unit tests for weighted RRF configuration and pg_adapter RPC allowlist."""
from api.adapters.pg_adapter import _ALLOWED_RPC_FUNCTIONS
from api.config import Settings


def test_weighted_rrf_defaults():
    settings = Settings()
    assert settings.rrf_dense_weight == 1.5
    assert settings.rrf_lexical_weight == 0.5


def test_weighted_rrf_rpcs_allowed():
    assert "hybrid_search_v7" in _ALLOWED_RPC_FUNCTIONS
    assert "hybrid_search_trusted_v7" in _ALLOWED_RPC_FUNCTIONS
    assert "hybrid_search_with_graph_v7" in _ALLOWED_RPC_FUNCTIONS
    assert "hybrid_search_trusted_with_graph_v7" in _ALLOWED_RPC_FUNCTIONS
