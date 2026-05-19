from __future__ import annotations

from collections import Counter

from .config import ValidationConfig


def _is_broken_markdown(text: str) -> bool:
    return text.count("```") % 2 == 1


def _repeated_boilerplate_score(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    repeats = sum(1 for line in set(lines) if lines.count(line) > 1 and len(line) > 30)
    return repeats / max(1, len(lines))


def validate_chunks(chunks: list[dict], config: ValidationConfig) -> dict:
    issues = Counter()
    bad_examples: list[dict] = []

    for chunk in chunks:
        text = str(chunk.get("content") or "")
        reason: list[str] = []
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            issues["malformed_utf8"] += 1
            reason.append("malformed_utf8")
        if not text.strip():
            issues["empty_chunks"] += 1
            reason.append("empty")
        if len(text) < config.min_chars:
            issues["extremely_short_chunks"] += 1
            reason.append("short")
        if len(text) > config.max_chars:
            issues["extremely_long_chunks"] += 1
            reason.append("long")
        if _is_broken_markdown(text):
            issues["broken_markdown"] += 1
            reason.append("broken_markdown")
        if _repeated_boilerplate_score(text) >= config.boilerplate_threshold:
            issues["repeated_boilerplate"] += 1
            reason.append("boilerplate")
        if text.count("```") % 2 == 1:
            issues["code_fence_corruption"] += 1
            reason.append("code_fence_corruption")
        if reason and len(bad_examples) < 25:
            bad_examples.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "reasons": reason,
                    "preview": text[:180],
                }
            )

    return {
        "total_chunks": len(chunks),
        "issue_counts": dict(issues),
        "examples": bad_examples,
    }
