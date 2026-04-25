"""Webhook compatibility routes for DepthAPI."""

import os

from fastapi import APIRouter, HTTPException, Request

from api.auth import get_supabase_admin
from api.config import get_settings
from routers.payments import dodo_webhook as payments_dodo_webhook
from routers.payments import process_dodo_webhook_payload

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/dodo")
async def dodo_webhook(request: Request):
    """Backward-compatible webhook endpoint that delegates to /api/payments/webhook/dodo."""
    return await payments_dodo_webhook(request=request)


@router.post("/webhooks/dodo/dev-replay")
async def dodo_webhook_dev(payload: dict, request: Request):
    """Dev-only webhook replay endpoint (disabled in production).
    
    Requires:
      1. ENVIRONMENT is not "production"
      2. Request originates from loopback (127.0.0.1/::1)
      3. X-Dev-Replay-Secret header matches DEV_REPLAY_SECRET env var
    """
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")

    # Restrict to loopback only
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Replay endpoint restricted to localhost")

    # Require admin secret
    expected_secret = os.getenv("DEV_REPLAY_SECRET", "")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="Replay endpoint not configured")
    provided_secret = request.headers.get("x-dev-replay-secret", "")
    if not provided_secret or provided_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid replay secret")

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase configuration missing")
    return process_dodo_webhook_payload(payload, supabase)
