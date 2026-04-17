import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from standardwebhooks import Webhook

import auth as auth_module
import routers.payments as payments_module
import services.cache as cache_module
from logging_config import anonymize_user_id


def _test_whsec_secret(label: str = "knowbear-test-webhook-secret") -> str:
    return "whsec_" + base64.b64encode(label.encode()).decode("ascii")


def _sign_webhook_payload(payload: dict, secret: str) -> tuple[bytes, dict[str, str]]:
    body_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    wh = Webhook(secret)
    msg_id = "evt-" + hashlib.sha256(body_str.encode()).hexdigest()[:32]
    ts = datetime.now(tz=timezone.utc)
    sig = wh.sign(msg_id, ts, body_str)
    headers = {
        "webhook-id": msg_id,
        "webhook-timestamp": str(int(ts.timestamp())),
        "webhook-signature": sig,
    }
    return body_str.encode("utf-8"), headers


@pytest.mark.asyncio
async def test_create_checkout_session(app_client, monkeypatch, fake_user, test_settings):
    logged = []

    def fake_log_info(event, **kwargs):
        logged.append((event, kwargs))

    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module.logger, "info", fake_log_info)

    resp = await app_client.post(
        "/api/payments/create-checkout",
        json={"plan": "pro"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "pay.dodopayments.com" in data["checkout_url"]
    assert data["session_id"].startswith("pl_")
    expected_hash = anonymize_user_id(fake_user.id)
    create_event = next(fields for event, fields in logged if event == "create_checkout_session_called")
    link_event = next(fields for event, fields in logged if event == "payment_link_generated")
    assert create_event.get("user_id_hash") == expected_hash
    assert link_event.get("user_id_hash") == expected_hash
    assert "user_id" not in create_event
    assert "user_id" not in link_event


@pytest.mark.asyncio
async def test_create_checkout_accepts_full_payment_link_url(app_client, monkeypatch, fake_user, test_settings):
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    test_settings.dodo_payment_link_id = "https://checkout.dodopayments.com/buy/pdt_test123?quantity=1"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    resp = await app_client.post("/api/payments/create-checkout", json={"plan": "pro"})
    assert resp.status_code == 200
    url = resp.json()["checkout_url"]
    assert url.startswith("https://checkout.dodopayments.com/buy/pdt_test123")
    assert "metadata%5Buser_id%5D" in url or "metadata[user_id]" in url


@pytest.mark.asyncio
async def test_verify_payment_status(app_client, monkeypatch, fake_user, test_settings, fake_supabase):
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    async def fake_check_is_pro(_user_id, force_refresh=False):
        assert force_refresh is True
        return True

    monkeypatch.setattr(payments_module, "check_is_pro", fake_check_is_pro)

    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth

    resp = await app_client.get("/api/payments/verify-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_pro"] is True
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_webhook_success_flow(app_client, monkeypatch, test_settings, fake_supabase):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)
    invalidated = []
    monkeypatch.setattr(payments_module, "invalidate_pro_cache", lambda user_id: invalidated.append(user_id))

    payload = {
        "id": "evt-success-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)
    headers["content-type"] = "application/json"

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers=headers,
    )

    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["duplicate"] is False
    assert body_json["state"] == "active"
    assert body_json["user_id"] == "user-123"
    assert fake_supabase.updates[0][1]["is_pro"] is True
    assert invalidated == ["user-123"]


@pytest.mark.asyncio
async def test_webhook_accepts_whitespace_in_signature_header(app_client, monkeypatch, test_settings, fake_supabase):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(payments_module, "invalidate_pro_cache", lambda user_id: None)

    payload = {
        "id": "evt-whitespace-signature-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-whitespace-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)
    headers["webhook-signature"] = f"  {headers['webhook-signature']}  "

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(
    app_client,
    monkeypatch,
    test_settings,
    fake_supabase,
):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)

    payload = {
        "id": "evt-invalid-signature-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-invalid-signature-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)
    headers["webhook-signature"] = "v1,invalidbase64!!!"

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid signature"


@pytest.mark.asyncio
async def test_webhook_duplicate_event_is_idempotent(app_client, monkeypatch, test_settings, fake_supabase):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(payments_module, "invalidate_pro_cache", lambda user_id: None)
    payload = {
        "id": "evt-duplicate-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-dup-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)
    hdrs = {**headers, "content-type": "application/json"}

    first = await app_client.post("/api/payments/webhook/dodo", content=body, headers=hdrs)
    second = await app_client.post("/api/payments/webhook/dodo", content=body, headers=hdrs)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(fake_supabase.updates) == 1


@pytest.mark.asyncio
async def test_webhook_failed_payment_does_not_grant_pro(app_client, monkeypatch, test_settings, fake_supabase):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)

    payload = {
        "id": "evt-failed-1",
        "event": "payment.failed",
        "data": {
            "payment_id": "pay-failed-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["state"] == "payment_failed"
    assert fake_supabase.updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["subscription.cancelled", "subscription.renewal_failed"])
async def test_webhook_revokes_pro_for_cancellation_or_renewal_failure(
    app_client,
    monkeypatch,
    test_settings,
    fake_supabase,
    event_name,
):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)

    payload = {
        "id": f"evt-revoke-{event_name}",
        "event": event_name,
        "data": {
            "subscription_id": "sub-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["state"] == "inactive"
    assert fake_supabase.updates[-1][1]["is_pro"] is False


@pytest.mark.asyncio
async def test_webhook_on_hold_revokes_pro(app_client, monkeypatch, test_settings, fake_supabase):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)

    payload = {
        "id": "evt-on-hold-1",
        "event": "subscription.on_hold",
        "data": {
            "subscription_id": "sub-1",
            "metadata": {"user_id": "user-123"},
            "status": "on_hold",
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "on_hold"
    assert fake_supabase.updates[-1][1]["is_pro"] is False


@pytest.mark.asyncio
async def test_webhook_treats_event_as_duplicate_when_idempotency_store_is_unavailable(
    app_client,
    monkeypatch,
    test_settings,
    fake_supabase,
):
    secret = _test_whsec_secret()
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_supabase_admin", lambda: fake_supabase)

    async def broken_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(cache_module, "get_redis", broken_get_redis)

    payload = {
        "id": "evt-no-redis-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-no-redis",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    body, headers = _sign_webhook_payload(payload, secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["duplicate"] is True
    assert body_json["state"] == "duplicate"
    assert fake_supabase.updates == []
