from __future__ import annotations

import httpx
from openai import APIConnectionError, APIStatusError

from services.fallback_orchestrator import FallbackOrchestrator
from services.provider_registry import ProviderRegistry


def test_classify_error_marks_retryable_status_codes() -> None:
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(429, request=request)
    exc = APIStatusError("rate limited", response=response, body={"error": "rate_limited"})

    classification = FallbackOrchestrator(ProviderRegistry()).classify_error(exc)
    assert classification.retryable is True
    assert classification.auth is False


def test_classify_error_marks_bad_request_non_retryable() -> None:
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(400, request=request)
    exc = APIStatusError("bad request", response=response, body={"error": "bad_request"})

    classification = FallbackOrchestrator(ProviderRegistry()).classify_error(exc)
    assert classification.bad_request is True
    assert classification.retryable is False


def test_connection_errors_are_retryable() -> None:
    request = httpx.Request("POST", "https://example.com")
    exc = APIConnectionError(request=request)
    orchestrator = FallbackOrchestrator(ProviderRegistry())

    assert orchestrator.is_retryable_error(exc) is True
