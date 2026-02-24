import json
import pytest

import routers.webhooks as webhooks_module


def test_verify_dodo_signature():
    payload = b"{}"
    secret = "secret"
    sig = webhooks_module.hmac.new(secret.encode(), payload, webhooks_module.hashlib.sha256).hexdigest()
    assert webhooks_module.verify_dodo_signature(payload, sig, secret) is True


@pytest.mark.asyncio
async def test_webhook_invalid_signature(app_client, monkeypatch, test_settings):
    old_secret = test_settings.dodo_webhook_secret
    test_settings.dodo_webhook_secret = "secret"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)

    resp = app_client.post(
        "/webhooks/dodo",
        data=json.dumps({"event": "payment.succeeded", "data": {}}),
        headers={"x-dodo-signature": "bad", "content-type": "application/json"}
    )

    assert resp.status_code == 401
    test_settings.dodo_webhook_secret = old_secret


@pytest.mark.asyncio
async def test_webhook_invalid_json(app_client, monkeypatch, test_settings):
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)

    resp = app_client.post(
        "/webhooks/dodo",
        data="not-json",
        headers={"content-type": "application/json"}
    )

    assert resp.status_code == 400


def test_handle_dodo_event_payment_succeeded(fake_supabase):
    payload = {
        "event": "payment.succeeded",
        "data": {
            "customer_email": "user@example.com",
            "metadata": {"user_id": "user-1"},
            "payment_id": "p1"
        }
    }

    result = webhooks_module.handle_dodo_event(payload, fake_supabase)
    assert result["status"] == "success"
    assert fake_supabase.updates


@pytest.mark.asyncio
async def test_dev_replay_disabled_in_prod(app_client, monkeypatch, test_settings):
    old_env = test_settings.environment
    test_settings.environment = "production"
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)

    resp = app_client.post("/webhooks/dodo/dev-replay", json={"event": "payment.failed", "data": {}})
    assert resp.status_code == 404
    test_settings.environment = old_env
