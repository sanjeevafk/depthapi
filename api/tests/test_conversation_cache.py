import pytest
from types import SimpleNamespace

import services.conversation_cache as conversation_cache


class CaptureQuery:
    def __init__(self, response):
        self.response = response
        self.order_args = None
        self.select_args = None

    def select(self, *args, **_kwargs):
        self.select_args = args
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, column, desc=False):
        self.order_args = (column, desc)
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.response)


class FakeSupabase:
    def __init__(self, conversation_query, messages_query):
        self.conversation_query = conversation_query
        self.messages_query = messages_query

    def table(self, table):
        if table == "conversations":
            return self.conversation_query
        if table == "messages":
            return self.messages_query
        return CaptureQuery([])


@pytest.mark.asyncio
async def test_warm_snapshot_orders_by_sequence_id(dummy_redis, monkeypatch):
    conversation_query = CaptureQuery(
        {
            "id": "conv-1",
            "user_id": "user-1",
            "mode": "learn",
            "settings": {},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    messages_query = CaptureQuery(
        [
            {
                "role": "user",
                "content": "hello",
                "created_at": "2026-01-01T00:00:00Z",
                "metadata": {},
                "sequence_id": 1,
            }
        ]
    )
    fake_supabase = FakeSupabase(conversation_query, messages_query)

    monkeypatch.setattr(conversation_cache, "get_supabase_admin", lambda: fake_supabase)

    async def fake_get_redis():
        return dummy_redis

    monkeypatch.setattr(conversation_cache, "get_redis", fake_get_redis)

    await conversation_cache.warm_conversation_snapshot("conv-1", "user-1")

    assert messages_query.order_args is not None
    assert messages_query.order_args[0] == "sequence_id"
    assert messages_query.select_args is not None
    assert "sequence_id" in messages_query.select_args[0]
