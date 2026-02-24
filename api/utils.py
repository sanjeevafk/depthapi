"""Input validation and sanitization utilities."""

import re
import html
import json
import os
import logging

MAX_TOPIC_LENGTH = 200
ALLOWED_PATTERN = re.compile(r"^[\w\s\-.,!?'\"()]+$", re.UNICODE)
_logger = logging.getLogger(__name__)


def sanitize_topic(topic: str) -> str:
    """Sanitize and validate topic input."""
    if not topic or not isinstance(topic, str):
        raise ValueError("Topic required")
    topic = topic.strip()
    if len(topic) > MAX_TOPIC_LENGTH:
        raise ValueError(f"Topic exceeds {MAX_TOPIC_LENGTH} chars")
    if not ALLOWED_PATTERN.match(topic):
        raise ValueError("Invalid characters in topic")
    return html.escape(topic)


def topic_cache_key(topic: str, level: str) -> str:
    """Generate cache key for topic+level."""
    safe = re.sub(r"\W+", "_", topic.lower().strip()).strip("_")[:50]
    return f"knowbear:{safe}:{level}"


FREE_LEVELS = ["eli5", "eli10", "eli12", "eli15", "meme-style"]
PREMIUM_LEVELS = ["classic60", "gentle70", "warm80"]

_DEFAULT_CHAT_MODE_DATA = {
    "chat_modes": [
        "eli5",
        "eli10",
        "eli12",
        "eli15",
        "meme-style",
        "classic60",
        "gentle70",
        "warm80",
        "ensemble",
        "technical-depth",
        "socratic",
    ],
    "free_modes": ["eli5", "eli10", "eli12", "eli15", "meme-style", "ensemble", "technical-depth", "socratic"],
    "pro_modes": ["classic60", "gentle70", "warm80"],
    "prompt_modes": ["eli5", "eli10", "eli12", "eli15", "meme-style", "classic60", "gentle70", "warm80"],
    "legacy_modes": ["technical", "meme"],
}


def _load_chat_modes():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared", "chat_modes.json"))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("chat_modes.json root must be an object")
        return data
    except Exception as exc:
        _logger.warning("Failed to load chat_modes.json (%s: %s); using hardcoded defaults.", type(exc).__name__, exc)
        return _DEFAULT_CHAT_MODE_DATA


_CHAT_MODE_DATA = _load_chat_modes()
CHAT_MODES = _CHAT_MODE_DATA.get("chat_modes") or []
CHAT_FREE_MODES = _CHAT_MODE_DATA.get("free_modes") or []
CHAT_PREMIUM_MODES = _CHAT_MODE_DATA.get("pro_modes") or []
CHAT_PROMPT_MODES = _CHAT_MODE_DATA.get("prompt_modes") or []
CHAT_LEGACY_MODES = _CHAT_MODE_DATA.get("legacy_modes") or []

SUPPORTED_CHAT_MODES = set(CHAT_MODES + CHAT_LEGACY_MODES)
SUPPORTED_PROMPT_MODES = set(CHAT_PROMPT_MODES + ["meme"])
DEFAULT_CHAT_MODE = "eli5"

CHAT_MODE_ALIASES = {
    "meme-style": "meme",
    "meme": "meme",
    "ensemble": "eli15",
    "technical-depth": "eli15",
    "technical": "eli15",
    "socratic": "socratic",
}

CHAT_INFERENCE_MODE_ALIASES = {
    "technical-depth": "technical_depth",
    "technical": "technical_depth",
}

PROMPT_MODE_ALIASES = {
    "meme": "meme-style",
}
