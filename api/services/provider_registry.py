"""Provider configuration registry and fallback chain resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

import api.config as config

ProviderName = Literal["groq", "cerebras", "gemini", "openrouter"]

PROVIDER_PRIORITY: tuple[ProviderName, ...] = ("groq", "cerebras", "gemini")
PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}

# Semantic alias -> provider-specific model IDs in fallback order.
MODEL_FALLBACK_MAP: dict[str, dict[ProviderName, str]] = {
    "default-fast": {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/free",
        "cerebras": "zai-glm-4.7",
    },
    "learning-detailed": {
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.3-70b-versatile",
        "openrouter": "openrouter/free",
    },
    "technical-primary": {
        "gemini": "gemini-2.5-pro",
        "cerebras": "zai-glm-4.7",
        "groq": "llama-3.3-70b-versatile",
        "openrouter": "openrouter/free",
    },
    "technical-fallback": {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/free",
    },
    "learn-gemini-flash": {
        "gemini": "gemini-2.5-flash",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "learn-groq-llama8b": {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/free",
    },
    "learn-openrouter-free": {
        "gemini": "gemini-2.5-flash",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "technical-gemini-flash": {
        "gemini": "gemini-2.5-flash",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "technical-openrouter-free": {
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "technical-groq-llama8b": {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-pro",
        "openrouter": "openrouter/free",
    },
    "technical-gemini-pro": {
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "technical-cerebras-glm": {
        "cerebras": "zai-glm-4.7",
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
    },
    "socratic-openrouter-free": {
        "openrouter": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    },
    "socratic-groq-llama8b": {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-pro",
        "openrouter": "openrouter/free",
    },
    "socratic-cerebras-glm": {
        "cerebras": "zai-glm-4.7",
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "socratic-gemini-pro": {
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
    "socratic": {
        "gemini": "gemini-2.5-pro",
        "groq": "llama-3.1-8b-instant",
        "openrouter": "openrouter/free",
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    name: ProviderName
    api_key: str
    base_url: str
    priority: int


@dataclass(frozen=True)
class ProviderTarget:
    """Concrete provider + model pair for a routed request."""

    provider: ProviderName
    model: str


class ProviderRegistry:
    """Loads provider config and resolves model fallback chains."""

    def __init__(self) -> None:
        self._configs: dict[ProviderName, ProviderConfig] = {}
        self.reload_from_env()

    def _provider_api_key(self, provider: ProviderName) -> str:
        settings = config.get_settings()
        lookup = {
            "groq": "groq_api_key",
            "cerebras": "cerebras_api_key",
            "gemini": "gemini_api_key",
            "openrouter": "openrouter_api_key",
        }
        value = getattr(settings, lookup[provider], "")
        if isinstance(value, SecretStr):
            return value.get_secret_value().strip()
        if not isinstance(value, str):
            return ""
        return value.strip()

    def reload_from_env(self) -> None:
        priorities = {name: index for index, name in enumerate(PROVIDER_PRIORITY)}
        self._configs = {
            provider: ProviderConfig(
                name=provider,
                api_key=self._provider_api_key(provider),
                base_url=PROVIDER_BASE_URLS[provider],
                priority=priorities.get(provider, len(PROVIDER_PRIORITY)),
            )
            for provider in PROVIDER_BASE_URLS
        }

    def get_config(self, provider: ProviderName) -> ProviderConfig:
        return self._configs[provider]

    def get_provider_api_key(self, provider: ProviderName) -> str:
        return self._configs[provider].api_key

    def build_candidate_chain(self, model_alias: str | None) -> list[ProviderTarget]:
        alias = (model_alias or "default-fast").strip().lower()

        # Direct provider/model route support: e.g. "groq/llama-3.1-8b-instant".
        if "/" in alias:
            provider_name, raw_model = alias.split("/", 1)
            if provider_name in PROVIDER_BASE_URLS and raw_model:
                return [ProviderTarget(provider=provider_name, model=raw_model)]

        model_map = MODEL_FALLBACK_MAP.get(alias) or MODEL_FALLBACK_MAP["default-fast"]
        return [
            ProviderTarget(provider=provider, model=model_name)
            for provider, model_name in model_map.items()
            if provider in PROVIDER_BASE_URLS
        ]

    def get_fallback_chain(self, model_alias: str | None) -> list[ProviderConfig]:
        chain: list[ProviderConfig] = []
        for target in self.build_candidate_chain(model_alias):
            chain.append(self._configs[target.provider])
        return chain

    def configured_providers(self) -> dict[ProviderName, bool]:
        return {provider: bool(cfg.api_key) for provider, cfg in self._configs.items()}
