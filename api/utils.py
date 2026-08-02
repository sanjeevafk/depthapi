"""Small request and prompt safety helpers."""
import asyncio
import re
from collections.abc import Awaitable
from typing import TypeVar
T = TypeVar("T")

def sanitize_query(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned: raise ValueError("query must not be empty")
    return cleaned
def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:255]
def escape_for_prompt(value: str) -> str:
    return value.replace("\x00", "")
async def with_timeout(awaitable: Awaitable[T], timeout: float) -> T:
    return await asyncio.wait_for(awaitable, timeout=timeout)
