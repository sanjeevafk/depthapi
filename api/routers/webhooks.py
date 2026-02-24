"""Webhook handlers for payment processing."""

import hmac
import hashlib
import structlog
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional
from config import get_settings
from supabase import create_client

logger = structlog.get_logger()

router = APIRouter(tags=["webhooks"])


def verify_dodo_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Dodo webhook signature."""
    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


def handle_dodo_event(payload: dict, supabase):
    """Process a Dodo webhook event payload."""
    event_type = payload.get("event")
    data = payload.get("data", {})

    logger.info("dodo_webhook_received", event_type=event_type, data=data)

    try:
        if event_type == "payment.succeeded":
            # Extract user information from metadata
            customer_email = data.get("customer_email")
            user_id = data.get("metadata", {}).get("user_id")

            if not user_id:
                logger.warning("dodo_webhook_missing_user_id", data=data)
                return {"status": "error", "message": "Missing user_id in metadata"}

            # Upgrade user to Pro
            supabase.table("users").update({
                "is_pro": True
            }).eq("id", user_id).execute()

            logger.info(
                "user_upgraded_to_pro",
                user_id=user_id,
                email=customer_email,
                payment_id=data.get("payment_id")
            )

            return {
                "status": "success",
                "message": "User upgraded to Pro",
                "user_id": user_id
            }

        if event_type == "payment.failed":
            customer_email = data.get("customer_email")
            logger.warning(
                "payment_failed",
                email=customer_email,
                reason=data.get("failure_reason")
            )
            return {"status": "acknowledged", "message": "Payment failure logged"}

        if event_type == "subscription.created":
            user_id = data.get("metadata", {}).get("user_id")

            if not user_id:
                logger.warning("dodo_webhook_missing_user_id", data=data)
                return {"status": "error", "message": "Missing user_id in metadata"}

            # Ensure user is Pro
            supabase.table("users").update({
                "is_pro": True
            }).eq("id", user_id).execute()

            logger.info(
                "subscription_created",
                user_id=user_id,
                subscription_id=data.get("subscription_id")
            )

            return {
                "status": "success",
                "message": "Subscription activated",
                "user_id": user_id
            }

        if event_type == "subscription.cancelled":
            user_id = data.get("metadata", {}).get("user_id")

            if not user_id:
                logger.warning("dodo_webhook_missing_user_id", data=data)
                return {"status": "error", "message": "Missing user_id in metadata"}

            # Downgrade user from Pro
            supabase.table("users").update({
                "is_pro": False
            }).eq("id", user_id).execute()

            logger.info(
                "subscription_cancelled",
                user_id=user_id,
                subscription_id=data.get("subscription_id")
            )

            return {
                "status": "success",
                "message": "User downgraded from Pro",
                "user_id": user_id
            }

        logger.info("dodo_webhook_unhandled_event", event=event_type)
        return {"status": "acknowledged", "message": f"Event {event_type} received"}

    except Exception as e:
        logger.error("dodo_webhook_processing_error", error=str(e), event=event_type)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@router.post("/webhooks/dodo")
async def dodo_webhook(
    request: Request,
    x_dodo_signature: Optional[str] = Header(None)
):
    """
    Handle Dodo payment webhooks.
    
    Events handled:
    - payment.succeeded: Upgrade user to Pro
    - payment.failed: Log failure
    - subscription.created: Handle subscription start
    - subscription.cancelled: Downgrade user from Pro
    """
    settings = get_settings()
    
    # Get raw body for signature verification
    body = await request.body()
    
    # Verify webhook signature
    if x_dodo_signature:
        webhook_secret = settings.dodo_webhook_secret or settings.dodo_api_key
        if not webhook_secret:
            logger.warning("dodo_webhook_missing_secret")
        elif not verify_dodo_signature(body, x_dodo_signature, webhook_secret):
            logger.warning("dodo_webhook_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("dodo_webhook_invalid_json", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # Initialize Supabase client
    supabase = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key
    )

    return handle_dodo_event(payload, supabase)


@router.post("/webhooks/dodo/dev-replay")
async def dodo_webhook_dev(payload: dict):
    """Dev-only webhook replay endpoint (disabled in production)."""
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")

    supabase = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key
    )

    return handle_dodo_event(payload, supabase)
