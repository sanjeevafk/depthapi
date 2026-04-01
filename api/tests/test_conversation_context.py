from services.conversation_context import build_context_messages


def _msg(role: str, content: str):
    return {"role": role, "content": content}


def test_preserve_first_turns_on_truncation():
    messages = [
        _msg("system", "You are a helpful tutor."),
        _msg("user", "Question one"),
        _msg("assistant", "Answer one"),
        _msg("user", "Question two"),
        _msg("assistant", "Answer two"),
    ]

    for index in range(12):
        messages.append(
            _msg("user", f"Later question {index} " + ("extra " * 40))
        )
        messages.append(
            _msg("assistant", f"Later answer {index} " + ("details " * 40))
        )

    context_messages, _ = build_context_messages(
        messages,
        max_tokens=80,
        summary_max_tokens=0,
    )

    contents = [msg["content"] for msg in context_messages]

    assert "Question one" in contents
    assert "Answer one" in contents
    assert "Question two" in contents
    assert "Answer two" in contents
