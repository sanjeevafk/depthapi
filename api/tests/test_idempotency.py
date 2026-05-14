import pytest

from api.services.security.idempotency import message_idempotency_key, query_stream_idempotency_key


def test_query_stream_idempotency_key_is_stable_for_valid_inputs() -> None:
    key = query_stream_idempotency_key("scope-a", "msg-1")

    assert key.startswith("depthapi:query_stream:idempotency:")
    assert len(key.split(":")[-1]) == 64


def test_query_stream_idempotency_key_rejects_null_bytes() -> None:
    with pytest.raises(ValueError, match="scope must not contain null bytes"):
        query_stream_idempotency_key("bad\x00scope", "msg-1")

    with pytest.raises(ValueError, match="message_id must not contain null bytes"):
        query_stream_idempotency_key("scope-a", "bad\x00msg")


def test_message_idempotency_key_rejects_null_bytes() -> None:
    with pytest.raises(ValueError, match="user_id must not contain null bytes"):
        message_idempotency_key("bad\x00user", "msg-1")

    with pytest.raises(ValueError, match="message_id must not contain null bytes"):
        message_idempotency_key("user-1", "bad\x00msg")
