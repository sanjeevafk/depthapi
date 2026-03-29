"""Email templates for transactional messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html as html_lib
from urllib.parse import urlparse


@dataclass(slots=True)
class EmailContent:
    subject: str
    html: str
    text: str


def _format_date(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).strftime("%B %d, %Y")
    except ValueError:
        return raw


def _safe_url(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http"}:
        return None
    if not parsed.netloc:
        return None
    return raw


def build_welcome_email(site_name: str, support_email: str, user_name: str | None) -> EmailContent:
    greeting_name = user_name or "there"
    safe_site_name = html_lib.escape(site_name, quote=True)
    safe_support_email = html_lib.escape(support_email, quote=True)
    safe_greeting_name = html_lib.escape(greeting_name, quote=True)
    subject = f"Welcome to {site_name}"
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <h1 style="margin-bottom: 8px;">Welcome to {safe_site_name}, {safe_greeting_name}!</h1>
      <p>Thanks for signing up. You now have access to your AI learning workspace with layered explanations and structured learning modes.</p>
      <p>If you ever need help, reach us at <a href="mailto:{safe_support_email}">{safe_support_email}</a>.</p>
      <p style="margin-top: 24px;">— The {safe_site_name} Team</p>
    </div>
    """
    text = (
        f"Welcome to {site_name}, {greeting_name}!\n\n"
        "Thanks for signing up. You now have access to your AI learning workspace with layered explanations and structured learning modes.\n\n"
        f"Need help? Email {support_email}.\n\n"
        f"— The {site_name} Team"
    )
    return EmailContent(subject=subject, html=html.strip(), text=text)


def build_subscription_confirmation_email(
    site_name: str,
    support_email: str,
    user_name: str | None,
    plan: str | None,
    next_billing_date: str | None,
    amount: str | int | None,
    currency: str | None,
    payment_id: str | None,
    invoice_url: str | None,
    receipt_url: str | None,
) -> EmailContent:
    greeting_name = user_name or "there"
    plan_label = plan or "Pro"
    next_billing = _format_date(next_billing_date)
    safe_invoice_url = _safe_url(invoice_url)
    safe_receipt_url = _safe_url(receipt_url)
    amount_line = ""
    if amount is not None:
        amount_line = f"{amount} {currency or ''}".strip()
    subject = f"{site_name} subscription confirmed"
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <h1 style="margin-bottom: 8px;">Your {site_name} subscription is active</h1>
      <p>Hi {greeting_name}, thanks for subscribing to the {plan_label} plan.</p>
      <p>Your premium access is now enabled. You can start using all pro features right away.</p>
      {f"<p><strong>Next billing date:</strong> {next_billing}</p>" if next_billing else ""}
      {f"<p><strong>Amount paid:</strong> {amount_line}</p>" if amount_line else ""}
      {f"<p><strong>Payment ID:</strong> {payment_id}</p>" if payment_id else ""}
      {f"<p><a href='{safe_invoice_url}'>View invoice</a></p>" if safe_invoice_url else ""}
      {f"<p><a href='{safe_receipt_url}'>View receipt</a></p>" if safe_receipt_url else ""}
      <p>Need help? Contact <a href="mailto:{support_email}">{support_email}</a>.</p>
      <p style="margin-top: 24px;">— The {site_name} Team</p>
    </div>
    """
    text = (
        f"Your {site_name} subscription is active\n\n"
        f"Hi {greeting_name}, thanks for subscribing to the {plan_label} plan.\n"
        "Your premium access is now enabled.\n"
        f"{f'Next billing date: {next_billing}\n' if next_billing else ''}"
        f"{f'Amount paid: {amount_line}\n' if amount_line else ''}"
        f"{f'Payment ID: {payment_id}\n' if payment_id else ''}"
        f"{f'Invoice: {safe_invoice_url}\n' if safe_invoice_url else ''}"
        f"{f'Receipt: {safe_receipt_url}\n' if safe_receipt_url else ''}"
        f"Need help? Email {support_email}.\n\n"
        f"— The {site_name} Team"
    )
    return EmailContent(subject=subject, html=html.strip(), text=text)


def build_cancellation_email(
    site_name: str,
    support_email: str,
    user_name: str | None,
    end_date: str | None,
) -> EmailContent:
    greeting_name = user_name or "there"
    end_label = _format_date(end_date)
    subject = f"{site_name} subscription canceled"
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <h1 style="margin-bottom: 8px;">Your subscription has been canceled</h1>
      <p>Hi {greeting_name}, your {site_name} subscription has been canceled.</p>
      {f"<p><strong>Access until:</strong> {end_label}</p>" if end_label else ""}
      <p>If this was a mistake or you need help, contact <a href="mailto:{support_email}">{support_email}</a>.</p>
      <p style="margin-top: 24px;">— The {site_name} Team</p>
    </div>
    """
    text = (
        f"Your {site_name} subscription has been canceled\n\n"
        f"Hi {greeting_name}, your subscription has been canceled.\n"
        f"{f'Access until: {end_label}\n' if end_label else ''}"
        f"Need help? Email {support_email}.\n\n"
        f"— The {site_name} Team"
    )
    return EmailContent(subject=subject, html=html.strip(), text=text)
