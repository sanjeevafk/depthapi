from __future__ import annotations

from types import SimpleNamespace

import services.provider_registry as provider_registry_module
from services.provider_authenticator import ProviderAuthenticator
from services.provider_registry import ProviderRegistry


def test_get_auth_header_returns_bearer_when_key_present(monkeypatch) -> None:
    settings = SimpleNamespace(
        groq_api_key="groq-key",
        cerebras_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
    )
    monkeypatch.setattr(provider_registry_module, "get_settings", lambda: settings)

    auth = ProviderAuthenticator(ProviderRegistry())
    assert auth.get_auth_header("groq") == {"Authorization": "Bearer groq-key"}


def test_validate_credentials_false_when_key_missing(monkeypatch) -> None:
    settings = SimpleNamespace(
        groq_api_key="",
        cerebras_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
    )
    monkeypatch.setattr(provider_registry_module, "get_settings", lambda: settings)

    auth = ProviderAuthenticator(ProviderRegistry())
    assert auth.validate_credentials("gemini") is False


def test_refresh_auth_reloads_registry(monkeypatch) -> None:
    settings_a = SimpleNamespace(
        groq_api_key="old",
        cerebras_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
    )
    settings_b = SimpleNamespace(
        groq_api_key="new",
        cerebras_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
    )
    values = [settings_a, settings_b]
    monkeypatch.setattr(provider_registry_module, "get_settings", lambda: values.pop(0) if values else settings_b)

    registry = ProviderRegistry()
    auth = ProviderAuthenticator(registry)
    assert auth.get_api_key("groq") == "old"
    auth.refresh_auth("groq")
    assert auth.get_api_key("groq") == "new"
