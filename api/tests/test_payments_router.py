import pytest
import supabase

import auth as auth_module
import routers.payments as payments_module


@pytest.mark.asyncio
async def test_create_checkout_session(app_client, monkeypatch, fake_user, test_settings):
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)

    resp = app_client.post(
        "/api/payments/create-checkout",
        json={"plan": "pro"}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "pay.dodopayments.com" in data["checkout_url"]
    assert data["session_id"].startswith("pl_")


@pytest.mark.asyncio
async def test_verify_payment_status(app_client, monkeypatch, fake_user, test_settings, fake_supabase):
    fake_supabase.responses["users"] = [{"is_pro": True}]
    monkeypatch.setattr(payments_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(supabase, "create_client", lambda *_args, **_kwargs: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.get("/api/payments/verify-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_pro"] is True
    assert data["status"] == "active"
