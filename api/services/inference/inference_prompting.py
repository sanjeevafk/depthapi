"""Prompt length and formatting helpers for inference."""

from __future__ import annotations

import re

from api.config import get_settings
from api.logging_config import logger
from api.services.messaging.token_count import count_prompt_tokens
from api.utils import canonical_prompt_depth, requests_depth

# Word caps aligned with prompt_configs/depths/*.json guidance.
_DEPTH_WORD_LIMITS: dict[str, int] = {
    "simple": 80,
    "accessible": 160,
    "technical": 400,
    "expert": 0,  # 0 => no post-generation word cap
}

_DEPTH_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "simple": 512,
    "accessible": 1024,
    "technical": 2048,
    "expert": 4096,
}


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part and part.strip()]


def _append_cue_if_fits(text: str, limit: int, cue: str | None) -> str:
    if not cue:
        return text
    cue_words = _word_count(cue)
    if _word_count(text) + cue_words <= limit:
        return f"{text} {cue}".strip()
    return text


def _compress_sentence(sentence: str, limit: int) -> str:
    cleaned = _normalize_whitespace(sentence)
    if _word_count(cleaned) <= limit:
        return cleaned
    without_parentheticals = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    if _word_count(without_parentheticals) <= limit:
        return without_parentheticals
    words = without_parentheticals.split()
    if not words:
        return ""
    cutoff = min(limit, len(words))
    for index in range(cutoff, 0, -1):
        if re.search(r"[,:;–—-]$", words[index - 1]):
            trimmed = " ".join(words[:index]).rstrip(",;–—-")
            return trimmed + ("" if trimmed.endswith((".", "!", "?")) else ".")
    trimmed = " ".join(words[:cutoff]).rstrip(",;–—-")
    return trimmed + ("" if trimmed.endswith((".", "!", "?")) else ".")


def _enforce_word_limit(text: str, limit: int, *, cue: str | None = None) -> str:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return ""
    if _word_count(normalized) <= limit:
        return normalized
    sentences = _split_sentences(normalized)
    selected: list[str] = []
    words_used = 0
    for sentence in sentences:
        sentence_words = _word_count(sentence)
        if words_used + sentence_words <= limit:
            selected.append(sentence)
            words_used += sentence_words
        else:
            break
    if selected:
        result = " ".join(selected).strip()
        return _append_cue_if_fits(result, limit, cue)
    compressed = _compress_sentence(sentences[0] if sentences else normalized, limit)
    return _append_cue_if_fits(compressed, limit, cue)


def _enforce_length_constraint(text: str, constraint: tuple[str, int] | None) -> str:
    if not constraint:
        return text
    unit, count = constraint
    if count <= 0:
        return ""
    if unit == "chars":
        normalized = _normalize_whitespace(text)
        if len(normalized) <= count:
            return normalized
        sentences = _split_sentences(normalized)
        selected = []
        chars_used = 0
        for sentence in sentences:
            space_needed = 1 if selected else 0
            if chars_used + space_needed + len(sentence) <= count:
                selected.append(sentence)
                chars_used += space_needed + len(sentence)
            else:
                break
        if selected:
            return " ".join(selected).strip()
        return normalized[:count].rstrip() + (
            "." if normalized and normalized[-1] not in ".!?" else ""
        )
    return _enforce_word_limit(text, count)


def _learning_length_policy(topic: str, depth: str | None = None) -> tuple[int, str | None]:
    """Return (word_limit, cue). A word_limit of 0 means no post-generation cap."""
    canonical = canonical_prompt_depth(depth) if depth else None
    if canonical:
        limit = _DEPTH_WORD_LIMITS.get(canonical, 160)
        if limit == 0:
            return (0, None)
        return (limit, None)
    if requests_depth(topic):
        return (120, None)
    return (60, None)


def _max_output_tokens_for_depth(depth: str | None, *, default: int = 1024) -> int:
    canonical = canonical_prompt_depth(depth) if depth else None
    if not canonical:
        return default
    return _DEPTH_MAX_OUTPUT_TOKENS.get(canonical, default)


def _is_large_input(text: str) -> bool:
    settings = get_settings()
    char_threshold = int(getattr(settings, "large_input_char_threshold", 5000))
    token_threshold = int(getattr(settings, "large_input_token_threshold", 5000))
    if len(text) > char_threshold:
        return True
    try:
        return count_prompt_tokens(text) > token_threshold
    except Exception as exc:
        logger.debug("large_input_token_check_failed", error=str(exc))
        return False


def _drain_complete_sentences(buffer: str) -> tuple[list[str], str]:
    if not buffer:
        return [], ""
    parts = re.split(r"(?<=[.!?])\s+", buffer)
    if not buffer.strip().endswith((".", "!", "?")) and parts:
        remainder = parts.pop()
    else:
        remainder = ""
    sentences = [part.strip() for part in parts if part and part.strip()]
    return sentences, remainder


def _extract_length_constraint(text: str) -> tuple[str, int] | None:
    lowered = (text or "").lower()
    if not lowered:
        return None
    patterns = (
        r"\b(?:in|within|under|max(?:imum)?|limit(?:ed)? to)\s+(\d{1,4})\s*(words?|chars?|characters?)\b",
        r"\b(\d{1,4})\s*(words?|chars?|characters?)\b",
        r"\b(\d{1,4})-(word|words|char|chars|character|characters)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        count = int(match.group(1))
        unit = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
        unit = unit.lower()
        if "char" in unit:
            return ("chars", max(count, 1))
        return ("words", max(count, 1))
    return None


def _apply_length_constraint(prompt: str, constraint: tuple[str, int] | None) -> str:
    if not constraint:
        return prompt
    unit, count = constraint
    if unit == "chars":
        return (
            f"{prompt}\n\nLength constraint: respond in at most {count} characters. "
            "If this conflicts with earlier length guidance, follow this limit "
            "and still complete the final sentence."
        )
    return (
        f"{prompt}\n\nLength constraint: respond in at most {count} words. "
        "If this conflicts with earlier length guidance, follow this limit "
        "and still complete the final sentence."
    )
