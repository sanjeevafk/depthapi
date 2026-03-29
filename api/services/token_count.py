from functools import lru_cache

import tiktoken


@lru_cache
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_prompt_tokens(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return len(_encoding().encode(cleaned))
