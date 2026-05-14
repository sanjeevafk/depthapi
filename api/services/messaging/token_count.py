from functools import lru_cache
from typing import Optional

import tiktoken

from api.logging_config import logger


@lru_cache
def _encoding():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning("token_count_encoding_unavailable", error=str(exc))
        return None


def count_prompt_tokens(text: Optional[str]) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    encoding = _encoding()
    if encoding is not None:
        return len(encoding.encode(cleaned))
    # Offline-safe estimate: ~4 chars/token with punctuation-aware floor.
    return max(1, (len(cleaned) + 3) // 4)
