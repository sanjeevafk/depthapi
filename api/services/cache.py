import asyncio
import threading
from typing import Any

import httpx
import orjson

from api.config import get_settings
from api.constants import (
    REDIS_REST_CALL_TIMEOUT_SECONDS,
    UPSTASH_HTTP_CONNECT_TIMEOUT_SECONDS,
    UPSTASH_HTTP_TIMEOUT_SECONDS,
)
from api.logging_config import logger
from services.message_utils import safe_json_parse
from services.redis_safe import safe_redis_call
from api.utils import with_timeout

UNIFIED_IDEMPOTENCY_CACHE_LUA = """
-- unified_idempotency_cache
-- KEYS: [idempotency_key, cache_key]
-- ARGV: [now_ts, idempotency_ttl, idempotency_stale, set_in_progress, check_cache]
local idempotency_key = KEYS[1]
local cache_key = KEYS[2]
local now_ts = tonumber(ARGV[1])
local idempotency_ttl = tonumber(ARGV[2])
local idempotency_stale = tonumber(ARGV[3])
local set_in_progress = tonumber(ARGV[4])
local check_cache = tonumber(ARGV[5])

local raw = redis.call('GET', idempotency_key)
if raw then
    local ok, idem = pcall(cjson.decode, raw)
    if ok and idem then
        local status = idem.status
        if status == 'completed' and idem.response then
            return {1, idem.response}
        end
        if status == 'in_progress' then
            local started_at = tonumber(idem.started_at or now_ts)
            if (now_ts - started_at) < idempotency_stale then
                return {2, ''}
            end
        end
    end
end

if check_cache == 1 then
    local cached = redis.call('GET', cache_key)
    if cached then
        return {3, cached}
    end
end

if set_in_progress == 1 then
    local payload = cjson.encode({status = 'in_progress', started_at = now_ts})
    redis.call('SET', idempotency_key, payload, 'EX', idempotency_ttl)
end

return {0, ''}
"""


class UpstashRedisCompat:
    """Minimal async Redis-like client backed by Upstash REST API."""

    def __init__(self, base_url: str, token: str):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(UPSTASH_HTTP_TIMEOUT_SECONDS, connect=UPSTASH_HTTP_CONNECT_TIMEOUT_SECONDS),
        )

    async def _execute(self, *command: Any) -> Any:
        payload = [[str(part) for part in command]]
        response = await with_timeout(
            self._client.post("/pipeline", json=payload),
            timeout_seconds=REDIS_REST_CALL_TIMEOUT_SECONDS,
            default=None,
            context_label=f"redis_call_{str(command[0]).lower() if command else 'unknown'}",
            swallow_exceptions=True,
        )
        if response is None:
            raise RuntimeError("Redis REST call timed out or failed")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError("Invalid Upstash Redis response")

        first = data[0]
        error = first.get("error") if isinstance(first, dict) else None
        if error:
            raise RuntimeError(str(error))

        return first.get("result") if isinstance(first, dict) else None

    async def _execute_pipeline(self, commands: list[list[Any]]) -> list[Any]:
        payload = [[str(part) for part in command] for command in commands]
        response = await with_timeout(
            self._client.post("/pipeline", json=payload),
            timeout_seconds=REDIS_REST_CALL_TIMEOUT_SECONDS,
            default=None,
            context_label="redis_call_pipeline",
            swallow_exceptions=True,
        )
        if response is None:
            raise RuntimeError("Redis REST pipeline call timed out or failed")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("Invalid Upstash Redis pipeline response")
        results = []
        for item in data:
            if isinstance(item, dict) and item.get("error"):
                raise RuntimeError(str(item.get("error")))
            results.append(item.get("result") if isinstance(item, dict) else None)
        return results

    async def ping(self) -> bool:
        await self._execute("PING")
        return True

    async def get(self, key: str) -> Any:
        return await self._execute("GET", key)

    async def delete(self, key: str) -> int:
        result = await self._execute("DEL", key)
        return int(result) if result is not None else 0

    async def rpush(self, key: str, *values: Any) -> int:
        result = await self._execute("RPUSH", key, *values)
        return int(result) if result is not None else 0

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        result = await self._execute("LTRIM", key, int(start), int(stop))
        return bool(result) if result is not None else True

    async def lrange(self, key: str, start: int, stop: int) -> list[Any]:
        result = await self._execute("LRANGE", key, int(start), int(stop))
        return list(result) if isinstance(result, list) else []

    async def hget(self, key: str, field: str) -> Any:
        return await self._execute("HGET", key, field)

    async def hset(self, key: str, field: str, value: Any) -> int:
        result = await self._execute("HSET", key, field, value)
        return int(result) if result is not None else 0

    async def hgetall(self, key: str) -> dict[str, Any]:
        result = await self._execute("HGETALL", key)
        if not isinstance(result, list):
            return {}
        items = {}
        for i in range(0, len(result), 2):
            k = result[i]
            v = result[i + 1] if i + 1 < len(result) else None
            if k is not None:
                items[str(k)] = v
        return items

    async def setex(self, key: str, ttl: int, value: Any) -> bool:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        await self._execute("SETEX", key, int(ttl), value)
        return True

    async def set_if_not_exists(self, key: str, ttl: int, value: Any) -> bool:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        result = await self._execute("SET", key, value, "NX", "EX", int(ttl))
        return bool(result)

    async def incr(self, key: str) -> int:
        result = await self._execute("INCR", key)
        return int(result)

    async def incrby(self, key: str, amount: int) -> int:
        result = await self._execute("INCRBY", key, int(amount))
        return int(result)

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        return await self._execute("EVAL", script, int(numkeys), *args)

    async def pipeline(self, commands: list[list[Any]]) -> list[Any]:
        return await self._execute_pipeline(commands)

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        result = await self._execute("EXPIRE", key, int(ttl_seconds))
        return bool(int(result)) if result is not None else False

    async def ttl(self, key: str) -> int:
        result = await self._execute("TTL", key)
        return int(result) if result is not None else -2

    async def close(self) -> None:
        await self._client.aclose()


_client: UpstashRedisCompat | None = None


_client: UpstashRedisCompat | None = None
_client_lock: asyncio.Lock | None = None
_client_lock_loop: asyncio.AbstractEventLoop | None = None
_thread_lock = threading.Lock()


def _get_lock() -> asyncio.Lock:
    global _client_lock, _client_lock_loop
    with _thread_lock:
        current_loop = asyncio.get_running_loop()
        if _client_lock is None or _client_lock_loop is not current_loop:
            _client_lock = asyncio.Lock()
            _client_lock_loop = current_loop
        return _client_lock


def _strip_env_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


async def get_redis() -> UpstashRedisCompat:
    """Get or create Upstash Redis REST client."""
    global _client
    if _client is not None:
        return _client

    async with _get_lock():
        if _client is not None:
            return _client

        settings = get_settings()
        base_url = _strip_env_quotes(getattr(settings, "upstash_redis_rest_url", ""))
        token = _strip_env_quotes(getattr(settings, "upstash_redis_rest_token", ""))

        if not base_url or not token:
            raise RuntimeError("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required")

        _client = UpstashRedisCompat(base_url=base_url, token=token)
        return _client


async def check_idempotency_and_cache(
    *,
    idempotency_key: str,
    cache_key: str,
    now_ts: int,
    idempotency_ttl: int,
    idempotency_stale: int,
    set_in_progress: bool,
    check_cache: bool,
) -> dict[str, Any]:
    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            return {"status": "new"}
        result = await safe_redis_call(
            redis.eval,
            UNIFIED_IDEMPOTENCY_CACHE_LUA,
            2,
            idempotency_key,
            cache_key,
            now_ts,
            idempotency_ttl,
            idempotency_stale,
            1 if set_in_progress else 0,
            1 if check_cache else 0,
            operation="eval",
        )
        if result is None:
            return {"status": "new"}
        status_code = int(result[0]) if isinstance(result, (list, tuple)) and result else 0
        payload = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else None
        if status_code == 1:
            return {"status": "replay", "response": str(payload or "")}
        if status_code == 2:
            return {"status": "wait"}
        if status_code == 3:
            if payload is None:
                return {"status": "new"}
            loaded = safe_json_parse(payload)
            if isinstance(loaded, dict):
                return {"status": "cache_hit", "cached": loaded}
            try:
                await safe_redis_call(redis.delete, cache_key, operation="delete")
            except Exception as exc:
                logger.debug("cache_cleanup_failed", key=cache_key, error=str(exc))
        return {"status": "new"}
    except Exception as exc:
        logger.warning(
            "cache_idempotency_check_failed",
            key=idempotency_key,
            error=str(exc),
        )
        return {"status": "new"}


async def cache_get(key: str) -> dict[str, Any] | None:
    """Get cached JSON value."""
    try:
        r = await safe_redis_call(get_redis, operation="connect")
        if r is None:
            return None
        val = await safe_redis_call(r.get, key, operation="get")
        if val is None:
            return None
        loaded = safe_json_parse(val)
        if isinstance(loaded, dict):
            return loaded
        try:
            await safe_redis_call(r.delete, key, operation="delete")
        except Exception as exc:
            logger.debug("cache_cleanup_failed", key=key, error=str(exc))
        logger.warning("cache_json_parse_failed", key=key)
        return None
    except Exception as e:
        logger.warning("cache_get_failed", key=key, error=str(e))
        return None


async def cache_get_many(keys: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch multiple cached JSON values in a single Redis pipeline."""
    if not keys:
        return {}
    try:
        r = await safe_redis_call(get_redis, operation="connect")
        if r is None:
            return {}
        results = await safe_redis_call(r.pipeline, [["GET", key] for key in keys], operation="pipeline")
        if not isinstance(results, list):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for key, val in zip(keys, results):
            if val is None:
                continue
            if isinstance(val, (bytes, bytearray)):
                payload = bytes(val)
            elif isinstance(val, str):
                payload = val.encode("utf-8")
            else:
                payload = str(val).encode("utf-8")
            loaded = orjson.loads(payload)
            if isinstance(loaded, dict):
                out[key] = loaded
        return out
    except Exception as e:
        logger.warning("cache_get_many_failed", key_count=len(keys), error=str(e))
        return {}


async def cache_set(key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
    """Set cached JSON value with TTL."""
    try:
        r = await safe_redis_call(get_redis, operation="connect")
        if r is None:
            return False
        settings = get_settings()
        ttl_seconds = int(ttl or getattr(settings, "cache_ttl", 3600))
        await safe_redis_call(
            r.setex,
            key,
            ttl_seconds,
            orjson.dumps(value).decode("utf-8"),
            operation="setex",
        )
        return True
    except Exception as e:
        logger.warning("cache_set_failed", key=key, error=str(e))
        return False


async def cache_set_many(values: dict[str, dict[str, Any]], ttl: int | None = None) -> bool:
    """Set multiple cached JSON values in a single Redis pipeline."""
    if not values:
        return True
    try:
        r = await safe_redis_call(get_redis, operation="connect")
        if r is None:
            return False
        settings = get_settings()
        ttl_seconds = int(ttl or getattr(settings, "cache_ttl", 3600))
        commands = []
        for key, value in values.items():
            payload = orjson.dumps(value).decode("utf-8")
            commands.append(["SETEX", key, ttl_seconds, payload])
        await safe_redis_call(r.pipeline, commands, operation="pipeline")
        return True
    except Exception as e:
        logger.warning("cache_set_many_failed", key_count=len(values), error=str(e))
        return False


async def cache_set_if_absent(key: str, value: dict[str, Any], ttl: int) -> bool:
    """Set cached JSON value only if the key is missing."""
    try:
        r = await safe_redis_call(get_redis, operation="connect")
        if r is None:
            return False
        payload = orjson.dumps(value).decode("utf-8")
        result = await safe_redis_call(r.set_if_not_exists, key, ttl, payload, operation="set_if_not_exists")
        return bool(result)
    except Exception as e:
        logger.warning("cache_set_if_absent_failed", key=key, error=str(e))
        return False


async def close_redis() -> None:
    """Close Upstash Redis REST client."""
    global _client
    client: UpstashRedisCompat | None = None
    async with _get_lock():
        if _client:
            client = _client
            _client = None
    if client:
        await client.close()
