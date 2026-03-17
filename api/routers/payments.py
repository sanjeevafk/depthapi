"""Payment processing endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from auth import check_is_pro, invalidate_pro_cache, verify_token
from config import get_settings
from logging_config import anonymize_user_id
from monitoring import capture_telemetry_event
from supabase import create_client

logger = structlog.get_logger()

router = APIRouter(tags=["payments"])

WEBHOOK_IDEMPOTENCY_TTL_SECONDS = 60 * 60 * 24


class CheckoutRequest(BaseModel):
    """Request model for creating a checkout session."""
    plan: str = "pro"  # Future-proof for multiple plans
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
    user_id: Optional[str] = None
    message: str


def verify_dodo_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature with HMAC SHA-256."""
    expected_signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


def _extract_user_id(data: dict[str, Any]) -> Optional[str]:
    metadata = data.get("metadata") or {}
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str) and user_id.strip():
            return user_id.strip()
    return None


def _extract_event_id(payload: dict[str, Any], data: dict[str, Any], event_type: str) -> str:
    candidates = [
        payload.get("id"),
        payload.get("event_id"),
        data.get("event_id"),
        data.get("id"),
        data.get("payment_id"),
        data.get("subscription_id"),
        data.get("checkout_id"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    # Fallback keeps duplicate handling deterministic if provider does not send event IDs.
    fingerprint_source = json.dumps({"event": event_type, "data": data}, sort_keys=True)
    return hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()


def _resolve_user_id_from_email(supabase: Any, email: str) -> Optional[str]:
    try:
        response = supabase.table("users").select("id").eq("email", email).single().execute()
        payload = getattr(response, "data", None)
        if isinstance(payload, dict):
            user_id = payload.get("id")
            if isinstance(user_id, str) and user_id:
                return user_id
    except Exception:
        pass  # No user found or query error
    return None

def _event_transition(event_type: str) -> tuple[str, Optional[bool], str]:
    normalized = event_type.strip().lower()

    grant_events = {
        "payment.succeeded",
        "checkout.completed",
        "checkout.session.completed",
        "subscription.created",
        "subscription.renewed",
    }
    revoke_events = {
        "subscription.cancelled",
        "subscription.canceled",
        "subscription.renewal_failed",
        "subscription.payment_failed",
    }
    failure_events = {
        "payment.failed",
        "checkout.expired",
        "checkout.session.expired",
    }

    if normalized in grant_events:
        return "active", True, "Pro access granted from verified payment event."
    if normalized in revoke_events:
        return "inactive", False, "Pro access revoked from subscription state event."
    if normalized in failure_events:
        return "payment_failed", None, "Payment failure recorded; Pro access unchanged."
    return "ignored", None, f"Unhandled event type: {event_type}"


async def _acquire_webhook_idempotency_key(event_id: str) -> bool:
    key = f"payments:webhook:event:{event_id}"
    try:
        from services.cache import get_redis

        redis = await get_redis()
        return await redis.set_if_not_exists(key, WEBHOOK_IDEMPOTENCY_TTL_SECONDS, "1")
    except Exception as exc:
        logger.error(
            "webhook_idempotency_store_unavailable",
            error=str(exc),
            event_id=event_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook idempotency backend unavailable; retry later",
        )


def process_dodo_webhook_payload(payload: dict[str, Any], supabase: Any) -> PaymentWebhookResult:
    """Process webhook payload and apply subscription state changes idempotently."""
    event_type = str(payload.get("event") or "").strip()
    if not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing event type")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid event data")

    state, should_set_pro, message = _event_transition(event_type)
    user_id = _extract_user_id(data)

    if not user_id:
        customer_email = data.get("customer_email")
        if isinstance(customer_email, str) and customer_email.strip():
            user_id = _resolve_user_id_from_email(supabase, customer_email.strip())

    if should_set_pro is not None:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing user identifier for account state update",
            )

        try:
            response = (
                supabase.table("users")
                .update({"is_pro": should_set_pro})
                .eq("id", user_id)
                .execute()
            )
            updated_rows = getattr(response, "data", None)
            if not updated_rows:
                logger.error(
                    "payment_webhook_user_not_found",
                    user_id=user_id,
                    event_type=event_type,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not found for payment state update",
                )
        except Exception as exc:
            logger.error(
                "payment_webhook_user_update_failed",
                user_id=user_id,
                event_type=event_type,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to persist payment state",
            )

        response_error = getattr(response, "error", None)
        if response_error:
            logger.error(
                "payment_webhook_user_update_error",
                user_id=user_id,
                event_type=event_type,
                error=str(response_error),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to persist payment state",
            )
        invalidate_pro_cache(user_id)

    return PaymentWebhookResult(
        event=event_type,
        event_id=_extract_event_id(payload, data, event_type),
        state=state,
        user_id=user_id,
        message=message,
    )


@router.post("/payments/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    request: CheckoutRequest,
    auth = Depends(verify_token)
):
    """
    Create a Dodo Payments checkout URL using payment links.
    
    Returns a checkout URL that the user should be redirected to.
    """
    user_id = str(auth["user"].id)
    user_id_hash = anonymize_user_id(user_id)
    logger.info("create_checkout_session_called", user_id=user_id)
    capture_telemetry_event("payment_checkout_start", user_id_hash=user_id_hash)
    settings = get_settings()
    if not settings.dodo_payment_link_id:
        raise HTTPException(status_code=503, detail="Payment configuration is missing")
    
    # Use Dodo Payment Link from environment configuration
    base_payment_link = f"https://pay.dodopayments.com/{settings.dodo_payment_link_id}"
    
    # Add customer email and metadata as URL parameters
    params = {
        "prefilled_email": auth["user"].email,
        "customer_name": auth["user"].user_metadata.get("full_name", ""),
        "metadata[user_id]": str(auth["user"].id),
        "metadata[plan]": request.plan,
        "success_url": request.success_url or "https://knowbear.vercel.app/success",
        "cancel_url": request.cancel_url or "https://knowbear.vercel.app/app"
    }
    
    checkout_url = f"{base_payment_link}?{urllib.parse.urlencode(params)}"
    
    logger.info("payment_link_generated", user_id=user_id)
    capture_telemetry_event("payment_checkout_session_created", user_id_hash=user_id_hash, plan=request.plan)
    
    return CheckoutResponse(
        checkout_url=checkout_url,
        session_id=f"pl_{str(auth['user'].id)}"  # Use user ID as session reference
    )


@router.post("/payments/webhook/dodo", response_model=PaymentWebhookResult)
async def dodo_webhook(
    request: Request,
    x_dodo_signature: Optional[str] = Header(default=None),
):
    """Verify and process Dodo webhook events for Pro subscription state."""
    settings = get_settings()

    if not x_dodo_signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")

    webhook_secret = settings.dodo_webhook_secret
    if not webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret is not configured",
        )

    body = await request.body()
    if not verify_dodo_signature(body, x_dodo_signature, webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("dodo_webhook_invalid_json", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    raw_data = payload.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    event_type = str(payload.get("event") or "")
    event_id = _extract_event_id(payload, data, event_type)

    is_new_event = await _acquire_webhook_idempotency_key(event_id)
    if not is_new_event:
        logger.info("dodo_webhook_duplicate_event", event_id=event_id, event_type=event_type)
        capture_telemetry_event("payment_webhook_duplicate", event_type=event_type, event_id=event_id)
        return PaymentWebhookResult(
            acknowledged=True,
            event=event_type or "unknown",
            event_id=event_id,
            duplicate=True,
            state="duplicate",
            message="Duplicate event ignored",
        )

    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase configuration missing",
        )

    supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    result = process_dodo_webhook_payload(payload, supabase)
    capture_telemetry_event(
        "payment_webhook_processed",
        event_type=result.event,
        event_id=result.event_id,
        state=result.state,
    )
    logger.info(
        "dodo_webhook_processed",
        event_type=result.event,
        event_id=result.event_id,
        user_id_hash=anonymize_user_id(result.user_id) if result.user_id else None,
        state=result.state,
    )
    return result


@router.get("/payments/verify-status")
async def verify_payment_status(auth = Depends(verify_token)):
    """
    Verify the current Pro status of a user.
    
    This endpoint can be called after a successful payment to confirm
    that the webhook has processed and the user has been upgraded.
    """
    user_id = str(auth["user"].id)
    capture_telemetry_event("payment_verify_status", user_id_hash=anonymize_user_id(user_id))
    try:
        is_pro = await check_is_pro(user_id, force_refresh=True)
        return {
            "user_id": user_id,
            "is_pro": is_pro,
            "status": "active" if is_pro else "free"
        }
    except Exception as e:
        logger.error("payment_status_verification_error", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to verify payment status")
