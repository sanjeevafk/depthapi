"""Fallback chain and provider error classification logic."""

from __future__ import annotations

from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, AuthenticationError, PermissionDeniedError

from api.services.provider_registry import ProviderRegistry, ProviderTarget

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ErrorClass:
    kind: str
    retryable: bool
    auth: bool = False
    bad_request: bool = False


class FallbackOrchestrator:
    """Resolves candidates and classifies provider errors for retry/fallback behavior."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def build_candidate_chain(self, model_alias: str | None) -> list[ProviderTarget]:
        return self._registry.build_candidate_chain(model_alias)

    def classify_error(self, exc: Exception) -> ErrorClass:
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return ErrorClass(kind="auth", retryable=False, auth=True)
        if isinstance(exc, APIConnectionError):
            return ErrorClass(kind="connection", retryable=True)
        if isinstance(exc, APIStatusError):
            status = int(getattr(exc, "status_code", 0) or 0)
            if status in {401, 403}:
                return ErrorClass(kind="auth", retryable=False, auth=True)
            if status == 400:
                return ErrorClass(kind="bad_request", retryable=False, bad_request=True)
            if status in RETRYABLE_STATUS_CODES:
                return ErrorClass(kind="status_retryable", retryable=True)
            return ErrorClass(kind="status_non_retryable", retryable=False)
        return ErrorClass(kind="unknown", retryable=False)

    def should_retry(self, error_class: ErrorClass) -> bool:
        return bool(error_class.retryable)

    def is_retryable_error(self, exc: Exception) -> bool:
        return self.should_retry(self.classify_error(exc))

    def is_auth_error(self, exc: Exception) -> bool:
        return bool(self.classify_error(exc).auth)
