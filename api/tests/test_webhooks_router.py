import json
import pytest

import routers.webhooks as webhooks_module
import routers.payments as payments_module


def test_verify_dodo_signature():
    payload = b"{}"
    secret = "secret"
    sig = payments_module.hmac.new(secret.encode(), payload, payments_module.hashlib.sha256).hexdigest()
    assert webhooks_module.verify_dodo_signature(payload, sig, secret) is True


@pytest.mark.asyncio
async def test_webhook_invalid_signature(app_client, monkeypatch, test_settings):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/webhooks/dodo",
        data=json.dumps({"event": "payment.succeeded", "data": {}}),
        headers={"x-dodo-signature": "bad", "content-type": "application/json"}
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_missing_signature(app_client, monkeypatch, test_settings):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    resp = await app_client.post(
        "/webhooks/dodo",
        data=json.dumps({"event": "payment.succeeded", "data": {}}),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_invalid_json(app_client, monkeypatch, test_settings):
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    payload = b"not-json"
    sig = payments_module.hmac.new(
        test_settings.dodo_webhook_secret.encode(), payload, payments_module.hashlib.sha256
    ).hexdigest()

    resp = await app_client.post(
        "/webhooks/dodo",
        data=payload,
        headers={"content-type": "application/json", "x-dodo-signature": sig}
    )

    assert resp.status_code == 400


def test_process_dodo_payload_payment_succeeded(fake_supabase):
    payload = {
        "event": "payment.succeeded",
        "data": {
            "customer_email": "user@example.com",
            "metadata": {"user_id": "user-1"},
            "payment_id": "p1"
        }
    }

    result = webhooks_module.process_dodo_webhook_payload(payload, fake_supabase)
    assert result.state == "active"
    assert fake_supabase.updates


@pytest.mark.asyncio
async def test_dev_replay_disabled_in_prod(app_client, monkeypatch, test_settings):
    old_env = test_settings.environment
    test_settings.environment = "production"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)

    resp = await app_client.post("/webhooks/dodo/dev-replay", json={"event": "payment.failed", "data": {}})
    assert resp.status_code == 404
    test_settings.environment = old_env
