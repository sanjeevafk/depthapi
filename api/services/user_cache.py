import time

from api.auth import get_supabase_admin
from api.logging_config import anonymize_user_id, logger
from api.services.cache import get_redis
from api.services.redis_safe import safe_redis_call


async def refresh_is_pro_cache(user_id: str, *, ttl_seconds: int = 900) -> None:
    if not user_id:
        return
    supabase = get_supabase_admin()
    if not supabase:
        return
    try:
        response = await supabase.table("users").select("is_pro").eq("id", user_id).single().execute()
        data = getattr(response, "data", None)
        is_pro = bool(data.get("is_pro", False)) if isinstance(data, dict) else False
        redis = await safe_redis_call(get_redis, operation="connect")
        if redis is not None:
            await safe_redis_call(
                redis.setex,
                f"knowbear:user:is_pro:{user_id}",
                ttl_seconds,
                "1" if is_pro else "0",
                operation="setex",
            )
    except Exception as exc:
        logger.warning(
            "user_cache_refresh_failed",
            user_id_hash=anonymize_user_id(user_id),
            error=str(exc),
        )
