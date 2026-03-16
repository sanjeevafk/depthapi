"""LiteLLM adapter error types."""


class LLMError(Exception):
    """Base LLM error."""


class LLMUnavailable(LLMError):
    """Raised when LLM configuration or provider is unavailable."""


class LLMBadRequest(LLMError):
    """Raised when the LLM request is invalid."""
