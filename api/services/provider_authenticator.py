"""Provider authentication helpers."""

from __future__ import annotations

from api.services.provider_registry import ProviderName, ProviderRegistry


class ProviderAuthenticator:
    """Centralizes provider credential and auth header construction."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def openrouter_headers(self) -> dict[str, str]:
        return {
            "HTTP-Referer": "https://knowbear.vercel.app",
            "X-Title": "KnowBear",
        }

    def get_api_key(self, provider: ProviderName) -> str:
        return self._registry.get_provider_api_key(provider)

    def get_auth_header(self, provider: ProviderName) -> dict[str, str]:
        api_key = self.get_api_key(provider)
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def validate_credentials(self, provider: ProviderName) -> bool:
        return bool(self.get_api_key(provider))

    def refresh_auth(self, provider: ProviderName) -> None:
        # API key material is loaded from environment/settings, so refresh is a registry reload.
        _ = provider
        self._registry.reload_from_env()
