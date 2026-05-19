from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SourceDocument:
    document_id: str
    source: str
    source_url: str
    upstream_license: str
    title: str
    retrieved_at: str
    namespace: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChunkRecord:
    chunk_id: str
    source: str
    source_url: str
    upstream_license: str
    document_id: str
    chunk_index: int
    retrieved_at: str
    chunker_version: str
    content_hash: str
    content: str
    title: str = ""
    namespace: str = ""
    token_count: int = 0
    headings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
