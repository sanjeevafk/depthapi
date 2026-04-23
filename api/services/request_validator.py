"""Message request boundary validation and ingress de-duplication.

Responsibilities:
- Validate payload shape/content bounds for `/messages`.
- Normalize incoming mode and prompt-adjacent request fields.
- Provide short-lived de-duplication keys to prevent duplicate ingress work.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

import api.services.cache as cache_module
from api.services.redis_safe import safe_redis_call
from api.services.message_utils import normalize_mode


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    content: str = ""
    normalized_mode: str | None = None
    error_message: str | None = None


class RequestValidator:
    """Validate message payload boundaries and detect duplicate ingress requests."""

    def __init__(self, *, dedup_ttl_seconds: float = 3.0) -> None:
        self._dedup_ttl_seconds = max(float(dedup_ttl_seconds), 1.0)

    @staticmethod
    def require_uuid(value: str | None, field_name: str) -> str:
        if not value:
            raise ValueError(f"{field_name} is required")
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a UUID") from exc

    def validate_message_request(self, payload: Any) -> ValidationResult:
        if not isinstance(payload, dict):
            return ValidationResult(ok=False, error_message="Request body must be a JSON object")
        if "user_id" in payload:
            return ValidationResult(ok=False, error_message="user_id must not be supplied by the client")

        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return ValidationResult(ok=False, error_message="Content is required")

        mode_raw = payload.get("mode")
        normalized_mode: str | None = None
        if mode_raw is not None:
            try:
                normalized_mode = normalize_mode(mode_raw)
            except ValueError:
                return ValidationResult(ok=False, error_message="Invalid mode")

        return ValidationResult(ok=True, content=content.strip(), normalized_mode=normalized_mode)

    def generate_dedup_key(self, message_id: str) -> str:
        digest = hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
        return f"knowbear:messages:ingress_dedup:{digest}"

    async def check_deduplication(self, message_id: str, ttl_seconds: float | None = None) -> bool:
        ttl = max(int(ttl_seconds or self._dedup_ttl_seconds), 1)
        key = self.generate_dedup_key(message_id)

        redis = await safe_redis_call(cache_module.get_redis, operation="connect")
        if redis is None:
            return True

        created = await safe_redis_call(
            redis.set_if_not_exists,
            key,
            ttl,
            str(int(time.time())),
            operation="set_if_not_exists",
        )
        if created is None:
            return True
        return bool(created)

    async def is_duplicate(self, key: str) -> bool:
        redis = await safe_redis_call(cache_module.get_redis, operation="connect")
        if redis is None:
            return False
        raw = await safe_redis_call(redis.get, key, operation="get")
        return raw is not None

    async def clear_deduplication(self, message_id: str) -> None:
        key = self.generate_dedup_key(message_id)
        redis = await safe_redis_call(cache_module.get_redis, operation="connect")
        if redis is None:
            return
        await safe_redis_call(redis.delete, key, operation="delete")
