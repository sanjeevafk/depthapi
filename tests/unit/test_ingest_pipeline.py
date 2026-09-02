"""Tests for declarative pipeline ingestion route."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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
        self.fail_on_execute = False

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, statement: str, *args):
        if "INSERT INTO knowledge_collections" in statement:
            coll_id = args[0]
            owner_id = args[1]
            name = args[2]
            if coll_id in self.collections:
                if self.collections[coll_id]["api_key_id"] != owner_id:
                    return None
            self.collections[coll_id] = {"id": coll_id, "api_key_id": owner_id, "name": name}
            return {"id": coll_id}

        if "SELECT id FROM knowledge_documents" in statement:
            coll_id = args[0]
            c_hash = args[1]
            key = (coll_id, c_hash)
            if key in self.documents:
                return {"id": self.documents[key]["id"]}
            return None

        if "SELECT id, status FROM knowledge_ingestion_queue" in statement:
            return {"id": uuid4(), "status": "complete"}

        return None

    async def execute(self, statement: str, *args):
        if self.fail_on_execute:
            raise RuntimeError("Database write simulated failure")
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


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/ingest",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
        "scheme": "http",
    })


@pytest.mark.asyncio
async def test_multi_chunk_ingestion_preserves_metadata_and_provenance(monkeypatch):
    connection = _MockConnection()
    monkeypatch.setattr(ingest_module, "get_pool", lambda: _MockPool(connection))

    async def fake_embed(texts):
        return ["[" + ",".join(["0.1"] * 768) + "]" for _ in texts]

    monkeypatch.setattr(ingest_module, "embed_texts", fake_embed)
    key = ApiKeyRecord(str(uuid4()), "enterprise", True)

    multi_section_doc = (
        "# Architecture Overview\n"
        "This section documents the high-level architecture of DepthAPI with sufficient text to exceed limits.\n\n"
        "## Ingestion Pipeline\n"
        "This section documents the declarative ingestion pipeline stages and block chunking semantics.\n\n"
        "```python\n"
        "def run():\n"
        "    return 'success'\n"
        "```\n"
    )

    req = ingest_module.IngestRequest(
        filename="arch.md",
        source_url="https://docs.example.com/arch.md",
        raw_text=multi_section_doc,
        metadata={"project": "depthapi"},
    )
    res = await ingest_module.ingest(req, _request(), key)

    assert res.status == "complete"
    assert connection.committed is True
    assert connection.rolled_back is False

    # Document insert + multiple chunk inserts + queue insert
    doc_stmts = [s for s in connection.statements if "knowledge_documents" in s]
    chunk_stmts = [s for s in connection.statements if "knowledge_chunks" in s]
    queue_stmts = [s for s in connection.statements if "knowledge_ingestion_queue" in s]

    assert len(doc_stmts) == 1
    assert len(chunk_stmts) >= 2
    assert len(queue_stmts) == 1

    # Verify chunk metadata contracts
    chunk_calls = [args for s, args in zip(connection.statements, connection.execute_args) if "knowledge_chunks" in s]
    for idx, args in enumerate(chunk_calls):
        doc_id, chunk_order, content, token_count, embedding, meta_json, sec_title, chunk_hash = args
        assert str(doc_id) == res.document_id
        assert chunk_order == idx
        assert token_count > 0
        assert embedding.startswith("[0.1")
        assert chunk_hash is not None and len(chunk_hash) == 64

        meta = json.loads(meta_json)
        assert meta["schema_version"] == "1.0.0"
        assert meta["parser_version"] == "MarkdownParser@1.0.0"
        assert meta["chunker_version"] == "SemanticChunker@1.0.0"
        assert "quality_score" in meta
        assert meta["project"] == "depthapi"


@pytest.mark.asyncio
async def test_idempotency_duplicate_content_short_circuits(monkeypatch):
    connection = _MockConnection()
    monkeypatch.setattr(ingest_module, "get_pool", lambda: _MockPool(connection))

    embed_call_count = 0

    async def counting_embed(texts):
        nonlocal embed_call_count
        embed_call_count += 1
        return ["[" + ",".join(["0.1"] * 768) + "]" for _ in texts]

    monkeypatch.setattr(ingest_module, "embed_texts", counting_embed)
    key = ApiKeyRecord(str(uuid4()), "enterprise", True)

    doc_text = "Some unique text that should be ingested only once and short-circuited on subsequent requests."
    req = ingest_module.IngestRequest(raw_text=doc_text)

    # First ingestion
    res1 = await ingest_module.ingest(req, _request(), key)
    assert res1.status == "complete"
    assert embed_call_count == 1
    initial_statement_count = len(connection.statements)

    # Second ingestion with exact same content & collection
    req2 = ingest_module.IngestRequest(
        collection_id=res1.collection_id,
        raw_text=doc_text,
    )
    res2 = await ingest_module.ingest(req2, _request(), key)

    assert res2.status == "complete"
    assert res2.document_id == res1.document_id
    assert res2.collection_id == res1.collection_id
    # No additional embedding or insert statements
    assert embed_call_count == 1
    assert len(connection.statements) == initial_statement_count


@pytest.mark.asyncio
async def test_tenant_isolation_prevents_cross_tenant_collection_access(monkeypatch):
    connection = _MockConnection()
    monkeypatch.setattr(ingest_module, "get_pool", lambda: _MockPool(connection))

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]" for _ in texts]

    monkeypatch.setattr(ingest_module, "embed_texts", fake_embed)

    owner_a = ApiKeyRecord(str(uuid4()), "free", True)
    owner_b = ApiKeyRecord(str(uuid4()), "free", True)

    # Owner A creates a collection
    req1 = ingest_module.IngestRequest(raw_text="Tenant A private knowledge")
    res1 = await ingest_module.ingest(req1, _request(), owner_a)

    # Owner B tries to inject documents into Owner A's collection
    req2 = ingest_module.IngestRequest(
        collection_id=res1.collection_id,
        raw_text="Malicious tenant injection",
    )
    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest(req2, _request(), owner_b)

    assert exc_info.value.status_code == 404
    assert "Collection not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_empty_raw_text_rejected():
    key = ApiKeyRecord(str(uuid4()), "free", True)
    req_empty = ingest_module.IngestRequest(raw_text="")
    with pytest.raises(HTTPException) as exc_1:
        await ingest_module.ingest(req_empty, _request(), key)
    assert exc_1.value.status_code == 400

    req_whitespace = ingest_module.IngestRequest(raw_text="   \n\t  ")
    with pytest.raises(HTTPException) as exc_2:
        await ingest_module.ingest(req_whitespace, _request(), key)
    assert exc_2.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_collection_uuid_rejected():
    key = ApiKeyRecord(str(uuid4()), "free", True)
    req = ingest_module.IngestRequest(collection_id="not-a-uuid", raw_text="hello")
    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest(req, _request(), key)
    assert exc_info.value.status_code == 400
    assert "collection_id must be a UUID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_atomic_rollback_on_database_failure(monkeypatch):
    connection = _MockConnection()
    connection.fail_on_execute = True
    monkeypatch.setattr(ingest_module, "get_pool", lambda: _MockPool(connection))

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]" for _ in texts]

    monkeypatch.setattr(ingest_module, "embed_texts", fake_embed)
    key = ApiKeyRecord(str(uuid4()), "free", True)

    req = ingest_module.IngestRequest(raw_text="Valid document content")
    with pytest.raises(HTTPException) as exc_info:
        await ingest_module.ingest(req, _request(), key)

    assert exc_info.value.status_code == 503
    assert connection.rolled_back is True
    assert connection.committed is False
