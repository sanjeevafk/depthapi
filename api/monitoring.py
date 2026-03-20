"""Sentry monitoring and telemetry helpers with privacy-safe defaults."""

from __future__ import annotations

import os
import re
import hashlib
from typing import Any

from logging_config import logger

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
except Exception:  # pragma: no cover - dependency may be absent in local dev
    sentry_sdk = None  # type: ignore[assignment]
    FastApiIntegration = None  # type: ignore[assignment]


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "password",
    "secret",
    "cookie",
    "api_key",
    "headers",
    "email",
)
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(bearer\s+)[a-z0-9._\-]+", re.IGNORECASE)
_NOISE_MESSAGES = (
    "client disconnected",
    "cancellederror",
    "stream read timed out",
)


_sentry_ready = False


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_sample_rate(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def hash_for_monitoring(value: str | None) -> str | None:
    """Return a short, non-reversible hash for monitoring contexts."""
    if not value:
        return None
    return _hash_value(value)


def _looks_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        scrubbed = _EMAIL_PATTERN.sub(_REDACTED, value)
        scrubbed = _BEARER_PATTERN.sub(r"\1[REDACTED]", scrubbed)
        return scrubbed
    return value


def redact_pii(value: Any) -> Any:
    """Recursively redact PII and secrets before shipping telemetry."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, inner in value.items():
            key_str = str(key)
            if _looks_sensitive(key_str):
                output[key_str] = _REDACTED
            else:
                output[key_str] = redact_pii(inner)
        return output
    if isinstance(value, list):
        return [redact_pii(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_pii(item) for item in value)
    return _sanitize_scalar(value)


def _before_send(event: Any, _hint: Any) -> Any:
    scrubbed = redact_pii(event)
    message_value = scrubbed.get("message") if isinstance(scrubbed, dict) else None
    if isinstance(message_value, str) and any(noise in message_value.lower() for noise in _NOISE_MESSAGES):
        return None

    exception_values = (
        scrubbed.get("exception", {}).get("values", [])
        if isinstance(scrubbed, dict)
        else []
    )
    if isinstance(exception_values, list):
        for value in exception_values:
            if not isinstance(value, dict):
                continue
            exc_type = str(value.get("type") or "").lower()
            exc_value = str(value.get("value") or "").lower()
            if exc_type == "httpexception" and ("401" in exc_value or "403" in exc_value or "404" in exc_value):
                return None

    user = scrubbed.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        user.pop("email", None)
    request_data = scrubbed.get("request")
    if isinstance(request_data, dict):
        request_data["headers"] = _REDACTED
        if "cookies" in request_data:
            request_data["cookies"] = _REDACTED
        url = request_data.get("url")
        if isinstance(url, str) and "?" in url:
            request_data["url"] = url.split("?", 1)[0]
    return scrubbed


def _before_breadcrumb(crumb: Any, _hint: Any) -> Any:
    return redact_pii(crumb)


def init_sentry(settings: Any) -> bool:
    """Initialize Sentry when enabled (default) and DSN is configured."""
    global _sentry_ready

    dsn = str(getattr(settings, "sentry_dsn", "") or os.getenv("SENTRY_DSN", "")).strip()
    enabled_value = getattr(settings, "sentry_enabled", os.getenv("SENTRY_ENABLED", "true"))
    enabled = _parse_bool(enabled_value, True)

    if not enabled:
        logger.info("sentry_disabled_by_env")
        _sentry_ready = False
        return False

    if not dsn:
        logger.info("sentry_disabled_missing_dsn")
        _sentry_ready = False
        return False

    if sentry_sdk is None:
        logger.warning("sentry_sdk_not_installed", dsn_present=True)
        _sentry_ready = False
        return False

    traces_rate = _parse_sample_rate(
        getattr(settings, "sentry_traces_sample_rate", os.getenv("SENTRY_TRACES_SAMPLE_RATE", 0.1)),
        0.1,
    )
    profiles_rate = _parse_sample_rate(
        getattr(settings, "sentry_profiles_sample_rate", os.getenv("SENTRY_PROFILES_SAMPLE_RATE", 0.0)),
        0.0,
    )
    environment = str(getattr(settings, "environment", "development") or "development")
    if environment == "production" and traces_rate >= 1.0:
        traces_rate = 0.2
    release = str(
        getattr(settings, "sentry_release", "")
        or os.getenv("SENTRY_RELEASE", "")
    ).strip() or None

    integrations = [FastApiIntegration()] if FastApiIntegration is not None else []
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_rate,
        profiles_sample_rate=profiles_rate,
        send_default_pii=False,
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
        integrations=integrations,
    )
    _sentry_ready = True
    logger.info("sentry_initialized", environment=str(getattr(settings, "environment", "development")))
    return True


def sentry_is_enabled() -> bool:
    return _sentry_ready


def capture_exception(exc: Exception, **context: Any) -> None:
    """Capture backend exceptions with redacted contextual fields."""
    if not _sentry_ready or sentry_sdk is None:
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in redact_pii(context).items():
            scope.set_extra(str(key), value)
        sentry_sdk.capture_exception(exc)


def capture_telemetry_event(event_name: str, **payload: Any) -> None:
    """Send low-volume telemetry events to Sentry with redaction."""
    if not _sentry_ready or sentry_sdk is None:
        return
    scrubbed = redact_pii(payload)
    with sentry_sdk.new_scope() as scope:
        scope.set_level("info")
        scope.set_tag("telemetry_event", event_name)
        for key, value in scrubbed.items():
            scope.set_extra(str(key), value)
        sentry_sdk.capture_message(f"telemetry.{event_name}")


def continue_trace_from_headers(headers: dict[str, str]) -> None:
    """Continue incoming distributed trace when propagation headers are present."""
    if not _sentry_ready or sentry_sdk is None:
        return
    sentry_trace = headers.get("sentry-trace")
    baggage = headers.get("baggage")
    if not sentry_trace:
        return
    sentry_sdk.continue_trace({"sentry-trace": sentry_trace, "baggage": baggage})


def set_request_context(*, request_id: str | None, path: str | None, method: str | None, client_ip: str | None) -> None:
    """Attach request metadata onto the active Sentry scope."""
    if not _sentry_ready or sentry_sdk is None:
        return
    scope = sentry_sdk.get_isolation_scope()
    if request_id:
        scope.set_tag("request_id", request_id)
    if path:
        scope.set_tag("path", path)
    if method:
        scope.set_tag("method", method)
    if client_ip:
        scope.set_extra("client_ip_hash", _hash_value(client_ip))


def set_user_context(
    *,
    user_id: str | None,
    email_hash: str | None = None,
    token_hash: str | None = None,
) -> None:
    """Attach authenticated user/session context to Sentry scope.

    Expects hashed inputs only; do not pass plaintext PII or secrets.
    """
    if not _sentry_ready or sentry_sdk is None:
        return

    if user_id:
        user_data: dict[str, Any] = {"id": user_id}
        if email_hash:
            user_data["email_hash"] = email_hash
        sentry_sdk.set_user(user_data)
    else:
        sentry_sdk.set_user(None)

    if token_hash:
        scope = sentry_sdk.get_isolation_scope()
        scope.set_context("session", {"token_hash": token_hash})
