import asyncio
import threading
import time
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import get_settings
from monitoring import hash_for_monitoring, set_user_context
from supabase import create_client, Client
from supabase_auth.errors import AuthApiError

security = HTTPBearer(auto_error=False)
_PRO_STATE_CACHE: dict[str, tuple[bool, float]] = {}
_PRO_STATE_CACHE_LOCK = threading.Lock()


def _pro_cache_ttl_seconds() -> int:
    try:
        configured_ttl = int(getattr(get_settings(), "pro_state_cache_ttl_seconds", 30) or 30)
    except (ValueError, TypeError):
        configured_ttl = 30
    return min(max(configured_ttl, 1), 30)

def invalidate_pro_cache(user_id: str) -> None:
    if not user_id:
        return
    with _PRO_STATE_CACHE_LOCK:
        _PRO_STATE_CACHE.pop(user_id, None)

def get_supabase() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        print("Warning: Supabase credentials missing during init")
        return None
    return create_client(settings.supabase_url, settings.supabase_anon_key)

def get_supabase_admin() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Warning: Supabase Service Role Key missing")
        return None
    return create_client(settings.supabase_url, settings.supabase_service_role_key)

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verify the Supabase JWT token."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication credentials")
    token = credentials.credentials
    supabase = get_supabase()
    
    if not supabase:
         raise HTTPException(status_code=500, detail="Server configuration error: Auth unavailable")

    try:
        # Verify token by getting the user
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token")

        set_user_context(
            user_id=str(getattr(user_response.user, "id", "") or "") or None,
            email_hash=hash_for_monitoring(str(getattr(user_response.user, "email", "") or "") or None),
            token_hash=hash_for_monitoring(token),
        )
        return {"user": user_response.user, "token": token}
        
    except AuthApiError as e:
        print(f"Auth API Error: {e}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e.message}")
    except Exception as e:
        print(f"Auth Validation Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

async def verify_token_optional(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Optionally verify the Supabase JWT token."""
    if not credentials or not credentials.credentials:
        return None
    return await verify_token(credentials)

async def ensure_user_exists(user):
    """Ensure the user exists in the public.users table."""
    supabase = get_supabase_admin()
    if not supabase:
        return
    
    try:
        def _upsert():
            return supabase.table("users").upsert({
                "id": user.id,
                "email": user.email,
                "full_name": user.user_metadata.get("full_name"),
                "avatar_url": user.user_metadata.get("avatar_url")
            }).execute()
        
        await asyncio.to_thread(_upsert)
    except Exception as e:
        print(f"Failed to ensure user exists: {e}")

async def check_is_pro(user_id: str, force_refresh: bool = False) -> bool:
    """Check if a user has pro status in the database."""
    if not user_id:
        return False

    now = time.time()
    if not force_refresh:
        with _PRO_STATE_CACHE_LOCK:
            cached = _PRO_STATE_CACHE.get(user_id)
            if cached and cached[1] > now:
                return cached[0]

    supabase = get_supabase_admin()
    if not supabase:
        return False
        
    try:
        # Use simple select, admin client bypasses RLS so we can read any user
        response = await asyncio.to_thread(
            supabase.table("users").select("is_pro").eq("id", user_id).single().execute
        )
        data = getattr(response, "data", None)
        is_pro = bool(data.get("is_pro", False)) if isinstance(data, dict) else False
        with _PRO_STATE_CACHE_LOCK:
            _PRO_STATE_CACHE[user_id] = (is_pro, now + _pro_cache_ttl_seconds())
        return is_pro
    except Exception as e:
        print(f"Failed to check pro status: {e}")
        return False
