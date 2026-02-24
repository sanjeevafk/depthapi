import pytest

import auth as auth_module
import routers.export as export_module


@pytest.mark.asyncio
async def test_export_requires_pro(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return False

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "txt",
            "premium": True,
            "mode": "fast"
        }
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_export_txt_success(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return True

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "txt",
            "premium": True,
            "mode": "fast"
        }
    )

    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert "Cats" in resp.text
    assert "Meow" in resp.text


@pytest.mark.asyncio
async def test_export_missing_levels_triggers_generation(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return True

    calls = []

    async def fake_generate(_topic, level, _premium, _mode):
        calls.append(level)
        return "generated"

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(export_module, "ensemble_generate", fake_generate)
    monkeypatch.setattr(export_module, "FREE_LEVELS", ["eli5", "eli10"])
    monkeypatch.setattr(export_module, "PREMIUM_LEVELS", [])

    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "md",
            "premium": True,
            "mode": "fast"
        }
    )

    assert resp.status_code == 200
    assert "eli10" in calls


@pytest.mark.asyncio
async def test_export_invalid_format(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return True

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "pdf",
            "premium": True,
            "mode": "fast"
        }
    )
    assert resp.status_code == 422
