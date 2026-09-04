"""API-key authentication against local PostgreSQL."""
import hashlib
import hmac
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

async def _lookup_in_db(key_hash: str) -> ApiKeyRecord | None:
    row = await fetch_one("api_keys", {"key_hash": key_hash, "is_active": True})
    if row is None:
        return None
    stored_hash = str(row.get("key_hash") or "")
    if not stored_hash or not hmac.compare_digest(stored_hash, key_hash):
        return None
    # Optional expiry / revocation (added by 003 migration; absent on old DBs -> None).
    expires_at = row.get("expires_at")
    if expires_at is not None:
        try:
            exp = expires_at
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= datetime.now(timezone.utc):
                return None
        except Exception:
            return None
    if row.get("revoked_at") is not None:
        return None
    plan = str(row.get("plan") or "free")
    scopes_raw = row.get("scopes") or []
    scopes = tuple(str(s) for s in scopes_raw) if isinstance(scopes_raw, (list, tuple)) else tuple()
    return ApiKeyRecord(str(row["id"]), plan, plan in {"pro", "enterprise"}, scopes, expires_at)

async def verify_api_key(authorization: str | None = Header(default=None)) -> ApiKeyRecord:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer API key required")
    raw_key = authorization[7:].strip()
    record = await _lookup_in_db(hashlib.sha256(raw_key.encode()).hexdigest())
    if record is None:
        raise HTTPException(401, "Invalid API key")
    return record
