"""Core shared types used by extracted service modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProviderName = Literal["groq", "cerebras", "gemini", "openrouter"]


@dataclass(frozen=True)
class ProviderConfig:
    """Provider configuration used by routing and fallback orchestration."""

    name: ProviderName
    api_key: str
    base_url: str
    models: list[str] = field(default_factory=list)
    priority: int = 0
    fallback_chain: list[ProviderName] = field(default_factory=list)


@dataclass(frozen=True)
class InferenceRequest:
    """Normalized inference request payload."""

    topic: str
    mode: Literal["learn", "technical", "socratic"]
    prompt_mode: str
    model_alias: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024


@dataclass(frozen=True)
class ConversationTurn:
    """Single turn in conversation context."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ConversationContext:
    """Conversation context used by prompt and orchestration services."""

    conversation_id: str
    user_id: str
    turns: list[ConversationTurn] = field(default_factory=list)


__all__ = [
    "ProviderConfig",
    "ProviderName",
    "InferenceRequest",
    "ConversationContext",
    "ConversationTurn",
]
