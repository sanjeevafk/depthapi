"""Authentication utilities for DepthAPI."""

import asyncio
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Any, Optional

import structlog
from supabase import create_client, Client
from config import get_settings
from logging_config import anonymize_user_id
from services.cache import get_redis
from services.redis_safe import safe_redis_call

logger = structlog.get_logger(__name__)

@lru_cache(maxsize=1)
def get_supabase() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        logger.warning("auth_supabase_credentials_missing")
        return None
    return create_client(settings.supabase_url, settings.supabase_publishable_key)


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.warning("auth_supabase_secret_key_missing")
        return None
    secret_key = settings.supabase_secret_key
    if hasattr(secret_key, "get_secret_value"):
        secret_key = secret_key.get_secret_value()
    return create_client(settings.supabase_url, secret_key) # type: ignore
