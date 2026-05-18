import pytest

import api.routers.history as history_module
from api.logging_config import anonymize_user_id
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key


@pytest.fixture
def override_history_api_key(app_client):
    async def fake_key():
        return ApiKeyRecord(
            id="user-123",
            prefix="sk-depth-test",
            project_name="Test Project",
            owner_email="test@example.com",
            plan="pro",
            monthly_token_budget=10_000_000,
            requests_per_minute=100,
        )

    app_client.app.dependency_overrides[verify_api_key] = fake_key
    yield
    app_client.app.dependency_overrides.pop(verify_api_key, None)


@pytest.mark.asyncio
async def test_get_history(app_client, monkeypatch, fake_user, fake_supabase, override_history_api_key):
    fake_supabase.responses["history"] = [
        {
            "id": "h1",
            "topic": "Cats",
            "prompt_specs": [{"depth": "simple"}],
            "created_at": "2024-01-01T00:00:00Z"
        }
    ]

    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.get("/api/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["topic"] == "Cats"
    assert data[0]["prompt_specs"] == [{"depth": "simple"}]


@pytest.mark.asyncio
async def test_add_history(app_client, monkeypatch, fake_user, fake_supabase, override_history_api_key):
    fake_supabase.responses["history"] = [
        {
            "id": "h2",
            "topic": "Ocean",
            "prompt_specs": [{"topic": "Ocean", "depth": "technical"}],
            "created_at": "2024-01-01T00:00:00Z"
        }
    ]

    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.post(
        "/api/history",
        json={"topic": "Ocean", "prompt_specs": [{"depth": "technical"}]}
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == "h2"


@pytest.mark.asyncio
async def test_delete_history(app_client, monkeypatch, fake_user, fake_supabase, override_history_api_key):
    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.delete("/api/history/h1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_clear_history(app_client, monkeypatch, fake_user, fake_supabase, override_history_api_key):
    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.delete("/api/history")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"


@pytest.mark.asyncio
async def test_get_history_logs_anonymized_user_id_on_error(app_client, monkeypatch, fake_user, override_history_api_key):
    class BrokenSupabase:
        def table(self, _name):
            raise RuntimeError("boom")

    logged = []

    def fake_log_error(event, **kwargs):
        logged.append((event, kwargs))

    monkeypatch.setattr(history_module, "get_supabase_admin", lambda: BrokenSupabase())
    monkeypatch.setattr(history_module.logger, "error", fake_log_error)

    resp = await app_client.get("/api/history")
    assert resp.status_code == 500
    assert logged
    _event, fields = logged[0]
    assert fields.get("user_id_hash") == anonymize_user_id(fake_user.id)
    assert "user_id" not in fields
