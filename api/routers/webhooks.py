"""Webhook compatibility routes for payment processing."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from auth import get_supabase_admin
from config import get_settings
from routers.payments import dodo_webhook as payments_dodo_webhook
from routers.payments import process_dodo_webhook_payload
from services.email_service import send_email
from services.email_templates import build_welcome_email

router = APIRouter(tags=["webhooks"])


def _extract_supabase_email(payload: dict) -> tuple[str | None, str | None]:
    record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    email = payload.get("email") or record.get("email") or user.get("email")
    metadata = record.get("raw_user_meta_data") if isinstance(record.get("raw_user_meta_data"), dict) else {}
    name = metadata.get("full_name") or user.get("user_metadata", {}).get("full_name")
    if isinstance(email, str) and email.strip():
        return email.strip(), name if isinstance(name, str) else None
    return None, name if isinstance(name, str) else None


@router.post("/webhooks/dodo")
async def dodo_webhook(request: Request, background_tasks: BackgroundTasks):
    """Backward-compatible webhook endpoint that delegates to `/api/payments/webhook/dodo`."""
    return await payments_dodo_webhook(request=request, background_tasks=background_tasks)


@router.post("/webhooks/dodo/dev-replay")
async def dodo_webhook_dev(payload: dict):
    """Dev-only webhook replay endpoint (disabled in production)."""
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase configuration missing")
    return process_dodo_webhook_payload(payload, supabase)


@router.post("/webhooks/supabase/auth")
async def supabase_auth_webhook(payload: dict, request: Request, background_tasks: BackgroundTasks):
    """Optional Supabase Auth webhook for welcome emails."""
    settings = get_settings()
    secret = settings.supabase_auth_webhook_secret
    if secret:
        incoming = request.headers.get("x-webhook-secret")
        if incoming != secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    email, name = _extract_supabase_email(payload)
    if not email:
        raise HTTPException(status_code=400, detail="Missing email in webhook payload")

    content = build_welcome_email(settings.site_name, settings.support_email, name)
    background_tasks.add_task(
        send_email,
        to=email,
        subject=content.subject,
        html=content.html,
        text=content.text,
        template="welcome",
        event_type="supabase.auth",
    )
    return {"queued": True}
