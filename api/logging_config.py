"""Structured logging configuration."""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import sys
import uuid
from typing import Any, Mapping

import structlog
from structlog.typing import EventDict

from config import get_settings

_REDACTED_VALUE = "[REDACTED]"
_DEFAULT_SUCCESS_SAMPLE_RATE = 0.15
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "headers",
    "cookie",
    "access_token",
    "refresh_token",
    "id_token",
}
_SENSITIVE_EXACT_KEYS = {
    "prompt",
    "content",
}
_REQUEST_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_USER_HASH_SALT_ENV = "LOG_USER_HASH_SALT"
_user_hash_salt_cache: str | None = None


def _get_user_hash_salt() -> str:
    global _user_hash_salt_cache
    if _user_hash_salt_cache:
        return _user_hash_salt_cache

    salt = (os.getenv(_USER_HASH_SALT_ENV) or "").strip()
    settings = None
    if not salt:
        settings = get_settings()
        salt = str(settings.log_user_hash_salt or "").strip()
    if not salt:
        env = ""
        if settings is not None:
            env = str(settings.environment or "")
        else:
            env = os.getenv("ENVIRONMENT", "")
        env = env.strip().lower()
        if env and env not in {"production", "prod"}:
            salt = uuid.uuid4().hex
            os.environ[_USER_HASH_SALT_ENV] = salt
            _user_hash_salt_cache = salt
            print(
                f"Warning: {_USER_HASH_SALT_ENV} not set; using ephemeral dev salt",
                file=sys.stderr,
            )
            return salt
        raise RuntimeError(
            f"{_USER_HASH_SALT_ENV} must be set to a non-empty value for secure user-id anonymization"
        )

    _user_hash_salt_cache = salt
    return salt


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): (_REDACTED_VALUE if _looks_sensitive_key(str(k)) else _sanitize_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    return value


def redact_sensitive_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Redact sensitive payloads before rendering logs."""
    return {
        str(key): (_REDACTED_VALUE if _looks_sensitive_key(str(key)) else _sanitize_value(value))
        for key, value in event_dict.items()
    }


def generate_request_id() -> str:
    """Return a new UUID4 request ID string."""
    return str(uuid.uuid4())


def is_valid_request_id(value: str | None) -> bool:
    """Validate user-supplied request ID format."""
    return bool(value and _REQUEST_ID_PATTERN.match(value.strip()))


def anonymize_user_id(user_id: str | None) -> str | None:
    """Hash user IDs for log-safe correlation without exposing raw identifiers."""
    if not user_id:
        return None
    salt = _get_user_hash_salt()
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()
    return digest[:16]


def anonymize_text(value: str | None) -> str | None:
    """Hash user-provided text for correlation without storing raw content."""
    if not value:
        return None
    salt = _get_user_hash_salt()
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return digest[:16]


def should_sample_success(sample_rate: float | None = None) -> bool:
    """Sample successful observability events to control log volume."""
    effective_rate = sample_rate
    if effective_rate is None:
        env_rate = os.getenv("LOG_SUCCESS_SAMPLE_RATE", "")
        try:
            effective_rate = float(env_rate) if env_rate else _DEFAULT_SUCCESS_SAMPLE_RATE
        except ValueError:
            effective_rate = _DEFAULT_SUCCESS_SAMPLE_RATE

    bounded = max(0.0, min(float(effective_rate), 1.0))
    return random.random() < bounded


def log_sampled_success(event: str, *, sample_rate: float | None = None, **fields: Any) -> None:
    """Emit sampled success logs; use direct logger.error for all failures."""
    if should_sample_success(sample_rate=sample_rate):
        logger.info(event, **fields)

def setup_logging():
    """Configure structured logging."""
    _get_user_hash_salt()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.CallsiteParameterAdder(
                {
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                }
            ),
            redact_sensitive_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

logger = structlog.get_logger()
