from __future__ import annotations

import os
from typing import Any

import httpx

from api.config import get_settings
from api.logging_config import logger
from services.cache import cache_get, cache_set


def _resolve_sentry_config() -> tuple[str, str, str]:
    settings = get_settings()
    token = (getattr(settings, "sentry_auth_token", "") or os.getenv("SENTRY_AUTH_TOKEN", "")).strip()
    org_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    project_slug = os.getenv("SENTRY_PROJECT_SLUG", "").strip()
    return token, org_slug, project_slug
    return token, org_slug, project_slug


async def fetch_sentry_issues(limit: int = 10, cache_ttl_seconds: int = 300) -> list[dict[str, Any]]:
    token, org_slug, project_slug = _resolve_sentry_config()
    cache_key = f"sentry:issues:{org_slug}:{project_slug}:{limit}"
    cached = await cache_get(cache_key)
    if cached and isinstance(cached.get("issues"), list):
        return cached["issues"]

    if not token:
        logger.warning("sentry_api_token_missing")
        return []

    url = f"https://sentry.io/api/0/projects/{org_slug}/{project_slug}/issues/"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": str(limit), "sort": "freq"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.error("sentry_issues_fetch_failed", error=str(exc))
        return []

    issues: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            issues.append(
                {
                    "id": item.get("id"),
                    "short_id": item.get("shortId"),
                    "title": item.get("title"),
                    "permalink": item.get("permalink"),
                    "count": item.get("count"),
                    "level": item.get("level"),
                    "first_seen": item.get("firstSeen"),
                    "last_seen": item.get("lastSeen"),
                    "status": item.get("status"),
                }
            )

    try:
        await cache_set(cache_key, {"issues": issues}, ttl=cache_ttl_seconds)
    except Exception as exc:
        logger.warning("sentry_cache_set_failed", error=str(exc))

    return issues
