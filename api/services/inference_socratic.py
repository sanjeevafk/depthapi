"""Socratic response helpers."""

from __future__ import annotations

import re

from config import get_settings


def _normalize_question_signature(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()


def _extract_socratic_questions(response: str) -> list[str]:
    if not isinstance(response, str) or not response.strip():
        return []

    candidates = [segment.strip() for segment in re.findall(r"[^?]*\?", response)]
    if not candidates:
        return []

    unique_questions: list[str] = []
    seen_signatures: set[str] = set()
    for question in candidates:
        signature = _normalize_question_signature(question)
        if not signature or signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_questions.append(question)

    return unique_questions


_DEFAULT_DIRECT_ANSWER_PATTERNS = (
    r"\bjust tell me\b",
    r"\bjust give me\b",
    r"\bgive me the answer\b",
    r"\btell me the answer\b",
    r"\banswer directly\b",
    r"\bno questions\b",
    r"\bstop asking\b",
    r"\bwhat is the answer\b",
    r"\bplease answer\b",
)


def _wants_direct_answer(text: str) -> bool:
    patterns = _get_direct_answer_patterns()
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in patterns)


def _get_direct_answer_patterns() -> tuple[str, ...]:
    settings = get_settings()
    raw = getattr(settings, "socratic_direct_answer_patterns", "") or ""
    raw = str(raw).strip()
    if not raw:
        return _DEFAULT_DIRECT_ANSWER_PATTERNS
    parts = [part.strip() for part in re.split(r"[,\n]+", raw) if part.strip()]
    return tuple(parts) if parts else _DEFAULT_DIRECT_ANSWER_PATTERNS


def _fallback_socratic_question(topic: str | None) -> str:
    topic_text = (topic or "").strip()
    if topic_text:
        return f"What specific factor most shapes your view on {topic_text}?"
    return "What specific factor most shapes your view here?"


def _enforce_socratic_response_constraints(
    response: str,
    *,
    topic: str | None = None,
    wants_direct_answer: bool = False,
) -> str:
    """Return a Socratic reply constrained to up to 3 unique questions, or answer+questions."""
    cleaned = (response or "").strip()
    questions = _extract_socratic_questions(cleaned)
    max_questions = 3
    footer = "Share your answer, and I will guide the next step."

    if wants_direct_answer:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        answer_line = None
        if lines:
            if not lines[0].endswith("?"):
                answer_line = lines[0]
            else:
                for line in lines[1:]:
                    if not line.endswith("?"):
                        answer_line = line
                        break
        if not answer_line:
            sentence = re.split(r"[.!?]\s+", cleaned, maxsplit=1)[0].strip()
            if sentence and not sentence.endswith("?"):
                answer_line = sentence
        if not answer_line:
            answer_line = cleaned

        selected_questions = questions[:max_questions]
        if not selected_questions:
            selected_questions = [_fallback_socratic_question(topic)]
        
        q_text = "\n\n".join(selected_questions)
        return f"{answer_line}\n\n{q_text}\n\n{footer}".strip()

    if questions:
        q_text = "\n\n".join(questions[:max_questions])
        return f"{q_text}\n\n{footer}".strip()

    fallback = _fallback_socratic_question(topic)
    return f"{fallback}\n\n{footer}".strip()
