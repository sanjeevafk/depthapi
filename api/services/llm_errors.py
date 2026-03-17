"""LiteLLM adapter error types."""


class LLMError(Exception):
    """Base LLM error."""

    error_type = "llm_error"
    retryable = True


class LLMUnavailable(LLMError):
    """Raised when LLM configuration or provider is unavailable."""

    error_type = "service_degraded"
    retryable = False


class LLMBadRequest(LLMError):
    """Raised when the LLM request is invalid."""

    error_type = "bad_request"
    retryable = False


class LLMInvalidAPIKey(LLMError):
    """Raised when LiteLLM authentication fails due to invalid credentials."""

    error_type = "invalid_api_key"
    retryable = False
