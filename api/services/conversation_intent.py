from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ConversationIntentType = Literal[
    "new_query",
    "follow_up",
    "correction",
    "clarification",
    "acknowledgment",
]


@dataclass(frozen=True)
class ConversationIntent:
    type: ConversationIntentType
    clarification_style: str | None = None
    needs_reference_resolution: bool = False
    reason: str | None = None


ACK_PATTERNS = (
    r"^(ok|okay|kk|k|got it|thanks|thank you|thx|cool|great|nice|understood|makes sense)$",
    r"^(👍|👌|✅)$",
)

CORRECTION_PATTERNS = (
    r"^(actually|correction|i meant|i misspoke|not exactly|no[, ]|that's wrong|let me correct|to clarify)\b",
)

CLARIFICATION_PATTERNS: list[tuple[str, str]] = [
    (r"\bshorter\b", "shorter"),
    (r"\bbrief\b", "shorter"),
    (r"\btldr\b|\btl;dr\b", "shorter"),
    (r"\bsimpler\b|\bmore simply\b", "simpler"),
    (r"\bexplain again\b|\brephrase\b|\breword\b", "rephrase"),
    (r"\bsummarize\b|\bsummary\b|\bin short\b", "summary"),
    (r"\bmore detail\b|\bexpand\b|\bgo deeper\b|\bmore depth\b", "expand"),
    (r"\bless technical\b|\bno jargon\b|\bplain english\b", "less_technical"),
    (r"\bmore technical\b|\bdeeper technical\b", "more_technical"),
    (r"\bstep by step\b|\bwalk me through\b", "step_by_step"),
    (r"\bgive an example\b|\bexample\b", "example"),
]

FOLLOW_UP_TRIGGERS = (
    r"\bwhat about\b",
    r"\bwhat if\b",
    r"\bwhy\b",
    r"\bhow\b",
    r"\bso what\b",
    r"\bthen what\b",
    r"\bcan you (expand|elaborate|clarify)\b",
    r"\bexpand on that\b",
)

IMPLICIT_REFERENCE_TERMS = (
    r"\bthis\b",
    r"\bthat\b",
    r"\bit\b",
    r"\bthose\b",
    r"\bthese\b",
    r"\bthey\b",
    r"\bthem\b",
    r"\bthere\b",
)


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def is_acknowledgment(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if len(normalized) > 32:
        return False
    if "?" in normalized:
        return False
    return any(re.match(pattern, normalized) for pattern in ACK_PATTERNS)


def _match_clarification(text: str) -> str | None:
    normalized = _normalize(text)
    for pattern, label in CLARIFICATION_PATTERNS:
        if re.search(pattern, normalized):
            return label
    return None


def _looks_like_follow_up(text: str, *, has_prior: bool) -> bool:
    if not has_prior:
        return False
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(re.search(pattern, normalized) for pattern in FOLLOW_UP_TRIGGERS):
        return True
    if "?" in normalized and len(normalized.split()) <= 8:
        return True
    if any(re.search(pattern, normalized) for pattern in IMPLICIT_REFERENCE_TERMS):
        return True
    return False


def is_low_signal_turn(text: str) -> bool:
    return is_acknowledgment(text)


def classify_conversation_intent(text: str, *, has_prior: bool) -> ConversationIntent:
    normalized = _normalize(text)
    if is_acknowledgment(normalized):
        return ConversationIntent(type="acknowledgment", reason="acknowledgment")

    if has_prior and any(re.match(pattern, normalized) for pattern in CORRECTION_PATTERNS):
        return ConversationIntent(type="correction", reason="correction")

    clarification = _match_clarification(normalized) if has_prior else None
    if clarification:
        return ConversationIntent(
            type="clarification",
            clarification_style=clarification,
            reason="clarification",
        )

    if _looks_like_follow_up(normalized, has_prior=has_prior):
        needs_reference_resolution = any(
            re.search(pattern, normalized) for pattern in IMPLICIT_REFERENCE_TERMS
        )
        return ConversationIntent(
            type="follow_up",
            needs_reference_resolution=needs_reference_resolution,
            reason="follow_up",
        )

    return ConversationIntent(type="new_query", reason="new_query")


def build_intent_system_prompt(
    intent: ConversationIntent,
    *,
    correction_text: str | None = None,
    clarification_text: str | None = None,
) -> str | None:
    if intent.type == "correction":
        correction = (correction_text or "").strip()
        if correction:
            return (
                "User correction (authoritative): "
                f"\"{correction}\". Update your response accordingly and "
                "treat the correction as replacing any conflicting earlier info."
            )
        return "The user is correcting earlier information. Treat the correction as authoritative."

    if intent.type == "clarification":
        clarification = (clarification_text or "").strip()
        style = intent.clarification_style or "clarify"
        if clarification:
            return (
                f"The user is asking for a clarification ({style}). "
                f"Rewrite your previous response accordingly. Request: \"{clarification}\"."
            )
        return f"The user is asking for a clarification ({style}). Rewrite your previous response accordingly."

    if intent.type == "follow_up" and intent.needs_reference_resolution:
        return (
            "The user is asking a follow-up that references prior context. "
            "Resolve pronouns and implicit references using the conversation history."
        )

    return None
