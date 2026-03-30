import asyncio
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from types import SimpleNamespace
from typing import Any
import json

import httpx
import jwt
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import get_settings
from logging_config import anonymize_user_id, logger
from monitoring import hash_for_monitoring, set_user_context
from supabase import create_client, Client

security = HTTPBearer(auto_error=False)
_PRO_STATE_CACHE: "OrderedDict[str, tuple[bool, float]]" = OrderedDict()
_PRO_STATE_CACHE_LOCK = threading.Lock()
_JWKS_CACHE: dict[str, Any] | None = None
_JWKS_CACHE_EXP: float | None = None
_JWKS_LOCK = threading.Lock()


def _pro_cache_ttl_seconds() -> int:
    try:
        configured_ttl = int(getattr(get_settings(), "pro_state_cache_ttl_seconds", 30) or 30)
    except (ValueError, TypeError):
        configured_ttl = 30
    return min(max(configured_ttl, 1), 30)


def _pro_cache_max_entries() -> int:
    try:
        configured_max = int(getattr(get_settings(), "pro_state_cache_max_entries", 1000) or 1000)
    except (ValueError, TypeError):
        configured_max = 1000
    return min(max(configured_max, 1), 10000)


def _prune_pro_cache_locked(now: float) -> None:
    expired_keys = [user_id for user_id, (_is_pro, expires_at) in _PRO_STATE_CACHE.items() if expires_at <= now]
    for user_id in expired_keys:
        _PRO_STATE_CACHE.pop(user_id, None)

    max_entries = _pro_cache_max_entries()
    while len(_PRO_STATE_CACHE) > max_entries:
        _PRO_STATE_CACHE.popitem(last=False)

def invalidate_pro_cache(user_id: str) -> None:
    if not user_id:
        return
    with _PRO_STATE_CACHE_LOCK:
        _PRO_STATE_CACHE.pop(user_id, None)

@lru_cache(maxsize=1)
def get_supabase() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        logger.warning("auth_supabase_credentials_missing")
        return None
    return create_client(settings.supabase_url, settings.supabase_anon_key)

@lru_cache(maxsize=1)
def get_supabase_admin() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning("auth_supabase_service_role_key_missing")
        return None
    service_key = settings.supabase_service_role_key
    if hasattr(service_key, "get_secret_value"):
        service_key = service_key.get_secret_value()
    return create_client(settings.supabase_url, service_key) # type: ignore

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verify the Supabase JWT token locally."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication credentials")
    token = credentials.credentials
    settings = get_settings()
    if not settings.supabase_url:
        raise HTTPException(status_code=500, detail="Server configuration error: Auth unavailable")

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Invalid token")

        jwks = await _get_jwks(str(settings.supabase_url))
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        key_data = next((item for item in keys or [] if item.get("kid") == kid), None)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid token")

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        decoded = jwt.decode(
            token,
            key=public_key,
            algorithms=[key_data.get("alg", "RS256")],
            options={"verify_aud": False},
            issuer=f"{str(settings.supabase_url).rstrip('/')}/auth/v1",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("auth_verify_token_validation_error", error_type=type(e).__name__)
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

    user_id = str(decoded.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    email = str(decoded.get("email") or "") or None
    app_metadata = decoded.get("app_metadata") if isinstance(decoded.get("app_metadata"), dict) else {}
    user_metadata = decoded.get("user_metadata") if isinstance(decoded.get("user_metadata"), dict) else {}
    is_pro = bool(decoded.get("is_pro") or app_metadata.get("is_pro") or False)

    user = SimpleNamespace(
        id=user_id,
        email=email,
        app_metadata=app_metadata,
        user_metadata=user_metadata,
    )
    set_user_context(
        user_id=user_id or None,
        email_hash=hash_for_monitoring(email) if email else None,
        token_hash=hash_for_monitoring(token),
    )
    return {"user": user, "token": token, "is_pro": is_pro, "exp": decoded.get("exp")}


def _is_admin_user(user: object) -> bool:
    app_metadata = getattr(user, "app_metadata", None)
    if not isinstance(app_metadata, dict):
        return False
    role = app_metadata.get("role")
    if not isinstance(role, str):
        return False
    return role.lower() == "admin"


async def require_admin(auth_data: dict = Depends(verify_token)):
    user = auth_data.get("user") if isinstance(auth_data, dict) else None
    if user and _is_admin_user(user):
        return auth_data
    raise HTTPException(status_code=403, detail="Admin access required")

async def verify_token_optional(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Optionally verify the Supabase JWT token."""
    if not credentials or not credentials.credentials:
        return None
    return await verify_token(credentials)


async def _get_jwks(supabase_url: str) -> dict[str, Any]:
    global _JWKS_CACHE, _JWKS_CACHE_EXP
    now = time.time()
    with _JWKS_LOCK:
        if _JWKS_CACHE and _JWKS_CACHE_EXP and _JWKS_CACHE_EXP > now:
            return _JWKS_CACHE

    url = f"{supabase_url.rstrip('/')}/auth/v1/keys"
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.75)) as client:
        response = await client.get(url)
        response.raise_for_status()
        jwks = response.json()

    with _JWKS_LOCK:
        _JWKS_CACHE = jwks
        _JWKS_CACHE_EXP = now + 300
    return jwks

async def ensure_user_exists(user):
    """Ensure the user exists in the public.users table."""
    supabase = get_supabase_admin()
    if not supabase:
        return
    
    try:
        def _upsert():
            metadata = user.user_metadata or {}
            return supabase.table("users").upsert({
                "id": user.id,
                "email": user.email,
                "full_name": metadata.get("full_name"),
                "avatar_url": metadata.get("avatar_url")
            }).execute()
        
        await asyncio.to_thread(_upsert)
    except Exception as e:
        logger.error("auth_ensure_user_exists_failed", error_type=type(e).__name__)

async def check_is_pro(user_id: str, force_refresh: bool = False) -> bool:
    """Check if a user has pro status in the database."""
    if not user_id:
        return False

    now = time.time()
    if not force_refresh:
        with _PRO_STATE_CACHE_LOCK:
            _prune_pro_cache_locked(now)
            cached = _PRO_STATE_CACHE.get(user_id)
            if cached and cached[1] > now:
                _PRO_STATE_CACHE.move_to_end(user_id)
                return cached[0]

    supabase = get_supabase_admin()
    if not supabase:
        return False
        
    try:
        # Use simple select, admin client bypasses RLS so we can read any user
        response = await asyncio.to_thread(
            lambda: supabase.table("users").select("is_pro").eq("id", user_id).single().execute()
        )
        data = getattr(response, "data", None)
        is_pro = bool(data.get("is_pro", False)) if isinstance(data, dict) else False
        with _PRO_STATE_CACHE_LOCK:
            _prune_pro_cache_locked(now)
            _PRO_STATE_CACHE[user_id] = (is_pro, now + _pro_cache_ttl_seconds())
            _PRO_STATE_CACHE.move_to_end(user_id)
            _prune_pro_cache_locked(now)
        return is_pro
    except Exception as e:
        logger.error(
            "auth_check_is_pro_failed",
            error_type=type(e).__name__,
            user_id_hash=anonymize_user_id(user_id),
        )
        return False
