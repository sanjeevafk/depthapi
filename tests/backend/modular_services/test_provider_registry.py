from __future__ import annotations

from types import SimpleNamespace

from pydantic import SecretStr

import services.provider_registry as provider_registry_module
from services.provider_registry import ProviderRegistry


def test_build_candidate_chain_supports_direct_provider_route() -> None:
    registry = ProviderRegistry()
    chain = registry.build_candidate_chain("groq/llama-3.1-8b-instant")
    assert len(chain) == 1
    assert chain[0].provider == "groq"
    assert chain[0].model == "llama-3.1-8b-instant"


def test_get_fallback_chain_resolves_default_alias() -> None:
    registry = ProviderRegistry()
    chain = registry.get_fallback_chain("default-fast")
    assert chain
    assert all(item.base_url for item in chain)


def test_configured_providers_exposes_all_provider_flags() -> None:
    registry = ProviderRegistry()
    configured = registry.configured_providers()
    assert set(configured.keys()) == {"groq", "cerebras", "gemini", "openrouter"}


def test_reload_from_env_uses_secretstr_values(monkeypatch) -> None:
    settings = SimpleNamespace(
        groq_api_key=SecretStr("groq-key"),
        cerebras_api_key=SecretStr("cerebras-key"),
        gemini_api_key=SecretStr("gemini-key"),
        openrouter_api_key=SecretStr("openrouter-key"),
    )
    monkeypatch.setattr(provider_registry_module, "get_settings", lambda: settings)

    registry = ProviderRegistry()
    assert registry.get_provider_api_key("groq") == "groq-key"
    assert registry.get_provider_api_key("openrouter") == "openrouter-key"
