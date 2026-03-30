"""Transactional email endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from auth import verify_token
from config import get_settings
from services.email_service import send_email
from services.email_templates import (
    build_welcome_email,
    build_subscription_confirmation_email,
    build_cancellation_email,
)
from services.rate_limit import check_rate_limit

router = APIRouter(tags=["emails"])


class SubscriptionEmailRequest(BaseModel):
    plan: str | None = None
    next_billing_date: str | None = None


class CancellationEmailRequest(BaseModel):
    end_date: str | None = None


def _resolve_user_name(auth: dict) -> str | None:
    user = auth.get("user")
    if not user:
        return None
    metadata = user.user_metadata or {}
    return metadata.get("full_name") or user.email


def _build_test_email(site_name: str, support_email: str, user_name: str | None):
    greeting_name = user_name or "there"
    subject = f"{site_name} test email"
    html = (
        "<div style=\"font-family: Arial, sans-serif; line-height: 1.6; color: #111;\">"
        f"<h1 style=\"margin-bottom: 8px;\">{site_name} email test</h1>"
        f"<p>Hi {greeting_name}, this is a test email to confirm delivery.</p>"
        f"<p>If you did not request this, ignore it or contact {support_email}.</p>"
        f"<p style=\"margin-top: 24px;\">— The {site_name} Team</p>"
        "</div>"
    )
    text = (
        f"{site_name} email test\n\n"
        f"Hi {greeting_name}, this is a test email to confirm delivery.\n\n"
        f"If you did not request this, ignore it or contact {support_email}.\n\n"
        f"— The {site_name} Team"
    )
    return subject, html, text


@router.post("/emails/welcome")
async def send_welcome(background_tasks: BackgroundTasks, auth=Depends(verify_token)):
    settings = get_settings()
    user = auth["user"]
    rl = await check_rate_limit(
        identifier=str(user.id),
        limit=max(int(settings.email_rate_limit_per_minute or 5), 1),
        window_seconds=60,
        namespace="email_welcome",
        fail_open=True,
    )
    if not rl.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many email requests")
    content = build_welcome_email(settings.site_name, settings.support_email, _resolve_user_name(auth))
    background_tasks.add_task(
        send_email,
        to=user.email,
        subject=content.subject,
        html=content.html,
        text=content.text,
        template="welcome",
        user_id=str(user.id),
        event_type="welcome",
    )
    return {"queued": True}


@router.post("/emails/subscription-confirmation")
async def send_subscription_confirmation(
    payload: SubscriptionEmailRequest,
    background_tasks: BackgroundTasks,
    auth=Depends(verify_token),
):
    settings = get_settings()
    user = auth["user"]
    rl = await check_rate_limit(
        identifier=str(user.id),
        limit=max(int(settings.email_rate_limit_per_minute or 5), 1),
        window_seconds=60,
        namespace="email_subscription",
        fail_open=True,
    )
    if not rl.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many email requests")
    content = build_subscription_confirmation_email(
        settings.site_name,
        settings.support_email,
        _resolve_user_name(auth),
        payload.plan,
        payload.next_billing_date,
        None,
        None,
        None,
        None,
        None,
    )
    background_tasks.add_task(
        send_email,
        to=user.email,
        subject=content.subject,
        html=content.html,
        text=content.text,
        template="subscription_confirmation",
        user_id=str(user.id),
        event_type="subscription_confirmation",
    )
    return {"queued": True}


@router.post("/emails/cancellation")
async def send_cancellation(
    payload: CancellationEmailRequest,
    background_tasks: BackgroundTasks,
    auth=Depends(verify_token),
):
    settings = get_settings()
    user = auth["user"]
    rl = await check_rate_limit(
        identifier=str(user.id),
        limit=max(int(settings.email_rate_limit_per_minute or 5), 1),
        window_seconds=60,
        namespace="email_cancellation",
        fail_open=True,
    )
    if not rl.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many email requests")
    content = build_cancellation_email(
        settings.site_name,
        settings.support_email,
        _resolve_user_name(auth),
        payload.end_date,
    )
    background_tasks.add_task(
        send_email,
        to=user.email,
        subject=content.subject,
        html=content.html,
        text=content.text,
        template="cancellation",
        user_id=str(user.id),
        event_type="cancellation",
    )
    return {"queued": True}


@router.post("/emails/test")
async def send_test_email(background_tasks: BackgroundTasks, auth=Depends(verify_token)):
    settings = get_settings()
    user = auth["user"]
    rl = await check_rate_limit(
        identifier=str(user.id),
        limit=max(int(settings.email_rate_limit_per_minute or 5), 1),
        window_seconds=60,
        namespace="email_test",
        fail_open=True,
    )
    if not rl.allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many email requests")
    subject, html, text = _build_test_email(
        settings.site_name,
        settings.support_email,
        _resolve_user_name(auth),
    )
    background_tasks.add_task(
        send_email,
        to=user.email,
        subject=subject,
        html=html,
        text=text,
        template="test",
        user_id=str(user.id),
        event_type="test",
    )
    return {"queued": True}
