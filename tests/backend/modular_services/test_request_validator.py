from __future__ import annotations

import pytest

import services.request_validator as request_validator_module
from services.request_validator import RequestValidator


class DummyRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set_if_not_exists(self, key: str, ttl: int, value: str) -> bool:
        _ = ttl
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        if key in self.store:
            self.store.pop(key)
            return 1
        return 0


def test_validate_message_request_happy_path() -> None:
    validator = RequestValidator()
    result = validator.validate_message_request({"content": " hello ", "mode": "learn"})
    assert result.ok is True
    assert result.content == "hello"
    assert result.normalized_mode == "learn"


@pytest.mark.parametrize(
    ("payload", "error_message"),
    [
        ("not-an-object", "Request body must be a JSON object"),
        ({"user_id": "bad", "content": "ok"}, "user_id must not be supplied by the client"),
        ({"content": ""}, "Content is required"),
        ({"content": "ok", "mode": "invalid"}, "Invalid mode"),
    ],
)
def test_validate_message_request_rejects_invalid_payloads(payload, error_message: str) -> None:
    validator = RequestValidator()
    result = validator.validate_message_request(payload)
    assert result.ok is False
    assert result.error_message == error_message


@pytest.mark.asyncio
async def test_check_deduplication_blocks_second_request(monkeypatch) -> None:
    validator = RequestValidator(dedup_ttl_seconds=3)
    redis = DummyRedis()

    async def fake_safe_redis_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(request_validator_module, "safe_redis_call", fake_safe_redis_call)
    monkeypatch.setattr(request_validator_module.cache_module, "get_redis", fake_get_redis)

    first = await validator.check_deduplication("message-1")
    second = await validator.check_deduplication("message-1")

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_clear_deduplication_removes_key(monkeypatch) -> None:
    validator = RequestValidator(dedup_ttl_seconds=3)
    redis = DummyRedis()

    async def fake_safe_redis_call(fn, *args, **kwargs):
        _ = kwargs
        return await fn(*args)

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(request_validator_module, "safe_redis_call", fake_safe_redis_call)
    monkeypatch.setattr(request_validator_module.cache_module, "get_redis", fake_get_redis)

    assert await validator.check_deduplication("message-2") is True
    assert await validator.is_duplicate(validator.generate_dedup_key("message-2")) is True
    await validator.clear_deduplication("message-2")
    assert await validator.is_duplicate(validator.generate_dedup_key("message-2")) is False


@pytest.mark.asyncio
async def test_check_deduplication_fails_open_when_redis_unavailable(monkeypatch) -> None:
    validator = RequestValidator(dedup_ttl_seconds=3)

    async def fake_safe_redis_call(_fn, *args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(request_validator_module, "safe_redis_call", fake_safe_redis_call)

    assert await validator.check_deduplication("message-x") is True


def test_require_uuid_validates_format() -> None:
    validator = RequestValidator()
    value = validator.require_uuid("123e4567-e89b-12d3-a456-426614174000", "client_generated_id")
    assert value == "123e4567-e89b-12d3-a456-426614174000"
    with pytest.raises(ValueError):
        validator.require_uuid("", "client_generated_id")
    with pytest.raises(ValueError):
        validator.require_uuid("not-a-uuid", "client_generated_id")
