"""Webhook compatibility routes for payment processing."""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from auth import get_supabase_admin
from config import get_settings
from routers.payments import dodo_webhook as payments_dodo_webhook
from routers.payments import process_dodo_webhook_payload, verify_dodo_signature

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/dodo")
async def dodo_webhook(request: Request, x_dodo_signature: Optional[str] = Header(default=None)):
    """Backward-compatible webhook endpoint that delegates to `/api/payments/webhook/dodo`."""
    return await payments_dodo_webhook(request=request, x_dodo_signature=x_dodo_signature)


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
