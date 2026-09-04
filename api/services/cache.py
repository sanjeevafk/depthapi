"""Query-result cache and per-key token quotas backed by Redis.

Both features fail open: if Redis is unreachable, queries run uncached and
quotas are not enforced (with a loud warning). Availability first; enforcement
is best-effort until Redis becomes a hard deployment dependency.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from api.config import get_settings

log = logging.getLogger(__name__)

CACHE_VERSION = 1

_client = None


def get_client():
    """Lazy Redis singleton; None when Redis is unreachable. Monkeypatchable."""
    global _client
    if _client is not None:
        return _client
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        _client = client
        return _client
    except Exception as exc:
        log.warning("Redis unavailable, running without cache/quotas: %s", exc)
        return None


def reset_client() -> None:
    """Drop the cached client (tests)."""
    global _client
    _client = None


def cache_key(api_key_id: str, query: str, collection_id: str | None, depth: int,
              temperature: float, rerank: bool, use_trusted: bool,
              graph_hops: int | None, llm_model: str) -> str:
    material = "|".join([
        f"v{CACHE_VERSION}", api_key_id, query.strip(),
        collection_id or "", str(depth), f"{temperature:.2f}",
        str(rerank), str(use_trusted), str(graph_hops), llm_model,
    ])
    return "depthapi:q:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def get_cached(key: str) -> dict[str, Any] | None:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or "answer" not in payload or "contexts" not in payload:
            return None
        return payload
    except Exception as exc:
        log.warning("Cache read failed, treating as miss: %s", exc)
        return None


def put_cached(key: str, payload: dict[str, Any]) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(payload, default=str), ex=get_settings().query_cache_ttl_seconds)
    except Exception as exc:
        log.warning("Cache write failed: %s", exc)


def quota_limit(is_pro: bool) -> int:
    settings = get_settings()
    return settings.pro_daily_token_quota if is_pro else settings.daily_token_quota_per_user


def count_tokens(text: str, model: str) -> int:
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text or ""))
    except Exception as exc:
        log.warning("Token counting failed, assuming 0: %s", exc)
        return 0


def _quota_redis_key(api_key_id: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"depthapi:quota:{api_key_id}:{day}"


def check_quota(api_key_id: str, is_pro: bool, estimated_tokens: int) -> None:
    """Raise 429 when the key would exceed its daily token budget. Fail-open."""
    limit = quota_limit(is_pro)
    if limit <= 0:
        return
    client = get_client()
    if client is None:
        return
    try:
        used = int(client.get(_quota_redis_key(api_key_id)) or 0)
    except Exception as exc:
        log.warning("Quota read failed, allowing request: %s", exc)
        return
    if used + estimated_tokens > limit:
        raise HTTPException(429, "Daily token quota exceeded")


def consume_quota(api_key_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    client = get_client()
    if client is None:
        return
    try:
        key = _quota_redis_key(api_key_id)
        client.incrby(key, tokens)
        client.expire(key, 172800)
    except Exception as exc:
        log.warning("Quota accounting failed: %s", exc)
