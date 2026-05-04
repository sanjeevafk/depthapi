"""
BaseIngestor — shared logic for all corpus ingest scripts.

Handles:
- Chunk deduplication by SHA-256 content hash
- Loading / appending to chunks.json
- Writing raw chunks (embedding is a separate step, or done per-ingestor)
- Progress reporting
- Normalisation of whitespace / control characters
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Callable, Iterable
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")


# ─── Data model ───────────────────────────────────────────────────────────────
@dataclass
class Chunk:
    id: str           # sha256 of content (first 16 hex chars)
    content: str
    source_name: str
    source_url: str | None
    chunk_order: int
    token_count: int
    source_type: str  # "markdown" | "html" | "pdf" | "qa_pair"
    tags: list[str]   # e.g. ["python", "stdlib", "P0"]


# ─── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR  = REPO_ROOT / "data" / "rag" / "trusted"
CHUNKS_FILE = DATA_DIR / "chunks.json"


# ─── Text utilities ───────────────────────────────────────────────────────────
_CTRL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MULTI_NL   = re.compile(r"\n{3,}")
_MULTI_SP   = re.compile(r"[ \t]{2,}")


def clean_text(text: str) -> str:
    """Strip control chars, normalise whitespace, NFC-normalise unicode."""
    text = unicodedata.normalize("NFC", text)
    text = _CTRL_CHARS.sub("", text)
    text = _MULTI_SP.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def rough_token_count(text: str) -> int:
    """~4 chars per token (GPT-style approximation — good enough for budgeting)."""
    return max(1, len(text) // 4)


def chunk_id(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ─── Recursive character splitter ────────────────────────────────────────────
def split_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 100,
    separators: list[str] | None = None,
) -> list[str]:
    """
    Splits text by trying separators in order (markdown-aware by default).
    Falls back to character-level splitting.
    """
    if separators is None:
        separators = ["\n## ", "\n### ", "\n\n", "\n", " ", ""]

    def _split(text: str, seps: list[str]) -> list[str]:
        if not seps or len(text) <= chunk_size:
            return [text] if text.strip() else []
        sep = seps[0]
        parts = text.split(sep) if sep else list(text)
        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = (current + sep + part).strip() if current else part.strip()
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # Part itself too long — recurse into next separator
                if len(part) > chunk_size:
                    chunks.extend(_split(part, seps[1:]))
                    current = ""
                else:
                    current = part.strip()
        if current:
            chunks.append(current)
        return chunks

    raw = _split(text, separators)

    # Apply overlap by stitching previous tail onto next chunk
    result: list[str] = []
    for i, chunk in enumerate(raw):
        if i == 0 or overlap == 0:
            result.append(chunk)
        else:
            tail = raw[i - 1][-overlap:]
            result.append((tail + "\n" + chunk).strip())

    return [c for c in result if len(c.strip()) >= 50]  # drop tiny fragments


def split_text_semantic(
    text: str,
    chunk_size: int = 800,
    overlap_words: int = 25,
) -> list[str]:
    """Sentence-aware splitter that avoids mid-word fragments."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []

    def _sentences(para: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", para.strip())
        return [p.strip() for p in parts if p.strip()]

    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        sentences = _sentences(para)
        if not sentences:
            continue
        for sent in sentences:
            sent_len = len(sent)
            if current_len + sent_len + 1 <= chunk_size:
                current.append(sent)
                current_len += sent_len + 1
            else:
                if current:
                    chunks.append(" ".join(current).strip())
                current = [sent]
                current_len = sent_len

        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_len = 0

    if current:
        chunks.append(" ".join(current).strip())

    if overlap_words <= 0:
        return [c for c in chunks if len(c.strip()) >= 50]

    overlapped: list[str] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            overlapped.append(chunk)
            continue
        prev_words = chunks[i - 1].split()
        tail = " ".join(prev_words[-overlap_words:])
        overlapped.append((tail + "\n" + chunk).strip())

    return [c for c in overlapped if len(c.strip()) >= 50]


def split_by_header(
    text: str,
    header_prefix: str = "###",
    chunk_size: int = 500,
    overlap: int = 100,
) -> list[str]:
    """Split markdown at header boundaries first, then recursively."""
    sections = re.split(rf"(?m)^{re.escape(header_prefix)} ", text)
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append(section)
        else:
            chunks.extend(split_text(section, chunk_size=chunk_size, overlap=overlap))
    return chunks


def split_by_header_semantic(
    text: str,
    header_prefix: str = "###",
    chunk_size: int = 800,
    overlap_words: int = 25,
) -> list[str]:
    """Split markdown at header boundaries, then sentence-aware splitting."""
    sections = re.split(rf"(?m)^{re.escape(header_prefix)} ", text)
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append(section)
        else:
            chunks.extend(
                split_text_semantic(
                    section,
                    chunk_size=chunk_size,
                    overlap_words=overlap_words,
                )
            )
    return chunks


ChunkValidator = Callable[[str], bool]


def make_min_word_validator(min_words: int = 30) -> ChunkValidator:
    def _validate(text: str) -> bool:
        return len(text.split()) >= min_words
    return _validate


def make_link_ratio_validator(max_ratio: float = 0.2) -> ChunkValidator:
    def _validate(text: str) -> bool:
        words = text.split()
        if not words:
            return False
        link_count = len(re.findall(r"https?://", text))
        return (link_count / max(1, len(words))) <= max_ratio
    return _validate


def make_markdown_toc_validator() -> ChunkValidator:
    def _validate(text: str) -> bool:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return False
        anchor_lines = [ln for ln in lines if re.search(r"\]\(#.+?\)", ln)]
        if len(lines) >= 3 and len(anchor_lines) / len(lines) > 0.6:
            return False
        return True
    return _validate


# ─── BaseIngestor ─────────────────────────────────────────────────────────────
class BaseIngestor:
    def __init__(
        self,
        source_name: str,
        source_type: str = "prose",
        validators: Iterable[ChunkValidator] | None = None,
    ):
        self.source_name = source_name
        self.source_type = source_type
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._existing_ids = self._load_existing_ids()
        self.new_count = 0
        self.skip_count = 0
        self._buffer: list[Chunk] = []
        self._validators = list(validators) if validators else []

    def _load_existing_ids(self) -> set[str]:
        if not CHUNKS_FILE.exists():
            return set()
        with CHUNKS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {c["id"] for c in data}

    def _make_chunk(
        self,
        content: str,
        order: int,
        source_url: str | None = None,
        tags: list[str] | None = None,
    ) -> Chunk | None:
        content = clean_text(content)
        if len(content) < 50:
            return None
        for validator in self._validators:
            if not validator(content):
                self.skip_count += 1
                return None
        cid = chunk_id(content)
        if cid in self._existing_ids:
            self.skip_count += 1
            return None
        self._existing_ids.add(cid)
        return Chunk(
            id=cid,
            content=content,
            source_name=self.source_name,
            source_url=source_url,
            chunk_order=order,
            token_count=rough_token_count(content),
            source_type=self.source_type,
            tags=tags or [],
        )

    def add(
        self,
        texts: list[str],
        source_url: str | None = None,
        tags: list[str] | None = None,
        start_order: int = 0,
    ) -> list[Chunk]:
        added: list[Chunk] = []
        for i, text in enumerate(texts):
            chunk = self._make_chunk(text, start_order + i, source_url, tags)
            if chunk:
                self._buffer.append(chunk)
                self.new_count += 1
                added.append(chunk)
        return added

    def flush(self) -> int:
        """Write buffered chunks to chunks.json. Returns total chunk count."""
        if not self._buffer:
            log.info("No new chunks to write.")
            return self._total_count()

        existing: list[dict] = []
        if CHUNKS_FILE.exists():
            with CHUNKS_FILE.open("r", encoding="utf-8") as f:
                existing = json.load(f)

        existing.extend([asdict(c) for c in self._buffer])

        with CHUNKS_FILE.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        log.info(
            f"[{self.source_name}] wrote {len(self._buffer)} new chunks "
            f"(skipped {self.skip_count} dupes) → total {len(existing)}"
        )
        self._buffer.clear()
        return len(existing)

    def _total_count(self) -> int:
        if not CHUNKS_FILE.exists():
            return 0
        with CHUNKS_FILE.open("r", encoding="utf-8") as f:
            return len(json.load(f))
