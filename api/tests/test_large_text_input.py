"""Tests for large text input handling and truncation."""

import pytest
from api.services.security.input_limit import truncate_input_if_needed, get_max_input_tokens_for_alias
from api.services.messaging.token_count import count_prompt_tokens
from api.utils import LEARNING_MODE, TECHNICAL_MODE, SOCRATIC_MODE


@pytest.mark.asyncio
async def test_small_input_not_truncated():
    """Small input (5K chars) should not be truncated."""
    small_text = "This is a test. " * 300  # ~4.8K chars
    truncated, metadata = await truncate_input_if_needed(small_text, "learn-gemini-flash", LEARNING_MODE)
    
    assert metadata["was_truncated"] is False
    assert metadata["original_length"] == len(small_text)
    assert metadata["truncated_to"] == len(small_text)
    assert truncated == small_text


@pytest.mark.asyncio
async def test_large_input_truncated_learning_mode():
    """Large input in learning mode should be truncated to ~40K chars."""
    # Create a ~50K char text
    large_text = "This is a test sentence. " * 2000
    truncated, metadata = await truncate_input_if_needed(large_text, "learn-gemini-flash", LEARNING_MODE)
    
    assert metadata["was_truncated"] is True
    assert metadata["original_length"] == len(large_text)
    assert metadata["truncated_to"] < len(large_text)
    # Learning mode should truncate to ~30K-40K chars (depends on sentence boundaries)
    assert metadata["truncated_to"] < 50000
    assert truncated != large_text
    assert "truncated from" in metadata["truncation_reason"].lower()


@pytest.mark.asyncio
async def test_large_input_truncated_technical_mode():
    """Large input in technical mode should be truncated to ~60K chars."""
    large_text = "This is a test sentence. " * 3000  # ~75K chars
    truncated, metadata = await truncate_input_if_needed(large_text, "technical-cerebras-glm", TECHNICAL_MODE)
    
    assert metadata["was_truncated"] is True
    assert len(truncated) < len(large_text)
    # Technical mode allows larger inputs
    assert metadata["truncated_to"] > metadata["original_length"] * 0.5


@pytest.mark.asyncio
async def test_large_input_truncated_socratic_mode():
    """Socratic mode should have conservative truncation."""
    large_text = "This is a test sentence. " * 3000  # ~75K chars
    truncated, metadata = await truncate_input_if_needed(large_text, "socratic-gemini-pro", SOCRATIC_MODE)
    
    assert metadata["was_truncated"] is True
    assert len(truncated) < len(large_text)
    # Socratic mode is conservative - smaller limit


@pytest.mark.asyncio
async def test_100k_char_input_truncated():
    """Max 100K char API input should be truncated."""
    max_text = "This is a test sentence. " * 4000  # ~100K chars
    truncated, metadata = await truncate_input_if_needed(max_text, "learn-gemini-flash", LEARNING_MODE)
    
    assert metadata["was_truncated"] is True
    assert len(truncated) < len(max_text)
    assert len(truncated) > 0


def test_model_alias_max_tokens():
    """Test that different model aliases have appropriate max token limits."""
    # Gemini Pro: highest limit
    assert get_max_input_tokens_for_alias("technical-gemini-pro", TECHNICAL_MODE) == 18000
    
    # Gemini Flash: medium limit
    assert get_max_input_tokens_for_alias("learn-gemini-flash", LEARNING_MODE) == 15000
    
    # Cerebras: medium-high
    assert get_max_input_tokens_for_alias("technical-cerebras-glm", TECHNICAL_MODE) == 12000
    
    # Groq Llama: low limit
    assert get_max_input_tokens_for_alias("learn-groq-llama8b", LEARNING_MODE) == 6000
    
    # OpenRouter: lowest limit
    assert get_max_input_tokens_for_alias("learn-openrouter-free", LEARNING_MODE) == 5000
    
    # Socratic mode: always conservative
    assert get_max_input_tokens_for_alias("socratic-gemini-pro", SOCRATIC_MODE) == 6000


@pytest.mark.asyncio
async def test_truncation_metadata_accuracy():
    """Verify truncation metadata is accurate."""
    test_text = "This is a test. " * 1000  # ~16K chars
    truncated, metadata = await truncate_input_if_needed(test_text, "learn-gemini-flash", LEARNING_MODE)
    
    assert metadata["original_length"] == len(test_text)
    assert metadata["truncated_to"] == len(truncated)
    if metadata["was_truncated"]:
        assert "Input truncated from" in truncated


@pytest.mark.asyncio
async def test_truncation_at_sentence_boundary():
    """Verify truncation respects sentence boundaries."""
    sentences = "This is sentence one. This is sentence two. This is sentence three. " * 500
    truncated, metadata = await truncate_input_if_needed(sentences, "learn-gemini-flash", LEARNING_MODE)
    
    # Should not end mid-word (respects sentence boundary)
    if metadata["was_truncated"]:
        # Last character should be . or space or end of natural boundary
        assert truncated.rstrip()[-1] in ".?!\n" or truncated.rstrip().endswith("_")


@pytest.mark.asyncio
async def test_token_counting_large_input():
    """Token counter should handle large inputs without error."""
    large_text = "This is a test sentence. " * 4000  # ~100K chars
    
    # Should not raise exception
    tokens = count_prompt_tokens(large_text)
    assert tokens > 0
    assert tokens < 30000  # 100K chars should be ~25K-30K tokens


@pytest.mark.asyncio
async def test_different_aliases_same_mode():
    """Different aliases for same mode should respect their own limits."""
    large_text = "This is a test sentence. " * 2500  # ~62.5K chars
    
    # High-capacity model
    truncated_pro, metadata_pro = await truncate_input_if_needed(
        large_text, "technical-gemini-pro", TECHNICAL_MODE
    )
    
    # Low-capacity model
    truncated_groq, metadata_groq = await truncate_input_if_needed(
        large_text, "technical-groq-llama8b", TECHNICAL_MODE
    )
    
    # Groq should truncate more aggressively
    assert len(truncated_groq) <= len(truncated_pro)


@pytest.mark.asyncio
async def test_empty_and_whitespace_inputs():
    """Handle empty and whitespace-only inputs gracefully."""
    # Empty
    truncated, metadata = await truncate_input_if_needed("", "learn-gemini-flash", LEARNING_MODE)
    assert metadata["was_truncated"] is False
    assert len(truncated) == 0
    
    # Whitespace only
    truncated, metadata = await truncate_input_if_needed("   \n\t  ", "learn-gemini-flash", LEARNING_MODE)
    assert metadata["was_truncated"] is False
