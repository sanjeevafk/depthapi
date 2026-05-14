"""Payment processing endpoints for DepthAPI."""

from __future__ import annotations

import binascii
import hashlib
import json
import urllib.parse
from datetime import datetime
from typing import Any, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel
from standardwebhooks import Webhook, WebhookVerificationError

from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key, PLAN_MONTHLY_BUDGETS
from api.auth import get_supabase_admin
from api.config import get_settings
from api.services.security.rate_limit import check_rate_limit
from api.services.infra.redis_safe import safe_redis_command

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["payments"])

WEBHOOK_IDEMPOTENCY_TTL_SECONDS = 60 * 60 * 24


class CheckoutRequest(BaseModel):
    """Request model for creating a checkout session."""
    plan: str = "starter"  # Starter, Pro, Enterprise
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    """Response model for checkout session."""
    checkout_url: str
    session_id: str


class PaymentWebhookResult(BaseModel):
    """Structured result for payment webhook processing."""
    acknowledged: bool = True
    event: str
    event_id: str
    duplicate: bool = False
    state: str
    api_key_id: Optional[str] = None
    message: str


def _normalize_webhook_headers(request: Request) -> dict[str, str]:
    """Collect Standard Webhooks headers (case-insensitive)."""
    h = request.headers
    out: dict[str, str] = {}
    for key in ("webhook-id", "webhook-signature", "webhook-timestamp"):
        val = h.get(key)
        if val is None:
            for k, v in h.items():
                if k.lower() == key:
                    val = v
                    break
        if val is not None:
            out[key] = val
    return out


def verify_and_parse_dodo_webhook(body: bytes, headers: dict[str, str], secret: str) -> dict[str, Any]:
    """Verify Dodo webhook and return parsed JSON."""
    if not secret:
        raise WebhookVerificationError("Missing webhook secret")
    wh = Webhook(secret)
    try:
        parsed = wh.verify(body.decode("utf-8"), dict(headers))
    except (WebhookVerificationError, json.JSONDecodeError, ValueError, binascii.Error, TypeError) as exc:
        logger.warning("dodo_webhook_verify_failed", error=str(exc))
        raise WebhookVerificationError("Invalid signature or payload") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Webhook payload must be a JSON object")
    return parsed


def build_payment_checkout_base_url(raw: str) -> str:
    """Build the base Dodo payment link URL."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?", 1)[0].rstrip("/")
    return f"https://pay.dodopayments.com/{s.lstrip('/')}"


def _extract_api_key_id(data: dict[str, Any]) -> Optional[str]:
    metadata = data.get("metadata") or {}
    if isinstance(metadata, dict):
        return metadata.get("api_key_id")
    return None


def _extract_event_id(payload: dict[str, Any], data: dict[str, Any], event_type: str) -> str:
    candidates = [payload.get("id"), data.get("id"), data.get("subscription_id")]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def _event_transition(event_type: str) -> tuple[str, bool | None, str]:
    normalized = event_type.strip().lower()
    grant = {"payment.succeeded", "checkout.completed", "subscription.created", "subscription.active"}
    revoke = {"subscription.cancelled", "subscription.expired", "subscription.failed"}
    
    if normalized in grant:
        return "active", True, "Access granted/renewed."
    if normalized in revoke:
        return "inactive", False, "Access revoked."
    return "ignored", None, f"Event {event_type} ignored."


async def _acquire_idempotency_key(event_id: str) -> bool:
    key = f"depthapi:payments:webhook:{event_id}"
    return await safe_redis_command("set_if_not_exists", key, WEBHOOK_IDEMPOTENCY_TTL_SECONDS, "1")


def process_dodo_webhook_payload(payload: dict[str, Any], supabase: Any) -> PaymentWebhookResult:
    """Update api_keys table based on checkout/subscription events."""
    event_type = payload.get("type", payload.get("event", "unknown"))
    data = payload.get("data", {})
    state, should_be_active, message = _event_transition(event_type)
    api_key_id = _extract_api_key_id(data)

    if not api_key_id:
        return PaymentWebhookResult(event=event_type, event_id="none", state="ignored", message="No api_key_id in metadata")

    if should_be_active is not None:
        metadata = data.get("metadata") or {}
        plan = metadata.get("plan", "starter")
        budget = PLAN_MONTHLY_BUDGETS.get(plan, 100000)
        
        update_payload = {
            "plan": plan,
            "monthly_token_budget": budget,
            "is_active": should_be_active
        }
        
        try:
            supabase.table("api_keys").update(update_payload).eq("id", api_key_id).execute()
        except Exception as exc:
            logger.error("payment_webhook_update_failed", api_key_id=api_key_id, error=str(exc))
            raise HTTPException(503, "Failed to update API key status")

    return PaymentWebhookResult(
        event=event_type,
        event_id=_extract_event_id(payload, data, event_type),
        state=state,
        api_key_id=api_key_id,
        message=message
    )


@router.post("/payments/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(req: CheckoutRequest, api_key: ApiKeyRecord = Depends(verify_api_key)):
    """Create a Dodo Payments checkout URL for an API key."""
    settings = get_settings()
    if not settings.dodo_payment_link_id:
        raise HTTPException(503, "Payment configuration missing")

    base_url = build_payment_checkout_base_url(settings.dodo_payment_link_id)
    
    params = {
        "prefilled_email": api_key.owner_email,
        "metadata[api_key_id]": api_key.id,
        "metadata[plan]": req.plan,
        "success_url": req.success_url or "https://depthapi.com/success",
        "cancel_url": req.cancel_url or "https://depthapi.com/billing",
    }
    
    sep = "&" if "?" in base_url else "?"
    checkout_url = f"{base_url}{sep}{urllib.parse.urlencode(params)}"
    
    return CheckoutResponse(checkout_url=checkout_url, session_id=f"api_{api_key.id[:8]}")


@router.post("/payments/webhook/dodo", response_model=PaymentWebhookResult)
async def dodo_webhook(request: Request):
    """Handle Dodo webhooks for plan upgrades."""
    settings = get_settings()
    headers = _normalize_webhook_headers(request)
    body = await request.body()
    
    try:
        payload = verify_and_parse_dodo_webhook(body, headers, settings.dodo_webhook_secret)
    except WebhookVerificationError:
        raise HTTPException(401, "Invalid signature")

    event_type = payload.get("type", "unknown")
    data = payload.get("data", {})
    event_id = _extract_event_id(payload, data, event_type)
    
    if not await _acquire_idempotency_key(event_id):
        return PaymentWebhookResult(event=event_type, event_id=event_id, state="duplicate", message="Duplicate event")

    supabase = get_supabase_admin()
    return process_dodo_webhook_payload(payload, supabase)


@router.get("/payments/verify-status")
async def verify_payment_status(api_key: ApiKeyRecord = Depends(verify_api_key)):
    """Check current plan and status of the API key."""
    return {
        "api_key_id": api_key.id,
        "plan": api_key.plan,
        "monthly_budget": api_key.monthly_token_budget,
        "requests_per_minute": api_key.requests_per_minute,
        "is_active": api_key.is_active
    }
