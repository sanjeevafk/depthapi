"""Input validation and mode normalization utilities."""

import html
import json
import logging
import os
import re

MAX_TOPIC_LENGTH = 200
ALLOWED_PATTERN = re.compile(r"^[\w\s\-.,!?'\"()]+$", re.UNICODE)
_logger = logging.getLogger(__name__)

LEARNING_MODE = "learning"
TECHNICAL_MODE = "technical"
SOCRATIC_MODE = "socratic"

FREE_LEVELS = ["eli5", "eli10", "eli12", "eli15", "meme"]
PREMIUM_LEVELS: list[str] = []
PROMPT_LEVELS = FREE_LEVELS

MODE_ALIASES = {
    "fast": LEARNING_MODE,
    "default": LEARNING_MODE,
    "balanced": LEARNING_MODE,
    "ensemble": LEARNING_MODE,
    "technical-depth": TECHNICAL_MODE,
    "technical_depth": TECHNICAL_MODE,
    "technical": TECHNICAL_MODE,
    "learn": LEARNING_MODE,
    "learning": LEARNING_MODE,
    "socratic": SOCRATIC_MODE,
}

_DEFAULT_CHAT_MODE_DATA = {
    "chat_modes": [LEARNING_MODE, TECHNICAL_MODE, SOCRATIC_MODE],
    "free_modes": [LEARNING_MODE, SOCRATIC_MODE],
    "pro_modes": [TECHNICAL_MODE],
    "prompt_modes": PROMPT_LEVELS,
    "legacy_modes": [],
}


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


def topic_cache_key(topic: str, level: str, mode: str | None = None) -> str:
    """Generate cache key for topic+level, optionally scoping by mode."""
    safe = re.sub(r"\W+", "_", topic.lower().strip()).strip("_")[:50]
    return f"knowbear:{safe}:{mode}:{level}" if mode else f"knowbear:{safe}:{level}"


def normalize_mode(mode: str | None) -> str:
    normalized = (mode or "").strip().lower()
    return MODE_ALIASES.get(normalized, LEARNING_MODE)


def normalize_prompt_level(level: str | None) -> str:
    normalized = (level or "").strip().lower()
    return normalized if normalized in PROMPT_LEVELS else "eli15"


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

SUPPORTED_CHAT_MODES = set(CHAT_MODES)
SUPPORTED_PROMPT_MODES = set(CHAT_PROMPT_MODES)
DEFAULT_CHAT_MODE = LEARNING_MODE

CHAT_MODE_ALIASES = MODE_ALIASES.copy()
CHAT_INFERENCE_MODE_ALIASES = {
    LEARNING_MODE: LEARNING_MODE,
    TECHNICAL_MODE: TECHNICAL_MODE,
    SOCRATIC_MODE: SOCRATIC_MODE,
}
PROMPT_MODE_ALIASES = {
    "meme-style": "meme",
}
