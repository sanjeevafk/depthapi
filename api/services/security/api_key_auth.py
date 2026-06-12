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

from api.logging_config import logger
from api.services.infra.cache import get_redis
from api.services.infra.redis_safe import safe_redis_call

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
    from api.auth import get_supabase_admin  # local import to avoid circular deps

    supabase = get_supabase_admin()
    if not supabase:
        logger.error("api_key_db_lookup_no_supabase_client")
        return None

    try:
        response = await supabase.table("api_keys") \
            .select(
                "id, prefix, project_name, owner_email, plan, "
                "monthly_token_budget, requests_per_minute, is_active, revoked_at"
            ) \
            .eq("key_hash", key_hash) \
            .eq("is_active", True) \
            .single() \
            .execute()
    except Exception as exc:
        logger.error("api_key_db_lookup_failed", error=str(exc), error_type=type(exc).__name__)
        return None

    data = getattr(response, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
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


def _build_env_record(raw_key: str) -> ApiKeyRecord:
    """Construct a synthetic ApiKeyRecord for env-mode dev keys."""
    prefix = raw_key[:20] if len(raw_key) >= 20 else raw_key
    # Generate a valid UUID string so Postgres RPCs don't crash
    return ApiKeyRecord(
        id=f"00000000-0000-0000-0000-{_hash_key(raw_key)[:12]}",
        prefix=prefix,
        project_name="Local Development",
        owner_email="local@depthapi.dev",
        plan="enterprise",
        monthly_token_budget=0,  # unlimited in enterprise plan
        requests_per_minute=0,   # unlimited in enterprise plan
    )


def _get_env_keys() -> set[str]:
    """Return the set of valid env-mode API keys (normalised, stripped)."""
    from api.config import get_settings  # local import to avoid circular deps
    raw = get_settings().dev_api_keys
    return {k.strip() for k in raw.split(",") if k.strip()}


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> ApiKeyRecord:
    """FastAPI dependency. Validates the Bearer API key and returns its record.

    Auth resolution order:
      1. Env-mode fast path — if AUTH_PROVIDER_MODE=env, or auto+DEV_API_KEYS,
         match against the env key list and return a synthetic record immediately.
         No Redis, no Supabase.
      2. Redis cache hit (~1 ms).
      3. Supabase DB lookup (~20-50 ms); result cached for subsequent requests.

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

    # --- 1: Env-mode fast path ---
    from api.config import get_settings  # local import — avoids circular deps
    settings = get_settings()
    mode = str(getattr(settings, "auth_provider_mode", "auto") or "auto").strip().lower()
    env_keys = _get_env_keys()

    use_env = (mode == "env") or (mode == "auto" and bool(env_keys))
    if use_env:
        if raw_key in env_keys:
            record = _build_env_record(raw_key)
            await _cache_set(_hash_key(raw_key), record)
            return record
        if mode == "env":
            # Hard env mode: never fall through to Supabase
            raise HTTPException(
                status_code=401,
                detail={
                    "type": "invalid_api_key",
                    "message": "API key not found in DEV_API_KEYS.",
                },
            )
        # auto mode with env keys set but key not in the list → fall through to DB

    key_hash = _hash_key(raw_key)

    # --- 2: Redis cache hit (~1 ms) ---
    cached = await _cache_get(key_hash)
    if cached is not None:
        return cached

    # --- 3: Supabase DB lookup (~20-50 ms) ---
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
