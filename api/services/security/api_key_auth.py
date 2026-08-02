"""API-key authentication against local PostgreSQL."""
from dataclasses import dataclass
import hashlib
from fastapi import Header, HTTPException
from api.adapters.pg_adapter import fetch_one

@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    plan: str
    is_pro: bool

async def _lookup_in_db(key_hash: str) -> ApiKeyRecord | None:
    row = await fetch_one("api_keys", {"key_hash": key_hash, "is_active": True})
    if row is None:
        return None
    plan = str(row.get("plan") or "free")
    return ApiKeyRecord(str(row["id"]), plan, plan in {"pro", "enterprise"})

async def verify_api_key(authorization: str | None = Header(default=None)) -> ApiKeyRecord:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer API key required")
    raw_key = authorization[7:].strip()
    record = await _lookup_in_db(hashlib.sha256(raw_key.encode()).hexdigest())
    if record is None:
        raise HTTPException(401, "Invalid API key")
    return record
