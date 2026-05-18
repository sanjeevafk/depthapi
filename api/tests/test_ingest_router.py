import pytest

import api.routers.ingest as ingest_module
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key


@pytest.fixture(autouse=True)
def override_ingest_api_key(app_client):
    async def fake_key():
        return ApiKeyRecord(
            id="test-key-uuid-1234",
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
async def test_ingest_creates_queue_entry(app_client, monkeypatch, fake_supabase):
    fake_supabase.responses["knowledge_collections"] = {
        "id": "col-1",
        "name": "Docs",
        "description": None,
        "created_at": "2024-01-01T00:00:00Z",
    }
    fake_supabase.responses["knowledge_documents"] = [{"id": "doc-1"}]
    fake_supabase.responses["knowledge_ingestion_queue"] = [{"id": "queue-1"}]

    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.post(
        "/api/ingest",
        json={
            "collection_id": "col-1",
            "filename": "handbook.txt",
            "raw_text": "Policies and procedures.",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["collection_id"] == "col-1"
    assert data["document_id"] == "doc-1"
    assert data["queue_id"] == "queue-1"
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_ingest_requires_content(app_client, monkeypatch, fake_supabase):
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.post(
        "/api/ingest",
        json={"collection_id": "col-1"},
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_collections(app_client, monkeypatch, fake_supabase):
    fake_supabase.responses["knowledge_collections"] = [
        {"id": "col-1", "name": "Docs", "description": None, "created_at": "2024-01-01T00:00:00Z"},
        {"id": "col-2", "name": "Policies", "description": "HR", "created_at": "2024-01-02T00:00:00Z"},
    ]
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.get("/api/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert [row["id"] for row in data] == ["col-1", "col-2"]


@pytest.mark.asyncio
async def test_delete_collection_marks_deleted(app_client, monkeypatch, fake_supabase):
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: fake_supabase)

    resp = await app_client.delete("/api/collections/col-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert fake_supabase.updates
    table, payload = fake_supabase.updates[0]
    assert table == "knowledge_collections"
    assert "deleted_at" in payload
