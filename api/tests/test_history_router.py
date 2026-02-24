import pytest

import auth as auth_module
import routers.history as history_module


@pytest.mark.asyncio
async def test_get_history(app_client, monkeypatch, fake_user, fake_supabase):
    fake_supabase.responses["history"] = [
        {
            "id": "h1",
            "topic": "Cats",
            "levels": ["eli5"],
            "mode": "fast",
            "created_at": "2024-01-01T00:00:00Z"
        }
    ]

    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.get("/api/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["topic"] == "Cats"


@pytest.mark.asyncio
async def test_add_history(app_client, monkeypatch, fake_user, fake_supabase):
    fake_supabase.responses["history"] = [
        {
            "id": "h2",
            "topic": "Ocean",
            "levels": ["eli5"],
            "mode": "fast",
            "created_at": "2024-01-01T00:00:00Z"
        }
    ]

    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.post(
        "/api/history",
        json={"topic": "Ocean", "levels": ["eli5"], "mode": "fast"}
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == "h2"


@pytest.mark.asyncio
async def test_delete_history(app_client, monkeypatch, fake_user, fake_supabase):
    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.delete("/api/history/h1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_clear_history(app_client, monkeypatch, fake_user, fake_supabase):
    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = lambda: {"user": fake_user}

    resp = app_client.delete("/api/history")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"
