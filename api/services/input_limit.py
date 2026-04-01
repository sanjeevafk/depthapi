"""Input size limits and intelligent truncation for different modes and model aliases."""

from typing import Any

from config import get_settings
from services.token_count import count_prompt_tokens
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE


def get_max_input_tokens_for_alias(alias: str, mode: str) -> int:
    """
    Return model-aware input token limit based on which alias will be used.
    
    Args:
        alias: Model alias (e.g., "learn-gemini-flash", "technical-cerebras-glm")
        mode: Chat mode (learn, technical, socratic)
    
    Returns:
        Max input tokens allowed for this mode+alias combination
    """
    mode_lower = (mode or "").strip().lower()
    alias_lower = (alias or "").strip().lower()
    
    # Gemini Pro: 2M token window
    if "gemini-pro" in alias_lower or alias_lower == "technical-primary":
        return 18000  # ~72K chars; stay well under 2M
    
    # Gemini Flash / default: 1M token window
    if "gemini-flash" in alias_lower or "learn-gemini-flash" in alias_lower:
        return 15000  # ~60K chars
    
    # Cerebras GLM: 32K token window
    if "cerebras" in alias_lower or "technical-cerebras-glm" in alias_lower:
        return 12000  # ~48K chars; stay under 32K
    
    # Groq Llama (8K window): Conservative to avoid truncation during inference
    if "groq" in alias_lower or "llama" in alias_lower:
        return 6000   # ~24K chars; leaves 2K for system + output
    
    # OpenRouter: Unknown model mix, be conservative
    if "openrouter" in alias_lower:
        return 5000   # ~20K chars minimum safe
    
    # Socratic mode: Always conservative (questions don't need massive context)
    if mode_lower == SOCRATIC_MODE:
        return 6000   # ~24K chars
    
    # Default fallback
    return 8000  # ~32K chars


async def truncate_input_if_needed(
    user_input: str,
    alias: str,
    mode: str,
) -> tuple[str, dict[str, Any]]:
    """
    Truncate user input if it exceeds the model-aware limit.
    
    Returns:
        (truncated_input, metadata)
    
    Metadata includes:
        - was_truncated: bool
        - original_length: int (chars)
        - truncated_to: int (chars)
        - truncation_reason: str (reason for truncation)
    """
    max_tokens = get_max_input_tokens_for_alias(alias, mode)
    input_tokens = count_prompt_tokens(user_input)
    
    metadata = {
        "was_truncated": False,
        "original_length": len(user_input),
        "truncated_to": len(user_input),
        "truncation_reason": None,
    }
    
    if input_tokens <= max_tokens:
        return user_input, metadata
    
    # Truncate to ~75% of max (buffer for system prompt + output)
    safe_token_limit = int(max_tokens * 0.75)
    
    # Estimate: 1 token ≈ 4 chars
    safe_char_limit = safe_token_limit * 4
    
    # Truncate at sentence boundary (don't split mid-word)
    truncated = user_input[:safe_char_limit].rstrip()
    
    # Find last period, question mark, or newline
    for delimiter in [". ", "? ", "\n"]:
        last_pos = truncated.rfind(delimiter)
        if last_pos > 0:
            truncated = truncated[:last_pos + len(delimiter) - 1]
            break
    
    # Verify we actually truncated
    if len(truncated) < len(user_input):
        metadata["was_truncated"] = True
        metadata["truncated_to"] = len(truncated)
        
        truncated_tokens = count_prompt_tokens(truncated)
        metadata["truncation_reason"] = (
            f"Input ({input_tokens} tokens) exceeded {mode} mode limit ({max_tokens} tokens) "
            f"for model alias '{alias}'. Truncated to {truncated_tokens} tokens."
        )
        truncated += f"\n\n_[Input truncated from {len(user_input)} to {len(truncated)} characters]_"
    
    return truncated, metadata
