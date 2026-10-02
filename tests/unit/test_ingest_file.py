"""Unit tests for multipart file ingestion route (POST /api/ingest/file) powered by anydoc."""
from __future__ import annotations

import io
import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, UploadFile

from api.routers import ingest as ingest_module
from api.services.security.api_key_auth import ApiKeyRecord


class _Transaction:
    def __init__(self, connection: _MockConnection):
        self.connection = connection

    async def __aenter__(self):
        self.connection.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.connection.in_transaction = False
        if exc_type is not None:
            self.connection.rolled_back = True
            return False
        self.connection.committed = True
        return False


class _MockConnection:
    def __init__(self):
        self.in_transaction = False
        self.committed = False
        self.rolled_back = False
        self.statements: list[str] = []
        self.execute_args: list[tuple] = []
        self.collections: dict[UUID, dict] = {}
        self.documents: dict[tuple[UUID, str], dict] = {}

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, statement: str, *args):
        if "INSERT INTO knowledge_collections" in statement:
            coll_id = args[0]
            owner_id = args[1]
            name = args[2]
            self.collections[coll_id] = {"id": coll_id, "api_key_id": owner_id, "name": name}
            return {"id": coll_id}

        if "SELECT id FROM knowledge_documents" in statement:
            coll_id = args[0]
            c_hash = args[1]
            key = (coll_id, c_hash)
            if key in self.documents:
                return {"id": self.documents[key]["id"]}
            return None

        return None

    async def execute(self, statement: str, *args):
        self.statements.append(statement)
        self.execute_args.append(args)
        if "INSERT INTO knowledge_documents" in statement:
            doc_id = args[0]
            coll_id = args[1]
            c_hash = args[5]
            self.documents[(coll_id, c_hash)] = {"id": doc_id}


class _MockPool:
    def __init__(self, connection: _MockConnection):
        self.connection = connection

    def acquire(self):
        conn = self.connection

        class _Acquire:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_):
                return False

        return _Acquire()


def _api_key() -> ApiKeyRecord:
    return ApiKeyRecord(
        id=str(uuid4()),
        plan="standard",
        is_pro=False,
    )


@pytest.fixture
def mock_db(monkeypatch):
    conn = _MockConnection()
    pool = _MockPool(conn)
    monkeypatch.setattr(ingest_module, "get_pool", lambda: pool)
    async def fake_embed(texts):
        return [[0.1] * 768 for _ in texts]
    monkeypatch.setattr(ingest_module, "embed_texts", fake_embed)
    return conn


@pytest.mark.asyncio
async def test_ingest_file_markdown(mock_db):
    content = b"# Document Title\n\nThis is a body section with knowledge."
    upload = UploadFile(file=io.BytesIO(content), filename="doc.md", headers={"content-type": "text/markdown"})

    resp = await ingest_module.ingest_file(
        file=upload,
        collection_id=None,
        collection_name="docs",
        source_url="https://example.com/doc.md",
        metadata=json.dumps({"author": "Alice"}),
        engine=None,
        _api_key=_api_key(),
    )

    assert resp.status == "complete"
    assert resp.document_id
    assert resp.collection_id
    assert any("INSERT INTO knowledge_documents" in s for s in mock_db.statements)


@pytest.mark.asyncio
async def test_ingest_file_csv_via_anydoc(mock_db):
    content = b"service,status\nauth,ok\ningest,ok\nquery,ok\n"
    upload = UploadFile(file=io.BytesIO(content), filename="health.csv", headers={"content-type": "text/csv"})

    resp = await ingest_module.ingest_file(
        file=upload,
        collection_id=None,
        collection_name="metrics",
        source_url=None,
        metadata=None,
        engine=None,
        _api_key=_api_key(),
    )

    assert resp.status == "complete"
    # Find chunk insert statement and verify it processed markdown table
    chunk_inserts = [
        args for stmt, args in zip(mock_db.statements, mock_db.execute_args)
        if "INSERT INTO knowledge_chunks" in stmt
    ]
    assert len(chunk_inserts) >= 1
    chunk_content = chunk_inserts[0][2]
    assert "| service | status |" in chunk_content


@pytest.mark.asyncio
async def test_ingest_file_empty_rejected(mock_db):
    upload = UploadFile(file=io.BytesIO(b""), filename="empty.txt")

    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest_file(
            file=upload,
            collection_id=None,
            collection_name=None,
            source_url=None,
            metadata=None,
            engine=None,
            _api_key=_api_key(),
        )

    assert exc_info.value.status_code == 400
    assert "empty" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_ingest_file_invalid_metadata_rejected(mock_db):
    upload = UploadFile(file=io.BytesIO(b"content"), filename="valid.md")

    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest_file(
            file=upload,
            collection_id=None,
            collection_name=None,
            source_url=None,
            metadata="not-json",
            engine=None,
            _api_key=_api_key(),
        )

    assert exc_info.value.status_code == 400
    assert "metadata must be valid json" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_ingest_file_invalid_collection_uuid_rejected(mock_db):
    upload = UploadFile(file=io.BytesIO(b"content"), filename="valid.md")

    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest_file(
            file=upload,
            collection_id="not-a-uuid",
            collection_name=None,
            source_url=None,
            metadata=None,
            engine=None,
            _api_key=_api_key(),
        )

    assert exc_info.value.status_code == 400
    assert "collection_id must be a uuid" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_ingest_file_binary_without_depth_engine_fails_415(mock_db, monkeypatch):
    monkeypatch.setattr(ingest_module, "has_depth_engine", lambda: False)
    # Binary non-UTF-8 bytes
    binary_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\xff\xff"
    upload = UploadFile(file=io.BytesIO(binary_bytes), filename="sample.pdf")

    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest_file(
            file=upload,
            collection_id=None,
            collection_name=None,
            source_url=None,
            metadata=None,
            engine=None,
            _api_key=_api_key(),
        )

    assert exc_info.value.status_code == 415
