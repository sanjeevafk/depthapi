from __future__ import annotations

import hashlib
import re
from typing import Iterable, TypedDict

from services.token_count import count_prompt_tokens
from services.conversation_intent import is_low_signal_turn


class ConversationMessage(TypedDict):
    role: str
    content: str


ROLE_OVERHEAD_TOKENS = 4


def _first_sentence(text: str, *, max_chars: int = 240) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""
    match = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    snippet = match[0] if match else cleaned
    if len(snippet) > max_chars:
        snippet = f"{snippet[:max_chars].rstrip()}..."
    return snippet


def _trim_to_token_budget(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if count_prompt_tokens(text) <= max_tokens:
        return text
    approx = max(int(len(text) * max_tokens / max(count_prompt_tokens(text), 1)), 1)
    trimmed = text[:approx].rstrip()
    while trimmed and count_prompt_tokens(trimmed) > max_tokens:
        trimmed = trimmed[:-50].rstrip()
    return trimmed


def _summarize_messages(messages: Iterable[ConversationMessage], max_tokens: int) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        snippet = _first_sentence(content)
        if not snippet:
            continue
        label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
        lines.append(f"{label}: {snippet}")
    if not lines:
        return ""
    summary = "Summary of earlier conversation:\n" + "\n".join(lines)
    return _trim_to_token_budget(summary, max_tokens)


def build_context_messages(
    messages: list[ConversationMessage],
    *,
    max_tokens: int,
    summary_max_tokens: int,
    drop_low_signal: bool = True,
    max_turns: int | None = None,
) -> tuple[list[ConversationMessage], str]:
    if not messages:
        return [], ""

    filtered: list[ConversationMessage] = []
    for msg in messages:
        role = (msg.get("role") or "").strip().lower()
        content = (msg.get("content") or "").strip()
        if not role or role not in {"user", "assistant", "system"}:
            continue
        if not content:
            continue
        if drop_low_signal and role == "user" and is_low_signal_turn(content):
            continue
        filtered.append({"role": role, "content": content})

    if not filtered:
        return [], ""

    if max_turns is not None and max_turns > 0:
        non_system = [msg for msg in filtered if msg["role"] in {"user", "assistant"}]
        keep = max_turns * 2
        if len(non_system) > keep:
            non_system = non_system[-keep:]
        filtered = non_system

    # Reserve space for an optional summary block up front so the final prompt stays within max_tokens.
    effective_message_budget = (
        max_tokens
        if summary_max_tokens <= 0
        else max(0, max_tokens - summary_max_tokens)
    )

    # Sliding window by token budget (prioritize newest).
    selected: list[ConversationMessage] = []
    total_tokens = 0
    for msg in reversed(filtered):
        msg_tokens = count_prompt_tokens(msg["content"]) + ROLE_OVERHEAD_TOKENS
        if total_tokens + msg_tokens > effective_message_budget and selected:
            break
        if msg_tokens > effective_message_budget:
            # Single oversized message: truncate content to fit (account for role overhead).
            content_budget = max(0, effective_message_budget - ROLE_OVERHEAD_TOKENS)
            trimmed_content = _trim_to_token_budget(msg["content"], content_budget)
            if not trimmed_content:
                break
            selected.append({"role": msg["role"], "content": trimmed_content})
            total_tokens = min(
                effective_message_budget,
                ROLE_OVERHEAD_TOKENS + count_prompt_tokens(trimmed_content),
            )
            break
        selected.append(msg)
        total_tokens += msg_tokens

    selected = list(reversed(selected))

    dropped_count = len(filtered) - len(selected)
    summary = ""
    if dropped_count > 0 and summary_max_tokens > 0:
        summary = _summarize_messages(filtered[:dropped_count], summary_max_tokens)

    if summary:
        summary_message: ConversationMessage = {"role": "system", "content": summary}
        selected = [summary_message, *selected]

    signature_base = "\n".join(f"{m['role']}:{m['content']}" for m in selected)
    signature = hashlib.sha256(signature_base.encode("utf-8")).hexdigest()
    return selected, signature


def extract_last_turns(messages: list[ConversationMessage]) -> tuple[str | None, str | None]:
    last_user = None
    last_assistant = None
    for msg in reversed(messages):
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user" and last_user is None:
            last_user = content
        elif role == "assistant" and last_assistant is None:
            last_assistant = content
        if last_user and last_assistant:
            break
    return last_user, last_assistant


def build_socratic_context(messages: list[ConversationMessage]) -> str:
    last_user, _ = extract_last_turns(messages)
    if not last_user:
        return ""
    snippet = _first_sentence(last_user, max_chars=400)
    return f"User last answered: {snippet}"
