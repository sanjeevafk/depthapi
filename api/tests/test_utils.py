import pytest

from utils import sanitize_topic, topic_cache_key


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
    assert key == "knowbear:hello_world:eli5"
