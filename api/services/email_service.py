"""Resend email integration."""

from __future__ import annotations

import structlog
import httpx
import asyncio
from typing import Any

from config import get_settings
from auth import get_supabase_admin

logger = structlog.get_logger()

RESEND_API_URL = "https://api.resend.com/emails"


async def _log_email(
    *,
    to: str,
    template: str,
    status: str,
    provider_message_id: str | None = None,
    error: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    supabase = get_supabase_admin()
    if not supabase:
        return

    payload = {
        "to_email": to,
        "template": template,
        "status": status,
        "provider": "resend",
        "provider_message_id": provider_message_id,
        "error": error,
        "user_id": user_id,
        "event_type": event_type,
        "metadata": metadata or {},
    }
    try:
        await asyncio.to_thread(lambda: supabase.table("email_logs").insert(payload).execute())
    except Exception as exc:
        logger.error("email_log_failed", error=str(exc))


async def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    template: str = "unknown",
    user_id: str | None = None,
    event_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    settings = get_settings()
    api_key = ""
    if settings.resend_api_key:
        api_key = settings.resend_api_key.get_secret_value().strip()
    sender = (settings.resend_from or "").strip()

    if not api_key:
        logger.warning("resend_missing_api_key")
        await _log_email(
            to=to,
            template=template,
            status="skipped",
            error="missing_api_key",
            user_id=user_id,
            event_type=event_type,
            metadata=metadata,
        )
        return False
    if not sender:
        logger.warning("resend_missing_from_address")
        await _log_email(
            to=to,
            template=template,
            status="skipped",
            error="missing_from_address",
            user_id=user_id,
            event_type=event_type,
            metadata=metadata,
        )
        return False

    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            response = await client.post(RESEND_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            message_id = None
            try:
                body = response.json()
                message_id = body.get("id") if isinstance(body, dict) else None
            except ValueError:
                message_id = None
            await _log_email(
                to=to,
                template=template,
                status="sent",
                provider_message_id=message_id,
                user_id=user_id,
                event_type=event_type,
                metadata=metadata,
            )
            return True
    except httpx.HTTPError as exc:
        logger.error("resend_send_failed", error=str(exc))
        await _log_email(
            to=to,
            template=template,
            status="failed",
            error=str(exc),
            user_id=user_id,
            event_type=event_type,
            metadata=metadata,
        )
        return False
