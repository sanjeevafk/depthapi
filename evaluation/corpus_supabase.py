import os
from typing import Any

import httpx


def local_supabase_client() -> tuple[str, dict[str, str]]:
    base = (os.environ.get("LOCAL_PGVECTOR_URL") or os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("LOCAL_PGVECTOR_SECRET_KEY") or os.environ.get("SUPABASE_SECRET_KEY") or ""
    if not base or not key:
        raise RuntimeError("LOCAL_PGVECTOR_URL and LOCAL_PGVECTOR_SECRET_KEY are required")
    return base, {"apikey": key, "Authorization": f"Bearer {key}"}


def rest_get(table: str, *, params: dict[str, Any] | None = None, count: bool = False) -> httpx.Response:
    base, headers = local_supabase_client()
    h = dict(headers)
    if count:
        h["Prefer"] = "count=exact"
    return httpx.get(f"{base}/rest/v1/{table}", headers=h, params=params or {}, timeout=60)


def rest_rpc(name: str, payload: dict[str, Any]) -> httpx.Response:
    base, headers = local_supabase_client()
    return httpx.post(f"{base}/rest/v1/rpc/{name}", headers=headers, json=payload, timeout=120)
