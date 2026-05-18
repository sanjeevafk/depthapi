import pytest
from pathlib import Path

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


@pytest.mark.asyncio
async def test_local_ingest_writes_to_filesystem_backend(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: None)
    monkeypatch.setenv("RAG_DATA_PATH", str(tmp_path))

    class FakeStore:
        def __init__(self):
            self.calls = []

        async def ingest(self, **kwargs):
            self.calls.append(kwargs)
            return 2

    class FakeEmbedService:
        async def create_embeddings(self, texts):
            return [[0.1, 0.2] for _ in texts]

    fake_store = FakeStore()
    monkeypatch.setattr(ingest_module, "get_rag_backend", lambda: fake_store)
    monkeypatch.setattr(ingest_module, "FilesystemRAGStore", FakeStore)
    monkeypatch.setattr(ingest_module, "get_embedding_service", lambda: FakeEmbedService())

    async def fake_local_content_and_chunks(_req):
        return "Policies and procedures.", ["chunk one", "chunk two"]

    monkeypatch.setattr(ingest_module, "_local_content_and_chunks", fake_local_content_and_chunks)

    resp = await app_client.post(
        "/api/ingest",
        json={
            "collection_name": "Docs",
            "filename": "handbook.txt",
            "raw_text": "Policies and procedures.",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["queue_id"]
    assert fake_store.calls
    assert fake_store.calls[0]["namespace"].startswith("test-key-uuid-1234/")


@pytest.mark.asyncio
async def test_local_list_collections_reads_registry(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: None)
    monkeypatch.setenv("RAG_DATA_PATH", str(tmp_path))
    registry = ingest_module._registry()
    registry.get_or_create_collection(
        api_key_id="test-key-uuid-1234",
        collection_id=None,
        collection_name="Docs",
    )

    resp = await app_client.get("/api/collections")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Docs"


@pytest.mark.asyncio
async def test_local_delete_collection_removes_namespace(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_module, "get_supabase_admin", lambda: None)
    monkeypatch.setenv("RAG_DATA_PATH", str(tmp_path))
    registry = ingest_module._registry()
    collection = registry.get_or_create_collection(
        api_key_id="test-key-uuid-1234",
        collection_id=None,
        collection_name="Docs",
    )
    namespace_dir = Path(tmp_path) / "test-key-uuid-1234" / collection["id"]
    namespace_dir.mkdir(parents=True, exist_ok=True)
    (namespace_dir / "chunks.json").write_text("[]", encoding="utf-8")

    resp = await app_client.delete(f"/api/collections/{collection['id']}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert not namespace_dir.exists()
