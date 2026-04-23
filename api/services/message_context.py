"""Immutable request context DTO - replaces 40+ scattered variables."""

from dataclasses import dataclass
from typing import Any, Optional

from api.services.conversation_context import ConversationMessage, ConversationIntent
from api.utils import TECHNICAL_MODE


@dataclass(frozen=True)
class MessageContext:
    """
    Immutable request context combining all message processing parameters.
    
    Replaces:
    - request_id, user_id, conversation_id scattered throughout
    - 12+ nested function parameters
    - Multiple dictionaries with duplicate keys
    
    Enables:
    - Single object passed through pipeline
    - Type-safe access to all parameters
    - Easy mocking for tests
    - Clear API contracts
    """
    
    # ── Request Metadata ──────────────────────────────────────────────────
    request_id: str
    user_id: str
    user_id_hash: str
    is_pro: bool
    
    # ── Conversation Context ──────────────────────────────────────────────
    conversation_id: str
    client_message_id: str
    assistant_message_id: str
    
    # ── Content & Modes ───────────────────────────────────────────────────
    content: str
    selected_mode: str  # "chat", "summary", etc.
    llm_mode: str  # "learning", "technical", "socratic"
    prompt_mode: str  # "basic", "intermediate", "advanced"
    temperature: float
    regenerate: bool
    
    # ── Conversation State ────────────────────────────────────────────────
    history_messages: list[dict[str, Any]]
    context_messages: list[ConversationMessage]
    context_signature: str
    intent: ConversationIntent
    
    # ── Configuration ─────────────────────────────────────────────────────
    cache_ttl_seconds: int
    stream_max_seconds: int
    heartbeat_seconds: float
    start_timeout_seconds: float
    fallback_timeout_seconds: float
    is_prod: bool
    
    # ── Optional State ────────────────────────────────────────────────────
    cached_response: Optional[str] = None
    
    # ── Derived Properties ────────────────────────────────────────────────
    @property
    def max_output_tokens(self) -> int:
        """Get max output tokens based on LLM mode."""
        if self.llm_mode == TECHNICAL_MODE:
            return 2048  # Technical responses can be longer
        return 1024
    
    @property
    def should_use_cache(self) -> bool:
        """Check if caching should be used."""
        return not self.regenerate and self.cached_response is not None
    
    @property
    def has_prior_messages(self) -> bool:
        """Check if conversation has history."""
        return len(self.history_messages) > 0
