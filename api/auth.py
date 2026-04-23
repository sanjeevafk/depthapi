"""Authentication utilities for DepthAPI."""

from functools import lru_cache
from typing import Any, Optional

import structlog
from api.config import get_settings
from api.adapters.supabase_adapter import SupabaseHTTPClient

logger = structlog.get_logger(__name__)

@lru_cache(maxsize=1)
def get_supabase() -> Optional[SupabaseHTTPClient]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        logger.warning("auth_supabase_credentials_missing")
        return None
    
    key = settings.supabase_publishable_key
    if hasattr(key, "get_secret_value"):
        key = key.get_secret_value()
        
    return SupabaseHTTPClient(settings.supabase_url, str(key))


@lru_cache(maxsize=1)
def get_supabase_admin() -> Optional[SupabaseHTTPClient]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.warning("auth_supabase_secret_key_missing")
        return None
        
    secret_key = settings.supabase_secret_key
    if hasattr(secret_key, "get_secret_value"):
        secret_key = secret_key.get_secret_value()
        
    return SupabaseHTTPClient(settings.supabase_url, str(secret_key), is_admin=True)


async def ensure_user_exists(user: Any) -> None:
    """Best-effort user upsert for compatibility with history/chat persistence."""
    supabase = get_supabase_admin()
    if not supabase:
        raise RuntimeError("Database connection unavailable")

    user_id = str(getattr(user, "id", "") or "").strip()
    if not user_id:
        raise ValueError("User ID is required")

    payload: dict[str, Any] = {"id": user_id}
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        payload["email"] = email

    display_name = str(getattr(user, "display_name", "") or getattr(user, "name", "") or "").strip()
    if display_name:
        payload["display_name"] = display_name

    response = await supabase.table("users").upsert(payload, on_conflict="id").execute()
    if getattr(response, "error", None):
        raise RuntimeError(f"user_upsert_failed: {response.error}")
