"""Payment processing endpoints."""

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

from auth import check_is_pro, get_supabase_admin, invalidate_pro_cache, verify_token
from config import get_settings
from logging_config import anonymize_user_id
from monitoring import capture_telemetry_event
from services.email_service import send_email
from services.email_templates import (
    build_subscription_confirmation_email,
    build_cancellation_email,
)
from services.rate_limit import check_rate_limit
from services.redis_safe import safe_redis_command

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


def _normalize_webhook_headers(request: Request) -> dict[str, str]:
    """Collect Standard Webhooks headers (case-insensitive)."""
    h = request.headers
    out: dict[str, str] = {}
    for key in ("webhook-id", "webhook-signature", "webhook-timestamp"):
        val = h.get(key)
        if val is None:
            # HTTP headers are case-insensitive; Starlette lowercases keys
            for k, v in h.items():
                if k.lower() == key:
                    val = v
                    break
        if val is not None:
            out[key] = val
    return out


def verify_and_parse_dodo_webhook(body: bytes, headers: dict[str, str], secret: str) -> dict[str, Any]:
    """
    Verify Dodo webhook using Standard Webhooks and return the parsed JSON object.

    Raises WebhookVerificationError on cryptographic / header failures.
    Re-raises json.JSONDecodeError when the payload is not valid JSON after a valid signature.
    """
    if not secret:
        raise WebhookVerificationError("Missing webhook secret")
    wh = Webhook(secret)
    try:
        parsed = wh.verify(body.decode("utf-8"), dict(headers))
    except WebhookVerificationError:
        raise
    except json.JSONDecodeError:
        raise
    except (ValueError, binascii.Error, TypeError) as exc:
        logger.warning("dodo_webhook_verify_format_error", error=str(exc))
        raise WebhookVerificationError("Invalid signature or webhook headers") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Webhook payload must be a JSON object")
    return parsed


def build_payment_checkout_base_url(raw: str) -> str:
    """
    Accept either a bare payment-link id/slug or a full https URL.

    Bare ids are prefixed with https://pay.dodopayments.com/
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?", 1)[0].rstrip("/")
    return f"https://pay.dodopayments.com/{s.lstrip('/')}"


def _extract_user_id(data: dict[str, Any]) -> Optional[str]:
    metadata = data.get("metadata") or {}
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str) and user_id.strip():
            return user_id.strip()
    return None


def _extract_customer_email(data: dict[str, Any]) -> Optional[str]:
    if isinstance(data.get("customer_email"), str) and data["customer_email"].strip():
        return data["customer_email"].strip()
    cust = data.get("customer")
    if isinstance(cust, dict):
        email = cust.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    return None


def _extract_event_id_from_payload(payload: dict[str, Any], data: dict[str, Any], event_type: str) -> str:
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
    except Exception as exc:
        logger.debug("payments_user_lookup_by_email_failed", email=email, error=str(exc))
    return None


def _resolve_email_from_user_id(supabase: Any, user_id: str) -> Optional[str]:
    user_id_hash = anonymize_user_id(user_id)
    try:
        response = supabase.table("users").select("email").eq("id", user_id).single().execute()
        payload = getattr(response, "data", None)
        if isinstance(payload, dict):
            email = payload.get("email")
            if isinstance(email, str) and email:
                return email
    except Exception as exc:
        logger.debug("payments_email_lookup_by_user_id_failed", user_id_hash=user_id_hash, error=str(exc))
    return None


def _resolve_name_from_user_id(supabase: Any, user_id: str) -> Optional[str]:
    user_id_hash = anonymize_user_id(user_id)
    try:
        response = supabase.table("users").select("full_name").eq("id", user_id).single().execute()
        payload = getattr(response, "data", None)
        if isinstance(payload, dict):
            name = payload.get("full_name")
            if isinstance(name, str) and name:
                return name
    except Exception as exc:
        logger.debug("payments_name_lookup_by_user_id_failed", user_id_hash=user_id_hash, error=str(exc))
    return None


def _subscription_fields_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """Map Dodo subscription/payment-shaped dict to public.users columns."""
    out: dict[str, Any] = {}
    sid = data.get("subscription_id")
    if isinstance(sid, str) and sid.strip():
        out["dodo_subscription_id"] = sid.strip()

    cust = data.get("customer")
    if isinstance(cust, dict):
        cid = cust.get("customer_id")
        if isinstance(cid, str) and cid.strip():
            out["dodo_customer_id"] = cid.strip()

    nbd = data.get("next_billing_date")
    if nbd is not None:
        if isinstance(nbd, str):
            out["current_period_end"] = nbd
        elif isinstance(nbd, datetime):
            out["current_period_end"] = nbd.isoformat()

    st = data.get("status")
    if st is not None:
        out["subscription_status"] = st if isinstance(st, str) else str(st)

    return out


def _event_transition(event_type: str) -> tuple[str, Optional[bool], str]:
    normalized = event_type.strip().lower()

    grant_events = {
        "payment.succeeded",
        "checkout.completed",
        "checkout.session.completed",
        "subscription.created",
        "subscription.active",
        "subscription.renewed",
    }
    revoke_events = {
        "subscription.cancelled",
        "subscription.canceled",
        "subscription.renewal_failed",
        "subscription.payment_failed",
        "subscription.failed",
    }
    hold_events = {
        "subscription.on_hold",
    }
    expired_events = {
        "subscription.expired",
    }
    plan_changed_events = {
        "subscription.plan_changed",
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
    if normalized in hold_events:
        return "on_hold", False, "Subscription on hold; Pro access suspended."
    if normalized in expired_events:
        return "expired", False, "Subscription expired; Pro access revoked."
    if normalized in plan_changed_events:
        return "plan_changed", None, "Subscription plan changed; metadata updated."
    if normalized in failure_events:
        return "payment_failed", None, "Payment failure recorded; Pro access unchanged."
    return "ignored", None, f"Unhandled event type: {event_type}"


async def _acquire_webhook_idempotency_key(event_id: str) -> bool:
    key = f"payments:webhook:event:{event_id}"
    try:
        result = await safe_redis_command(
            "set_if_not_exists",
            key,
            WEBHOOK_IDEMPOTENCY_TTL_SECONDS,
            "1",
            timeout=0.8,
        )
        if result is None:
            logger.warning("webhook_idempotency_degraded_fail_closed", event_id=event_id)
            return False
        return bool(result)
    except Exception as exc:
        logger.warning(
            "webhook_idempotency_store_unavailable_fail_closed",
            error=str(exc),
            event_id=event_id,
        )
        return False


def _normalize_payload_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Support both `event` (tests) and Dodo's `type` field."""
    if "event" not in payload and "type" in payload:
        out = dict(payload)
        out["event"] = payload.get("type")
        return out
    return payload


def process_dodo_webhook_payload(payload: dict[str, Any], supabase: Any) -> PaymentWebhookResult:
    """Process webhook payload and apply subscription state changes idempotently."""
    payload = _normalize_payload_dict(payload)
    event_type = str(payload.get("event") or "").strip()
    if not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing event type")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid event data")

    state, should_set_pro, message = _event_transition(event_type)
    user_id = _extract_user_id(data)

    if not user_id:
        customer_email = _extract_customer_email(data)
        if customer_email:
            user_id = _resolve_user_id_from_email(supabase, customer_email)
    user_id_hash = anonymize_user_id(user_id) if user_id else None

    subscription_fields = _subscription_fields_from_data(data)
    update_payload: dict[str, Any] = {}
    if should_set_pro is not None:
        update_payload["is_pro"] = should_set_pro
    update_payload.update(subscription_fields)

    needs_user = bool(update_payload)
    if needs_user and not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user identifier for account state update",
        )

    if not update_payload:
        return PaymentWebhookResult(
            event=event_type,
            event_id=_extract_event_id_from_payload(payload, data, event_type),
            state=state,
            user_id=user_id,
            message=message,
        )

    try:
        response = supabase.table("users").update(update_payload).eq("id", user_id).execute()
        updated_rows = getattr(response, "data", None)
        if not updated_rows:
            logger.error(
                "payment_webhook_user_not_found",
                user_id_hash=user_id_hash,
                event_type=event_type,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found for payment state update",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "payment_webhook_user_update_failed",
            user_id_hash=user_id_hash,
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
            user_id_hash=user_id_hash,
            event_type=event_type,
            error=str(response_error),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to persist payment state",
        )
    if user_id:
        invalidate_pro_cache(user_id)

    return PaymentWebhookResult(
        event=event_type,
        event_id=_extract_event_id_from_payload(payload, data, event_type),
        state=state,
        user_id=user_id,
        message=message,
    )


@router.post("/payments/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(request: CheckoutRequest, auth=Depends(verify_token)):
    """
    Create a Dodo Payments checkout URL using payment links.

    Returns a checkout URL that the user should be redirected to.
    """
    user_id = str(auth["user"].id)
    user_id_hash = anonymize_user_id(user_id)
    logger.info("create_checkout_session_called", user_id_hash=user_id_hash)
    capture_telemetry_event("payment_checkout_start", user_id_hash=user_id_hash)
    settings = get_settings()
    if not settings.dodo_payment_link_id:
        raise HTTPException(status_code=503, detail="Payment configuration is missing")

    base_payment_link = build_payment_checkout_base_url(settings.dodo_payment_link_id)
    if not base_payment_link:
        raise HTTPException(status_code=503, detail="Payment configuration is missing")

    rl = await check_rate_limit(
        identifier=user_id,
        limit=max(int(getattr(settings, "checkout_rate_limit_per_minute", 10)), 1),
        window_seconds=60,
        namespace="checkout",
        fail_open=True,
    )
    if not rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many checkout requests; try again later",
        )

    params = {
        "prefilled_email": auth["user"].email,
        "customer_name": auth["user"].user_metadata.get("full_name", ""),
        "metadata[user_id]": str(auth["user"].id),
        "metadata[plan]": request.plan,
        "success_url": request.success_url or "https://knowbear.vercel.app/success",
        "cancel_url": request.cancel_url or "https://knowbear.vercel.app/app",
    }

    separator = "&" if "?" in base_payment_link else "?"
    checkout_url = f"{base_payment_link}{separator}{urllib.parse.urlencode(params)}"

    logger.info("payment_link_generated", user_id_hash=user_id_hash)
    capture_telemetry_event("payment_checkout_session_created", user_id_hash=user_id_hash, plan=request.plan)

    return CheckoutResponse(
        checkout_url=checkout_url,
        session_id=f"pl_{str(auth['user'].id)}",
    )


@router.post("/payments/webhook/dodo", response_model=PaymentWebhookResult)
async def dodo_webhook(request: Request, background_tasks: BackgroundTasks):
    """Verify Dodo Standard Webhooks and process Pro subscription state."""
    settings = get_settings()

    hook_headers = _normalize_webhook_headers(request)
    if not hook_headers.get("webhook-signature"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")

    webhook_secret = settings.dodo_webhook_secret
    if not webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret is not configured",
        )

    body = await request.body()
    try:
        payload = verify_and_parse_dodo_webhook(body, hook_headers, webhook_secret)
    except WebhookVerificationError as exc:
        logger.warning("dodo_webhook_signature_invalid", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    except json.JSONDecodeError as exc:
        logger.warning("dodo_webhook_invalid_json_after_verify", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    except ValueError as exc:
        logger.warning("dodo_webhook_invalid_payload_shape", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    payload = _normalize_payload_dict(payload)
    raw_data = payload.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    event_type = str(payload.get("event") or "")
    event_id = _extract_event_id_from_payload(payload, data, event_type)
    webhook_id = hook_headers.get("webhook-id", "").strip()
    if webhook_id:
        event_id = webhook_id

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

    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase configuration missing",
        )
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

    settings = get_settings()
    event_type_normalized = (result.event or "").lower()
    if result.state in {"active", "inactive", "expired"}:
        email = _extract_customer_email(data)
        user_id = result.user_id
        if not email and user_id:
            email = _resolve_email_from_user_id(supabase, user_id)
        if email:
            user_name = _resolve_name_from_user_id(supabase, user_id) if user_id else None
            plan = None
            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                plan = metadata.get("plan")
            next_billing = data.get("next_billing_date")
            end_date = data.get("current_period_end") or data.get("next_billing_date")

            if result.state == "active":
                content = build_subscription_confirmation_email(
                    settings.site_name,
                    settings.support_email,
                    user_name,
                    plan,
                    next_billing if isinstance(next_billing, str) else None,
                    data.get("amount"),
                    data.get("currency"),
                    data.get("payment_id") or data.get("id"),
                    data.get("invoice_url") or data.get("invoice_link"),
                    data.get("receipt_url"),
                )
                background_tasks.add_task(
                    send_email,
                    to=email,
                    subject=content.subject,
                    html=content.html,
                    text=content.text,
                    template="subscription_confirmation",
                    user_id=user_id,
                    event_type=result.event,
                    metadata={"event_id": result.event_id},
                )
            elif result.state in {"inactive", "expired"} and event_type_normalized.startswith("subscription."):
                content = build_cancellation_email(
                    settings.site_name,
                    settings.support_email,
                    user_name,
                    end_date if isinstance(end_date, str) else None,
                )
                background_tasks.add_task(
                    send_email,
                    to=email,
                    subject=content.subject,
                    html=content.html,
                    text=content.text,
                    template="cancellation",
                    user_id=user_id,
                    event_type=result.event,
                    metadata={"event_id": result.event_id},
                )
    return result


@router.get("/payments/verify-status")
async def verify_payment_status(auth=Depends(verify_token)):
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
            "status": "active" if is_pro else "free",
        }
    except Exception as e:
        logger.error(
            "payment_status_verification_error",
            error=str(e),
            user_id_hash=anonymize_user_id(user_id),
        )
        raise HTTPException(status_code=500, detail="Failed to verify payment status")
