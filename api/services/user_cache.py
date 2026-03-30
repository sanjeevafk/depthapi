import asyncio
import time

from auth import get_supabase_admin
from logging_config import logger
from services.cache import get_redis


async def refresh_is_pro_cache(user_id: str, *, ttl_seconds: int = 900) -> None:
    if not user_id:
        return
    supabase = get_supabase_admin()
    if not supabase:
        return
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("users").select("is_pro").eq("id", user_id).single().execute()
        )
        data = getattr(response, "data", None)
        is_pro = bool(data.get("is_pro", False)) if isinstance(data, dict) else False
        redis = await get_redis()
        await redis.setex(f"knowbear:user:is_pro:{user_id}", ttl_seconds, "1" if is_pro else "0")
    except Exception as exc:
        logger.warning("user_cache_refresh_failed", user_id=user_id, error=str(exc))
