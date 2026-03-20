import pytest
from fastapi.responses import Response

import auth as auth_module
import routers.export as export_module


def fake_streaming_response(content, **kwargs):
    body = content.getvalue() if hasattr(content, "getvalue") else content
    return Response(
        content=body,
        media_type=kwargs.get("media_type"),
        headers=kwargs.get("headers"),
        status_code=kwargs.get("status_code", 200),
    )


@pytest.mark.asyncio
async def test_export_requires_pro(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return False

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(export_module, "StreamingResponse", fake_streaming_response)
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth

    resp = await app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "txt",
            "premium": True,
            "mode": "learning"
        }
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_export_txt_success(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return True

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(export_module, "StreamingResponse", fake_streaming_response)
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth

    resp = await app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "txt",
            "premium": True,
            "mode": "learning"
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

    async def fake_generate(_topic, level, mode=None, **_kwargs):
        calls.append(level)
        return "generated"

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(export_module, "generate_explanation", fake_generate)
    monkeypatch.setattr(export_module, "FREE_LEVELS", ["eli5", "eli10"])
    monkeypatch.setattr(export_module, "StreamingResponse", fake_streaming_response)

    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth

    resp = await app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "md",
            "premium": True,
            "mode": "learning"
        }
    )

    assert resp.status_code == 200
    assert "eli10" in calls


@pytest.mark.asyncio
async def test_export_invalid_format(app_client, monkeypatch, fake_user):
    async def fake_check_is_pro(_user_id):
        return True

    monkeypatch.setattr(export_module, "check_is_pro", fake_check_is_pro)
    monkeypatch.setattr(export_module, "StreamingResponse", fake_streaming_response)
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth

    resp = await app_client.post(
        "/api/export",
        json={
            "topic": "Cats",
            "explanations": {"eli5": "Meow"},
            "format": "pdf",
            "premium": True,
            "mode": "learning"
        }
    )
    assert resp.status_code == 422
