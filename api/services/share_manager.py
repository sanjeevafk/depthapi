import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from logging_config import logger

_ACCESS_LEVELS = {"public"}
_SHARE_KINDS = {"response", "conversation"}
_PASSWORD_PREFIX = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 120_000


def generate_share_token() -> str:
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8").rstrip("=")
    derived_b64 = base64.urlsafe_b64encode(derived).decode("utf-8").rstrip("=")
    return f"{_PASSWORD_PREFIX}${_PASSWORD_ITERATIONS}${salt_b64}${derived_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    parts = password_hash.split("$")
    if len(parts) != 4:
        return False
    prefix, iterations_raw, salt_b64, derived_b64 = parts
    if prefix != _PASSWORD_PREFIX:
        return False
    try:
        iterations = int(iterations_raw)
    except ValueError:
        return False

    salt = _decode_b64(salt_b64)
    derived = _decode_b64(derived_b64)
    if salt is None or derived is None:
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(candidate, derived)


def _decode_b64(value: str) -> Optional[bytes]:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, binascii.Error):
        return None


def normalize_access_level(access_level: str) -> str:
    normalized = str(access_level or "").strip().lower() or "public"
    if normalized not in _ACCESS_LEVELS:
        raise ValueError("Only public shares are supported")
    return normalized


def normalize_share_kind(share_kind: str) -> str:
    normalized = str(share_kind or "").strip().lower() or "response"
    if normalized not in _SHARE_KINDS:
        raise ValueError("Invalid share kind")
    return normalized


def compute_expires_at(expiry_days: Optional[int]) -> Optional[datetime]:
    if not expiry_days:
        return None
    try:
        days = int(expiry_days)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)


def is_expired(share: dict[str, Any]) -> bool:
    expires_at = share.get("expires_at")
    if not expires_at:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)
    return False


def verify_share_access(
    share: dict[str, Any],
    user_id: Optional[str],
    password: Optional[str],
) -> bool:
    access_level = str(share.get("access_level") or "").lower()
    return access_level == "public"


def increment_view_count(supabase, share: dict[str, Any]) -> None:
    share_id = share.get("id")
    if not share_id:
        return
    try:
        supabase.rpc("increment_shared_response_view", {"share_id": str(share_id)}).execute()
    except Exception as exc:
        logger.warning("share_increment_view_failed", error=str(exc))


def ensure_unique_token(supabase, *, max_attempts: int = 4) -> str:
    for _ in range(max_attempts):
        token = generate_share_token()
        try:
            response = supabase.table("shared_responses").select("id").eq("share_token", token).limit(1).execute()
        except Exception as exc:
            logger.warning("share_token_lookup_failed", error=str(exc))
            raise
        data = response.data or []
        if not data:
            return token
    return generate_share_token()


def create_share(supabase, payload: dict[str, Any]) -> dict[str, Any]:
    response = supabase.table("shared_responses").insert(payload).execute()
    data = response.data or []
    if not data:
        raise RuntimeError("Share insert failed")
    if isinstance(data, list):
        return data[0]
    return data


def fetch_share_by_token(supabase, token: str) -> Optional[dict[str, Any]]:
    response = supabase.table("shared_responses").select("*").eq("share_token", token).limit(1).execute()
    data = response.data or []
    if isinstance(data, list):
        return data[0] if data else None
    return data


def fetch_share_by_id(supabase, share_id: str) -> Optional[dict[str, Any]]:
    response = supabase.table("shared_responses").select("*").eq("id", share_id).limit(1).execute()
    data = response.data or []
    if isinstance(data, list):
        return data[0] if data else None
    return data
