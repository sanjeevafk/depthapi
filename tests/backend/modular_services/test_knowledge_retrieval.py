import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from api.services.rag.knowledge_retrieval import RetrievalService

# Valid UUIDs for testing
TEST_USER_ID = "00000000-0000-0000-0000-000000000001"
TEST_DOC_ID = "00000000-0000-0000-0000-000000000002"

@pytest.mark.asyncio
async def test_retrieval_service_fallback_v5_to_v4():
    """Test that RetrievalService falls back to v4 when v5 RPC is missing."""
    
    # Mock the database client
    mock_db = MagicMock()
    
    # Setup the first call to fail with "function not found"
    mock_rpc_v5 = MagicMock()
    mock_rpc_v5.execute = AsyncMock(side_effect=Exception("Function 'hybrid_search_v5' not found"))
    
    # Setup the second call (fallback) to succeed
    mock_rpc_v4 = MagicMock()
    mock_rpc_v4.execute = AsyncMock(return_value=MagicMock(data=[{
        "chunk_id": TEST_DOC_ID, 
        "content": "fallback test",
        "filename": "test.pdf",
        "source_url": "http://test.com",
        "chunk_order": 0,
        "source_tier": "customer"
    }]))
    
    # Configure mock_db.rpc to return appropriate mocks
    def rpc_side_effect(name, payload):
        if "v5" in name:
            return mock_rpc_v5
        return mock_rpc_v4
    
    mock_db.rpc.side_effect = rpc_side_effect
    
    service = RetrievalService()
    
    with patch("api.services.rag.knowledge_retrieval.get_supabase_admin", return_value=mock_db):
        results = await service.retrieve_context(
            query="test query",
            api_key_id=TEST_USER_ID,
            limit=5,
            neighbor_window=0
        )
    
    # Assertions
    assert len(results) == 1
    assert results[0]["content"] == "fallback test"
    assert mock_db.rpc.call_count == 2
    assert mock_db.rpc.call_args_list[0][0][0] == "hybrid_search_v5"
    assert mock_db.rpc.call_args_list[1][0][0] == "hybrid_search_v4"

@pytest.mark.asyncio
async def test_retrieval_service_trusted_fallback_v5_to_v4():
    """Test that RetrievalService falls back to v4 for trusted corpus."""
    
    mock_db = MagicMock()
    
    # Mock v5 failing
    mock_rpc_v5 = MagicMock()
    mock_rpc_v5.execute = AsyncMock(side_effect=Exception("Does not exist: hybrid_search_trusted_v5"))
    
    # Mock v4 succeeding
    mock_rpc_v4 = MagicMock()
    mock_rpc_v4.execute = AsyncMock(return_value=MagicMock(data=[{
        "chunk_id": TEST_DOC_ID, 
        "content": "trusted fallback",
        "filename": "trusted.pdf",
        "source_url": "http://trusted.com",
        "chunk_order": 0,
        "source_tier": "trusted"
    }]))
    
    def rpc_side_effect(name, payload):
        if "v5" in name:
            return mock_rpc_v5
        return mock_rpc_v4
    
    mock_db.rpc.side_effect = rpc_side_effect
    
    service = RetrievalService()
    
    # We need to mock get_trusted_corpus_admin
    with patch("api.services.rag.knowledge_retrieval.get_trusted_corpus_admin", return_value=mock_db):
        results = await service.retrieve_context(
            query="trusted query",
            api_key_id=TEST_USER_ID,
            use_trusted_corpus=True,
            neighbor_window=0
        )
    
    assert len(results) == 1
    assert results[0]["content"] == "trusted fallback"
    assert mock_db.rpc.call_count == 2
    assert "v5" in mock_db.rpc.call_args_list[0][0][0]
    assert "v4" in mock_db.rpc.call_args_list[1][0][0]

@pytest.mark.asyncio
async def test_retrieval_service_param_fallback():
    """Test that RetrievalService strips target_collection_id if unsupported."""
    
    mock_db = MagicMock()
    
    # Mock first call failing with param error
    mock_rpc_param_fail = MagicMock()
    mock_rpc_param_fail.execute = AsyncMock(side_effect=Exception("unexpected parameter 'target_collection_id'"))
    
    # Mock second call (retry without param) succeeding
    mock_rpc_success = MagicMock()
    mock_rpc_success.execute = AsyncMock(return_value=MagicMock(data=[{
        "chunk_id": TEST_DOC_ID, 
        "content": "param fallback",
        "filename": "col.pdf",
        "source_url": "http://col.com",
        "chunk_order": 0,
        "source_tier": "customer"
    }]))
    
    mock_db.rpc.side_effect = [mock_rpc_param_fail, mock_rpc_success]
    
    service = RetrievalService()
    
    with patch("api.services.rag.knowledge_retrieval.get_supabase_admin", return_value=mock_db):
        results = await service.retrieve_context(
            query="col query",
            api_key_id=TEST_USER_ID,
            collection_id=TEST_DOC_ID,
            neighbor_window=0
        )
    
    assert len(results) == 1
    assert results[0]["content"] == "param fallback"
    assert mock_db.rpc.call_count == 2
    # Verify second call didn't have the param
    assert "target_collection_id" not in mock_db.rpc.call_args_list[1][0][1]
