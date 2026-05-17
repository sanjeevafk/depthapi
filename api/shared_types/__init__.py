"""Shared type definitions for service extraction work."""

from .core import (
    ConversationContext,
    ConversationTurn,
    InferenceRequest,
    ProviderConfig,
    ProviderName,
)
from .prompt import PromptSpecRequest

__all__ = [
    "ConversationContext",
    "ConversationTurn",
    "InferenceRequest",
    "PromptSpecRequest",
    "ProviderConfig",
    "ProviderName",
]
