"""API Key authentication for DepthAPI B2B endpoints.

Replaces Supabase JWT auth. Every authenticated endpoint uses the
`verify_api_key` dependency which:
  1. Extracts the raw key from `Authorization: Bearer sk-depth-xxx`
  2. SHA-256 hashes it (never stores/logs plaintext)
  3. Checks a Redis cache (60s TTL) to avoid a DB hit on every request
  4. Falls through to Supabase lookup on cache miss
  5. Returns a typed ApiKeyRecord to the route handler

Rate-limit scoping: the api_key.id is passed to enforce_request_controls
as the stable identifier, replacing the old user_id concept.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from logging_config import logger
from services.cache import get_redis
from services.redis_safe import safe_redis_call

import json

_bearer = HTTPBearer(auto_error=False)

# Redis cache TTL for a validated API key (seconds).
# Short enough to pick up revocations quickly.
_CACHE_TTL = 60

# Redis key prefix.
_CACHE_PREFIX = "depthapi:apikey:"

# Plan token budgets (monthly). 0 = unlimited.
PLAN_MONTHLY_BUDGETS: dict[str, int] = {
    "free":       100_000,
    "starter":  2_000_000,
    "pro":     10_000_000,
    "enterprise":        0,
}

# Plan request-per-minute limits. 0 = use global default.
PLAN_RPM: dict[str, int] = {
    "free":       10,
    "starter":    60,
    "pro":       300,
    "enterprise":  0,
}


@dataclass(frozen=True)
class ApiKeyRecord:
    """Validated API key metadata. Never contains the raw key."""
    id: str
    prefix: str
    project_name: str
    owner_email: str
    plan: str
    monthly_token_budget: int
    requests_per_minute: int

    @property
    def is_pro(self) -> bool:
        return self.plan in ("pro", "enterprise")

    @property
    def is_enterprise(self) -> bool:
        return self.plan == "enterprise"


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of the raw API key. This is the lookup token."""
    return hashlib.sha256(raw_key.strip().encode()).hexdigest()


async def _cache_get(key_hash: str) -> ApiKeyRecord | None:
    """Return a cached ApiKeyRecord or None on miss/error."""
    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            return None
        raw = await safe_redis_call(
            redis.get,
            f"{_CACHE_PREFIX}{key_hash}",
            operation="get",
        )
        if raw is None:
            return None
        data: dict = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        return ApiKeyRecord(**data)
    except Exception as exc:
        logger.warning("api_key_cache_get_failed", error=str(exc))
        return None


async def _cache_set(key_hash: str, record: ApiKeyRecord) -> None:
    """Cache the ApiKeyRecord for _CACHE_TTL seconds."""
    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            return
        payload = json.dumps({
            "id": record.id,
            "prefix": record.prefix,
            "project_name": record.project_name,
            "owner_email": record.owner_email,
            "plan": record.plan,
            "monthly_token_budget": record.monthly_token_budget,
            "requests_per_minute": record.requests_per_minute,
        })
        await safe_redis_call(
            redis.setex,
            f"{_CACHE_PREFIX}{key_hash}",
            _CACHE_TTL,
            payload,
            operation="setex",
        )
    except Exception as exc:
        logger.warning("api_key_cache_set_failed", error=str(exc))


async def _cache_invalidate(key_hash: str) -> None:
    """Evict a key from cache immediately (e.g. after revocation)."""
    try:
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is None:
            return
        await safe_redis_call(
            redis.delete,
            f"{_CACHE_PREFIX}{key_hash}",
            operation="delete",
        )
    except Exception as exc:
        logger.warning("api_key_cache_invalidate_failed", error=str(exc))


async def _lookup_in_db(key_hash: str) -> ApiKeyRecord | None:
    """Look up the hashed key in Supabase. Returns None on miss or error."""
    from auth import get_supabase_admin  # local import to avoid circular deps
    import asyncio

    supabase = get_supabase_admin()
    if not supabase:
        logger.error("api_key_db_lookup_no_supabase_client")
        return None

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("api_keys")
            .select(
                "id, prefix, project_name, owner_email, plan, "
                "monthly_token_budget, requests_per_minute, is_active, revoked_at"
            )
            .eq("key_hash", key_hash)
            .eq("is_active", True)
            .is_("revoked_at", "null")
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error("api_key_db_lookup_failed", error=str(exc), error_type=type(exc).__name__)
        return None

    data = getattr(response, "data", None)
    if not isinstance(data, dict) or not data.get("id"):
        return None

    plan = str(data.get("plan") or "free")
    return ApiKeyRecord(
        id=str(data["id"]),
        prefix=str(data.get("prefix") or ""),
        project_name=str(data.get("project_name") or ""),
        owner_email=str(data.get("owner_email") or ""),
        plan=plan,
        monthly_token_budget=int(
            data.get("monthly_token_budget") or PLAN_MONTHLY_BUDGETS.get(plan, 100_000)
        ),
        requests_per_minute=int(
            data.get("requests_per_minute") or PLAN_RPM.get(plan, 10)
        ),
    )


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> ApiKeyRecord:
    """FastAPI dependency. Validates the Bearer API key and returns its record.

    Usage in routes:
        @router.post("/v1/query")
        async def query(api_key: ApiKeyRecord = Depends(verify_api_key)):
            ...
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "missing_api_key",
                "message": (
                    "No API key provided. "
                    "Include `Authorization: Bearer sk-depth-<key>` in your request."
                ),
            },
        )

    raw_key = credentials.credentials
    if not raw_key.startswith("sk-depth-"):
        raise HTTPException(
            status_code=401,
            detail={
                "type": "invalid_api_key_format",
                "message": "API keys must start with `sk-depth-`. Get a key at https://depthapi.dev",
            },
        )

    key_hash = _hash_key(raw_key)

    # 1 — Redis cache hit (fast path, ~1ms)
    cached = await _cache_get(key_hash)
    if cached is not None:
        return cached

    # 2 — Supabase lookup (slow path, ~20-50ms; result cached for next requests)
    _lookup_start = time.perf_counter()
    record = await _lookup_in_db(key_hash)
    _lookup_ms = round((time.perf_counter() - _lookup_start) * 1000, 2)

    if record is None:
        logger.warning(
            "api_key_invalid_or_revoked",
            prefix=raw_key[:16] + "...",
            db_lookup_ms=_lookup_ms,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "type": "invalid_api_key",
                "message": "API key is invalid, revoked, or does not exist.",
            },
        )

    logger.info(
        "api_key_authenticated",
        prefix=record.prefix,
        plan=record.plan,
        db_lookup_ms=_lookup_ms,
    )

    # Cache for subsequent requests
    await _cache_set(key_hash, record)
    return record


async def verify_api_key_optional(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> ApiKeyRecord | None:
    """Same as verify_api_key but returns None instead of raising 401.

    Use this for endpoints that allow unauthenticated access (e.g. the demo playground).
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        return await verify_api_key(credentials)
    except HTTPException:
        return None
