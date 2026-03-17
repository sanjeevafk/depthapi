import pytest
import json

import auth as auth_module
import routers.payments as payments_module
import services.cache as cache_module


def _sign_payload(payload: dict, secret: str) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return payments_module.hmac.new(
        secret.encode("utf-8"),
        raw,
        payments_module.hashlib.sha256,
    ).hexdigest()


@pytest.mark.asyncio
async def test_create_checkout_session(app_client, monkeypatch, fake_user, test_settings):
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/api/payments/create-checkout",
        json={"plan": "pro"}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "pay.dodopayments.com" in data["checkout_url"]
    assert data["session_id"].startswith("pl_")


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
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "create_client", lambda *_args, **_kwargs: fake_supabase)
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
    signature = _sign_payload(payload, test_settings.dodo_webhook_secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicate"] is False
    assert body["state"] == "active"
    assert body["user_id"] == "user-123"
    assert fake_supabase.updates[0][1]["is_pro"] is True
    assert invalidated == ["user-123"]


@pytest.mark.asyncio
async def test_webhook_duplicate_event_is_idempotent(app_client, monkeypatch, test_settings, fake_supabase):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "create_client", lambda *_args, **_kwargs: fake_supabase)
    monkeypatch.setattr(payments_module, "invalidate_pro_cache", lambda user_id: None)
    payload = {
        "id": "evt-duplicate-1",
        "event": "payment.succeeded",
        "data": {
            "payment_id": "pay-dup-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    signature = _sign_payload(payload, test_settings.dodo_webhook_secret)

    first = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )
    second = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(fake_supabase.updates) == 1


@pytest.mark.asyncio
async def test_webhook_failed_payment_does_not_grant_pro(app_client, monkeypatch, test_settings, fake_supabase):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "create_client", lambda *_args, **_kwargs: fake_supabase)

    payload = {
        "id": "evt-failed-1",
        "event": "payment.failed",
        "data": {
            "payment_id": "pay-failed-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    signature = _sign_payload(payload, test_settings.dodo_webhook_secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "payment_failed"
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
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "create_client", lambda *_args, **_kwargs: fake_supabase)

    payload = {
        "id": f"evt-revoke-{event_name}",
        "event": event_name,
        "data": {
            "subscription_id": "sub-1",
            "metadata": {"user_id": "user-123", "plan": "pro"},
        },
    }
    signature = _sign_payload(payload, test_settings.dodo_webhook_secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "inactive"
    assert fake_supabase.updates[-1][1]["is_pro"] is False


@pytest.mark.asyncio
async def test_webhook_returns_503_when_idempotency_store_is_unavailable(
    app_client,
    monkeypatch,
    test_settings,
    fake_supabase,
):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "create_client", lambda *_args, **_kwargs: fake_supabase)

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
    signature = _sign_payload(payload, test_settings.dodo_webhook_secret)

    resp = await app_client.post(
        "/api/payments/webhook/dodo",
        data=json.dumps(payload),
        headers={"x-dodo-signature": signature, "content-type": "application/json"},
    )

    assert resp.status_code == 503
    assert "idempotency backend unavailable" in resp.json()["detail"].lower()
    assert fake_supabase.updates == []
