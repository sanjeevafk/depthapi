import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from standardwebhooks import Webhook

import routers.payments as payments_module
import routers.webhooks as webhooks_module


def _whsec(label: str = "test") -> str:
    return "whsec_" + base64.b64encode(label.encode()).decode("ascii")


def _sign(body_str: str, secret: str) -> dict[str, str]:
    wh = Webhook(secret)
    msg_id = "evt-" + hashlib.sha256(body_str.encode()).hexdigest()[:24]
    ts = datetime.now(tz=timezone.utc)
    sig = wh.sign(msg_id, ts, body_str)
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(int(ts.timestamp())),
        "webhook-signature": sig,
    }


def test_verify_and_parse_dodo_webhook_accepts_valid_signature():
    secret = _whsec("unit-test")
    body_str = json.dumps({"event": "ping", "data": {}}, sort_keys=True)
    headers = _sign(body_str, secret)
    parsed = payments_module.verify_and_parse_dodo_webhook(body_str.encode("utf-8"), headers, secret)
    assert parsed["event"] == "ping"


@pytest.mark.asyncio
async def test_webhook_invalid_signature(app_client, monkeypatch, test_settings):
    secret = _whsec("invalid-test")
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    body = json.dumps({"event": "payment.succeeded", "data": {}})
    resp = await app_client.post(
        "/webhooks/dodo",
        content=body.encode(),
        headers={
            "webhook-signature": "v1,YmFk",  # invalid
            "webhook-id": "evt-1",
            "webhook-timestamp": str(int(datetime.now(tz=timezone.utc).timestamp())),
            "content-type": "application/json",
        },
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_missing_signature(app_client, monkeypatch, test_settings):
    secret = _whsec("missing-sig")
    test_settings.dodo_webhook_secret = secret
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
    secret = _whsec("bad-json")
    test_settings.dodo_webhook_secret = secret
    monkeypatch.setattr(webhooks_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    payload = b"not-json"
    hdrs = _sign(payload.decode("utf-8"), secret)
    hdrs["content-type"] = "application/json"

    resp = await app_client.post(
        "/webhooks/dodo",
        content=payload,
        headers=hdrs,
    )

    assert resp.status_code == 401


def test_process_dodo_payload_payment_succeeded(fake_supabase):
    payload = {
        "event": "payment.succeeded",
        "data": {
            "customer_email": "user@example.com",
            "metadata": {"api_key_id": "user-1", "plan": "pro"},
            "payment_id": "p1",
        },
    }

    result = payments_module.process_dodo_webhook_payload(payload, fake_supabase)
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
