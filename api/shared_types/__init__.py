"""Shared type definitions for service extraction work."""

from .core import (
    ConversationContext,
    ConversationTurn,
    InferenceRequest,
    ProviderConfig,
    ProviderName,
)
from .prompt import PromptSpecRequest
from .protocols import IAuthProvider

__all__ = [
    "ConversationContext",
    "ConversationTurn",
    "InferenceRequest",
    "IAuthProvider",
    "PromptSpecRequest",
    "ProviderConfig",
    "ProviderName",
]
