"""Upstash Redis REST cache service with a Redis-like async interface."""

import threading
from typing import Any

import httpx
import orjson

from config import get_settings
from logging_config import logger


class UpstashRedisCompat:
    """Minimal async Redis-like client backed by Upstash REST API."""

    def __init__(self, base_url: str, token: str):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(3.0, connect=2.0),
        )

    async def _execute(self, *command: Any) -> Any:
        payload = [[str(part) for part in command]]
        response = await self._client.post("/pipeline", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError("Invalid Upstash Redis response")

        first = data[0]
        error = first.get("error") if isinstance(first, dict) else None
        if error:
            raise RuntimeError(str(error))

        return first.get("result") if isinstance(first, dict) else None

    async def ping(self) -> bool:
        await self._execute("PING")
        return True

    async def get(self, key: str) -> Any:
        return await self._execute("GET", key)

    async def setex(self, key: str, ttl: int, value: Any) -> bool:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        await self._execute("SETEX", key, int(ttl), value)
        return True

    async def incr(self, key: str) -> int:
        result = await self._execute("INCR", key)
        return int(result)

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        result = await self._execute("EXPIRE", key, int(ttl_seconds))
        return bool(int(result)) if result is not None else False

    async def ttl(self, key: str) -> int:
        result = await self._execute("TTL", key)
        return int(result) if result is not None else -2

    async def close(self) -> None:
        await self._client.aclose()


_client: UpstashRedisCompat | None = None
_client_lock = threading.Lock()


def _strip_env_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


async def get_redis() -> UpstashRedisCompat:
    """Get or create Upstash Redis REST client."""
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        settings = get_settings()
        base_url = _strip_env_quotes(getattr(settings, "upstash_redis_rest_url", ""))
        token = _strip_env_quotes(getattr(settings, "upstash_redis_rest_token", ""))

        if not base_url or not token:
            raise RuntimeError("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required")

        _client = UpstashRedisCompat(base_url=base_url, token=token)
        return _client


async def cache_get(key: str) -> dict[str, Any] | None:
    """Get cached JSON value."""
    try:
        r = await get_redis()
        val = await r.get(key)
        if val is None:
            return None
        if isinstance(val, (bytes, bytearray)):
            payload = bytes(val)
        elif isinstance(val, str):
            payload = val.encode("utf-8")
        else:
            payload = str(val).encode("utf-8")
        loaded = orjson.loads(payload)
        return loaded if isinstance(loaded, dict) else None
    except Exception as e:
        logger.warning("cache_get_failed", key=key, error=str(e))
        return None


async def cache_set(key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
    """Set cached JSON value with TTL."""
    try:
        r = await get_redis()
        settings = get_settings()
        ttl_seconds = int(ttl or getattr(settings, "cache_ttl", 3600))
        await r.setex(key, ttl_seconds, orjson.dumps(value).decode("utf-8"))
        return True
    except Exception as e:
        logger.error("cache_set_failed", key=key, error=str(e))
        return False


async def close_redis() -> None:
    """Close Upstash Redis REST client."""
    global _client
    client: UpstashRedisCompat | None = None
    with _client_lock:
        if _client:
            client = _client
            _client = None
    if client:
        await client.close()
