import pytest

from api.utils import sanitize_topic, topic_cache_key, with_timeout


def test_sanitize_topic_valid():
    assert sanitize_topic("  Physics ") == "Physics"


def test_sanitize_topic_invalid_chars():
    with pytest.raises(ValueError):
        sanitize_topic("bad<topic>")


def test_sanitize_topic_too_long():
    with pytest.raises(ValueError):
        sanitize_topic("a" * 201)


def test_sanitize_topic_missing():
    with pytest.raises(ValueError):
        sanitize_topic("")


def test_topic_cache_key_format():
    key = topic_cache_key("Hello World!", "eli5")
    assert key == "depthapi:hello_world:eli5"


@pytest.mark.asyncio
async def test_with_timeout_reraises_unexpected_exception_by_default():
    async def boom():
        raise ValueError("unexpected")

    with pytest.raises(ValueError, match="unexpected"):
        await with_timeout(
            boom(),
            timeout_seconds=0.05,
            default="fallback",
            context_label="unit_test_context",
        )


@pytest.mark.asyncio
async def test_with_timeout_swallow_exceptions_returns_default_and_logs(caplog):
    async def boom():
        raise RuntimeError("network-ish failure")

    with caplog.at_level("ERROR"):
        result = await with_timeout(
            boom(),
            timeout_seconds=0.05,
            default="fallback",
            context_label="unit_test_context",
            swallow_exceptions=True,
        )

    assert result == "fallback"
    exception_logs = [
        record for record in caplog.records if "timeout_wrapper_exception" in record.getMessage()
    ]
    assert exception_logs
    assert any("unit_test_context" in record.getMessage() for record in exception_logs)
    assert any(record.exc_info for record in exception_logs)
