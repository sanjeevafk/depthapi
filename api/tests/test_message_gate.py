import time

import orjson
import pytest

from services import message_gate as gate_module # type: ignore


@pytest.mark.asyncio
async def test_gatekeeper_replay_from_completed(dummy_redis, monkeypatch):
    async def fake_get_redis():
        return dummy_redis

    monkeypatch.setattr(gate_module, "get_redis", fake_get_redis)
    idempotency_key = "knowbear:idempotency:test"
    await dummy_redis.hset(idempotency_key, "status", "COMPLETED")
    await dummy_redis.hset(idempotency_key, "response", "cached-response")

    result = await gate_module.gatekeep_message_request(
        identifier="user:test",
        reserved_tokens=10,
        token_bucket_capacity=10,
        token_bucket_refill_per_sec=1.0,
        token_bucket_cost=1,
        daily_quota_limit=1000,
        daily_quota_window=86400,
        circuit_threshold=0,
        circuit_open_seconds=60,
        idempotency_key=idempotency_key,
        timeout_seconds=0.05,
    )

    assert result.allowed is True
    assert result.idempotency_status == "COMPLETED"
    assert result.idempotency_response == "cached-response"


@pytest.mark.asyncio
async def test_append_and_snapshot_roundtrip(dummy_redis, monkeypatch):
    async def fake_get_redis():
        return dummy_redis

    monkeypatch.setattr(gate_module, "get_redis", fake_get_redis)
    conversation_id = "conv-snapshot"
    meta_key = f"knowbear:conversation:{conversation_id}:meta"
    meta = {"conversation_id": conversation_id, "user_id": "user-1"}
    await dummy_redis.setex(meta_key, 3600, orjson.dumps(meta).decode("utf-8"))

    payload = {
        "role": "user",
        "content": "hello",
        "sequence_id": "__SEQ__",
        "created_at": time.time(),
    }
    seq = await gate_module.append_conversation_message(
        conversation_id=conversation_id,
        message_json=orjson.dumps(payload).decode("utf-8"),
        max_messages=10,
        timeout_seconds=0.05,
    )
    assert seq == 1

    meta_raw, messages = await gate_module.fetch_conversation_snapshot(
        conversation_id=conversation_id,
        max_messages=10,
        timeout_seconds=0.05,
    )
    assert meta_raw is not None
    assert len(messages) == 1
    assert "hello" in messages[0]


@pytest.mark.asyncio
async def test_cache_get_set_roundtrip(dummy_redis, monkeypatch):
    async def fake_get_redis():
        return dummy_redis

    monkeypatch.setattr(gate_module, "get_redis", fake_get_redis)
    key = "knowbear:cache:test"
    assert await gate_module.cache_set_value(key, "value", 60)
    cached = await gate_module.cache_get_value(key)
    assert cached == "value"
