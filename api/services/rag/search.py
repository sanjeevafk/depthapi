import asyncio
import hashlib
import os
import random
from typing import Any, Dict, List, Literal

import httpx
from pydantic import SecretStr

import api.config as config_module
from api.logging_config import logger
from api.services.infra.cache import cache_get, cache_set

ProviderName = Literal["tavily", "serper", "exa"]


class SearchManager:
    _shared_client: httpx.AsyncClient | None = None
    _client_init_lock: asyncio.Lock | None = None

    def __init__(self):
        self.visual_keywords = {"diagram", "flowchart", "image", "photo", "visual", "graph", "chart"}

    def _settings(self):
        return config_module.get_settings()

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        if cls._shared_client is not None:
            return cls._shared_client

        if cls._client_init_lock is None:
            cls._client_init_lock = asyncio.Lock()

        async with cls._client_init_lock:
            if cls._shared_client is None:
                cls._shared_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(5.0),
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                )
            return cls._shared_client

    async def close(self) -> None:
        if SearchManager._client_init_lock is None:
            SearchManager._client_init_lock = asyncio.Lock()
        async with SearchManager._client_init_lock:
            if SearchManager._shared_client is None:
                return
            await SearchManager._shared_client.aclose()
            SearchManager._shared_client = None

    @staticmethod
    def _secret_to_str(value: object) -> str:
        if isinstance(value, SecretStr):
            return value.get_secret_value().strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join((query or "").strip().split())

    @staticmethod
    def _query_hash(query: str) -> str:
        normalized = SearchManager._normalize_query(query)
        return hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_text(value: object, default: str = "") -> str:
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            return cleaned if cleaned else default
        return default

    @staticmethod
    def _json_dict(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _provider_keys_present(self) -> dict[ProviderName, bool]:
        settings = self._settings()
        return {
            "tavily": bool(self._secret_to_str(getattr(settings, "tavily_api_key", ""))),
            "serper": bool(self._secret_to_str(getattr(settings, "serper_api_key", ""))),
            "exa": bool(self._secret_to_str(getattr(settings, "exa_api_key", ""))),
        }

    def _configured_providers(self) -> list[ProviderName]:
        presence = self._provider_keys_present()
        return [provider for provider, configured in presence.items() if configured]

    def _deterministic_mode_enabled(self) -> bool:
        value = str(os.getenv("DEPTHAPI_BENCHMARK_MODE", "") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    async def get_search_context(self, query: str) -> str:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            return ""

        query_hash = self._query_hash(normalized_query)
        cache_key = f"search:{hashlib.sha256(normalized_query.lower().encode('utf-8')).hexdigest()}"

        configured_providers = self._configured_providers()
        if not configured_providers:
            logger.warning("search_no_provider_configured", query_hash=query_hash)
            return ""

        try:
            cached = await cache_get(cache_key)
            if isinstance(cached, dict):
                cached_content = self._safe_text(cached.get("content"), "")
                if cached_content:
                    logger.info("search_cache_hit", query_hash=query_hash)
                    return cached_content
        except Exception as exc:
            logger.warning("cache_error_search", query_hash=query_hash, error=str(exc))

        provider = self._select_provider(normalized_query, configured_providers)
        logger.info(
            "search_provider_selected",
            provider=provider,
            configured_providers=configured_providers,
            query_hash=query_hash,
        )

        content = ""
        try:
            content = await self._search_provider(provider, normalized_query)
        except Exception as exc:
            logger.error(
                "search_provider_failed",
                provider=provider,
                query_hash=query_hash,
                error=str(exc),
            )

        if not content:
            content = await self._fallback_search(
                normalized_query,
                failed_provider=provider,
                configured_providers=configured_providers,
            )

        if content:
            try:
                await cache_set(cache_key, {"content": content}, ttl=86400)
            except Exception as exc:
                logger.warning("search_cache_write_failed", query_hash=query_hash, error=str(exc))

        return content

    async def get_structured_search_context(self, query: str) -> Dict[str, Any]:
        """Return a structured payload suitable for prompt injection in technical mode."""
        content = await self.get_search_context(query)
        return {
            "query": self._normalize_query(query),
            "provider_keys_present": self._provider_keys_present(),
            "context": content,
        }

    def _select_provider(self, query: str, configured_providers: list[ProviderName]) -> ProviderName:
        if self._deterministic_mode_enabled():
            return configured_providers[0]

        lowered_query = query.lower()
        if "serper" in configured_providers and any(keyword in lowered_query for keyword in self.visual_keywords):
            return "serper" if random.random() < 0.7 else self._weighted_random(configured_providers)

        return self._weighted_random(configured_providers)

    def _weighted_random(self, configured_providers: list[ProviderName]) -> ProviderName:
        weighted_candidates: list[tuple[ProviderName, float]] = [
            (provider, weight)
            for provider, weight in (("tavily", 0.5), ("serper", 0.3), ("exa", 0.2))
            if provider in configured_providers
        ]
        if not weighted_candidates:
            # Should never happen because callers gate on configured providers,
            # but keep deterministic fallback for safety.
            return "tavily"

        total_weight = sum(weight for _, weight in weighted_candidates)
        roll = random.random() * total_weight
        cumulative = 0.0
        for provider, weight in weighted_candidates:
            cumulative += weight
            if roll <= cumulative:
                return provider

        return weighted_candidates[-1][0]

    async def _search_provider(self, provider: ProviderName, query: str) -> str:
        if provider == "tavily":
            return await self._search_tavily(query)
        if provider == "serper":
            return await self._search_serper(query)
        return await self._search_exa(query)

    async def _search_tavily(self, query: str) -> str:
        settings = self._settings()
        api_key = self._secret_to_str(getattr(settings, "tavily_api_key", ""))
        if not api_key:
            raise ValueError("Tavily API key missing")

        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": 5,
        }
        client = await self._get_client()
        resp = await client.post("https://api.tavily.com/search", json=payload, timeout=5.0)
        resp.raise_for_status()
        data = self._json_dict(resp)

        answer = self._safe_text(data.get("answer"), "")
        raw_results = data.get("results")
        results: list[dict[str, Any]] = (
            [item for item in raw_results if isinstance(item, dict)]
            if isinstance(raw_results, list)
            else []
        )

        lines: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = self._safe_text(item.get("title"), "Untitled")
            content = self._safe_text(item.get("content"), "No summary available.")
            url = self._safe_text(item.get("url"), "")
            url_suffix = f" ({url})" if url else ""
            lines.append(f"- {title}: {content}{url_suffix}")

        if not lines and not answer:
            return ""

        prefix = f"Answer: {answer}\n" if answer else ""
        return f"{prefix}Sources:\n" + "\n".join(lines)

    async def _search_serper(self, query: str) -> str:
        settings = self._settings()
        api_key = self._secret_to_str(getattr(settings, "serper_api_key", ""))
        if not api_key:
            raise ValueError("Serper API key missing")

        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }
        client = await self._get_client()
        resp = await client.post(
            "https://google.serper.dev/search",
            headers=headers,
            json={"q": query},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = self._json_dict(resp)

        raw_organic = data.get("organic")
        organic: list[dict[str, Any]] = (
            [item for item in raw_organic if isinstance(item, dict)]
            if isinstance(raw_organic, list)
            else []
        )
        lines: list[str] = []
        for item in organic[:5]:
            title = self._safe_text(item.get("title"), "Untitled")
            snippet = self._safe_text(item.get("snippet"), "No snippet available.")
            link = self._safe_text(item.get("link"), "")
            link_suffix = f" ({link})" if link else ""
            lines.append(f"- {title}: {snippet}{link_suffix}")

        return "\n".join(lines)

    async def _search_exa(self, query: str) -> str:
        settings = self._settings()
        api_key = self._secret_to_str(getattr(settings, "exa_api_key", ""))
        if not api_key:
            raise ValueError("Exa API key missing")

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        client = await self._get_client()
        resp = await client.post(
            "https://api.exa.ai/search",
            headers=headers,
            json={"query": query, "numResults": 5, "contents": {"text": True}},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = self._json_dict(resp)

        raw_results = data.get("results")
        results: list[dict[str, Any]] = (
            [item for item in raw_results if isinstance(item, dict)]
            if isinstance(raw_results, list)
            else []
        )
        lines: list[str] = []
        for item in results:
            title = self._safe_text(item.get("title"), "Untitled")
            raw_text = self._safe_text(item.get("text"), "")
            if raw_text:
                body = raw_text[:300]
                ellipsis = "..." if len(raw_text) > 300 else ""
            else:
                body = "No summary available."
                ellipsis = ""
            url = self._safe_text(item.get("url"), "")
            url_suffix = f" ({url})" if url else ""
            lines.append(f"- {title}: {body}{ellipsis}{url_suffix}")

        return "\n".join(lines)

    async def _fallback_search(
        self,
        query: str,
        failed_provider: ProviderName,
        configured_providers: list[ProviderName] | None = None,
    ) -> str:
        """Parallel fallback that returns first non-empty result and cancels the rest."""
        provider_pool: list[ProviderName] = (
            configured_providers
            if configured_providers is not None
            else self._configured_providers()
        )
        candidates = [provider for provider in provider_pool if provider != failed_provider]
        logger.info(
            "search_fallback_start",
            failed_provider=failed_provider,
            fallback_providers=candidates,
            query_hash=self._query_hash(query),
        )
        if not candidates:
            return ""

        task_to_provider = {
            asyncio.create_task(self._search_provider(provider=provider, query=query)): provider  # type: ignore[arg-type]
            for provider in candidates
        }
        pending = set(task_to_provider.keys())

        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    provider = task_to_provider[task]
                    try:
                        result = task.result()
                        if result:
                            logger.info("search_fallback_success", provider=provider, query_hash=self._query_hash(query))
                            return result
                    except Exception as exc:
                        logger.warning(
                            "fallback_provider_failed",
                            provider=provider,
                            query_hash=self._query_hash(query),
                            error=str(exc),
                        )
            logger.warning("search_fallback_exhausted", query_hash=self._query_hash(query))
            return ""
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def get_images(self, query: str) -> List[Dict[str, str]]:
        settings = self._settings()
        serper_key = self._secret_to_str(getattr(settings, "serper_api_key", ""))
        if not serper_key:
            return []

        headers = {
            "X-API-KEY": serper_key,
            "Content-Type": "application/json",
        }
        try:
            client = await self._get_client()
            resp = await client.post(
                "https://google.serper.dev/images",
                headers=headers,
                json={"q": query},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = self._json_dict(resp)
            raw_images = data.get("images")
            images: list[dict[str, Any]] = (
                [item for item in raw_images if isinstance(item, dict)]
                if isinstance(raw_images, list)
                else []
            )
            output: list[dict[str, str]] = []
            for image in images[:3]:
                image_url = self._safe_text(image.get("imageUrl"), "")
                title = self._safe_text(image.get("title"), "Image")
                if image_url:
                    output.append({"url": image_url, "title": title})
            return output
        except Exception as exc:
            logger.error("image_search_failed", error=str(exc), query_hash=self._query_hash(query))
            return []


search_service = SearchManager()


async def close_search_client() -> None:
    await search_service.close()
