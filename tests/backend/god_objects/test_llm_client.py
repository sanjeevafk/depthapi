from __future__ import annotations

import httpx

from openai import APIConnectionError, APIStatusError

from services.fallback_orchestrator import FallbackOrchestrator
from services.provider_authenticator import ProviderAuthenticator
from services.provider_registry import ProviderRegistry


def test_provider_registry_resolves_direct_provider_route() -> None:
    registry = ProviderRegistry()
    chain = registry.build_candidate_chain("groq/llama-3.1-8b-instant")
    assert len(chain) == 1
    assert chain[0].provider == "groq"
    assert chain[0].model == "llama-3.1-8b-instant"


def test_provider_authenticator_builds_bearer_header() -> None:
    registry = ProviderRegistry()
    auth = ProviderAuthenticator(registry)
    header = auth.get_auth_header("openrouter")
    if registry.get_provider_api_key("openrouter"):
        assert "Authorization" in header
        assert header["Authorization"].startswith("Bearer ")
    else:
        assert header == {}


def test_fallback_orchestrator_classifies_retryable_status() -> None:
    orchestrator = FallbackOrchestrator(ProviderRegistry())
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(429, request=request)
    exc = APIStatusError("rate limited", response=response, body={"error": "rate_limited"})

    classification = orchestrator.classify_error(exc)
    assert classification.retryable is True
    assert orchestrator.should_retry(classification) is True


def test_fallback_orchestrator_classifies_connection_error_as_retryable() -> None:
    orchestrator = FallbackOrchestrator(ProviderRegistry())
    request = httpx.Request("POST", "https://example.com")
    exc = APIConnectionError(request=request)
    assert orchestrator.is_retryable_error(exc) is True
