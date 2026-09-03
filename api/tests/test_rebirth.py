from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from api.main import app
from api.routers import ingest as ingest_module
from api.routers import query as query_module
from api.services.inference.inference import generate_response
from api.services.security.api_key_auth import ApiKeyRecord


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Connection:
    def __init__(self):
        self.transaction_started = False
        self.statements: list[str] = []

    def transaction(self):
        self.transaction_started = True
        return _Transaction()

    async def fetchrow(self, *args):
        statement = args[0] if args else ""
        if "knowledge_collections" in statement:
            return {"id": uuid4()}
        return None

    async def execute(self, statement, *_args):
        self.statements.append(statement)


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/test", "headers": [], "client": ("127.0.0.1", 1), "server": ("test", 80), "scheme": "http"})


@pytest.mark.asyncio
async def test_ingest_is_transactional_and_writes_embedding(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(ingest_module, "get_pool", lambda: _Pool(connection))
    monkeypatch.setattr(ingest_module, "embed_texts", lambda texts: _embedding_result(texts))
    key = ApiKeyRecord(str(uuid4()), "enterprise", True)

    response = await ingest_module.ingest(
        ingest_module.IngestRequest(raw_text="transactional document"), _request(), key
    )

    assert response.status == "complete"
    assert connection.transaction_started is True
    assert len(connection.statements) == 3
    assert "embedding" in connection.statements[1]


async def _embedding_result(texts):
    return ["[" + ",".join(["0"] * 768) + "]" for _ in texts]


@pytest.mark.asyncio
async def test_query_scopes_retrieval_to_api_key(monkeypatch):
    captured = {}

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(_function, params):
        captured.update(params)
        return []

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    key_id = uuid4()
    response = await query_module.query(query_module.QueryRequest(query="private"), _request(), ApiKeyRecord(str(key_id), "free", False))

    assert "could not find sufficient matching documentation" in response.answer.lower() or response.answer == "No matching knowledge was found."
    assert captured["api_key_filter"] == key_id


@pytest.mark.asyncio
async def test_local_response_fallback_is_truthful():
    assert await generate_response("question", [{"content": "source text"}]) == "source text"


def test_security_controls_are_registered():
    middleware_names = {middleware.cls.__name__ for middleware in app.user_middleware}
    assert "CORSMiddleware" in middleware_names
    assert "SlowAPIMiddleware" in middleware_names
