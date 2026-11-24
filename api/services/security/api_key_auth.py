"""API-key authentication against local PostgreSQL."""
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from api.adapters.pg_adapter import fetch_one


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    plan: str
    is_pro: bool
    scopes: tuple[str, ...] = field(default_factory=tuple)
    expires_at: datetime | None = None


_KEY_CACHE_TTL_SECONDS = 60.0
_KEY_CACHE_MAX_ENTRIES = 1024
_key_cache: dict[str, tuple[float, ApiKeyRecord]] = {}


def _is_expired(expires_at: datetime | str | None) -> bool:
    if expires_at is None:
        return False
    try:
        exp = datetime.fromisoformat(expires_at) if isinstance(expires_at, str) else expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)
    except Exception:
        return True


async def _lookup_in_db(key_hash: str) -> ApiKeyRecord | None:
    now = time.monotonic()
    cached = _key_cache.get(key_hash)
    if cached is not None:
        cached_at, cached_record = cached
        if now - cached_at < _KEY_CACHE_TTL_SECONDS and not _is_expired(cached_record.expires_at):
            return cached_record
        _key_cache.pop(key_hash, None)

    row = await fetch_one("api_keys", {"key_hash": key_hash, "is_active": True})
    if row is None:
        return None
    stored_hash = str(row.get("key_hash") or "")
    if not stored_hash or not hmac.compare_digest(stored_hash, key_hash):
        return None
    # Optional expiry / revocation (added by 003 migration; absent on old DBs -> None).
    expires_at = row.get("expires_at")
    if _is_expired(expires_at):
        return None
    if row.get("revoked_at") is not None:
        return None
    plan = str(row.get("plan") or "free")
    scopes_raw = row.get("scopes") or []
    scopes = tuple(str(s) for s in scopes_raw) if isinstance(scopes_raw, (list, tuple)) else tuple()
    record = ApiKeyRecord(str(row["id"]), plan, plan in {"pro", "enterprise"}, scopes, expires_at)
    if len(_key_cache) >= _KEY_CACHE_MAX_ENTRIES:
        _key_cache.clear()
    _key_cache[key_hash] = (now, record)
    return record

async def verify_api_key(authorization: str | None = Header(default=None)) -> ApiKeyRecord:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer API key required")
    raw_key = authorization[7:].strip()
    record = await _lookup_in_db(hashlib.sha256(raw_key.encode()).hexdigest())
    if record is None:
        raise HTTPException(401, "Invalid API key")
    return record
