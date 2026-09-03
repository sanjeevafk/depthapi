"""
BaseIngestor — shared logic for all corpus ingest scripts.

Handles:
- Chunk deduplication by SHA-256 content hash
- Loading / appending to chunks.json
- Writing raw chunks (embedding is a separate step, or done per-ingestor)
- Progress reporting
- Normalisation of whitespace / control characters

v2 additions:
- Stable deterministic chunk IDs (content_hash-based)
- doc_id for document-level grouping
- raw_text / cleaned_text dual storage for hybrid retrieval
- version field for pipeline-level traceability
- Incremental indexing via content_hash deduplication
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable
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
    # ── Identity ──────────────────────────────────────────────────────────────
    id: str           # sha256[:16] of cleaned_text — stable dedup key (v1 compat)
    doc_id: str       # Stable document-level ID (e.g. sha256 of source_name+url)
    chunk_id: str     # Deterministic chunk ID: "<doc_id>#c<order:04d>"
    content_hash: str # Full SHA-256 of cleaned_text for incremental indexing
    version: str      # Pipeline version tag, e.g. "v2"
    # ── Text ──────────────────────────────────────────────────────────────────
    content: str      # Alias for cleaned_text (backward compat)
    raw_text: str     # Unmodified text — preserved for BM25 / lexical search
    cleaned_text: str # NFC-normalised, whitespace-collapsed — used for embeddings
    # ── Source ────────────────────────────────────────────────────────────────
    source_name: str
    source_url: str | None
    chunk_order: int
    token_count: int
    source_type: str  # "markdown" | "html" | "pdf" | "qa_pair"
    tags: list[str]   # e.g. ["python", "stdlib", "P0"]
    metadata: dict[str, Any] | None = None


# ─── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "rag" / "trusted"
CHUNKS_FILE = DATA_DIR / "chunks.json"


# ─── Text utilities ───────────────────────────────────────────────────────────
_CTRL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MULTI_NL = re.compile(r"\n{3,}")
_MULTI_SP = re.compile(r"[ \t]{2,}")


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
    """Return first 16 hex chars of SHA-256 — backward-compat dedup key."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def content_hash(content: str) -> str:
    """Return full SHA-256 hex digest of content for incremental indexing."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_doc_id(source_name: str, source_url: str | None) -> str:
    """Deterministic document-level ID based on name + URL."""
    key = f"{source_name}::{source_url or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


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
        near_dup_threshold: int | None = 4,
    ):
        """
        Args:
            source_name:       Human-readable corpus label.
            source_type:       One of 'pdf', 'markdown', 'html', 'prose'.
            validators:        Optional list of ChunkValidator callables.
            near_dup_threshold: Hamming distance threshold for SimHash near-dup
                               detection (0-64). None disables near-dup checks.
                               Default 4 ≈ 94% text similarity.
        """
        self.source_name = source_name
        self.source_type = source_type
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._existing_ids = self._load_existing_ids()
        self.new_count = 0
        self.skip_count = 0
        self.skip_stats = {
            "too_short": 0,
            "validator_reject": 0,
            "duplicate": 0,
            "near_duplicate": 0,
        }
        self._buffer: list[Chunk] = []
        self._validators = list(validators) if validators else []

        # Near-duplicate filter (SimHash, optional)
        if near_dup_threshold is not None:
            from api.services.rag.pipeline.chunkers.legacy.neardup_filter import NearDupFilter
            self._near_dup: NearDupFilter | None = NearDupFilter(threshold=near_dup_threshold)
        else:
            self._near_dup = None

    def _load_existing_ids(self) -> set[str]:
        if not CHUNKS_FILE.exists():
            return set()
        try:
            with CHUNKS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return {c["id"] for c in data if "id" in c}
        except (json.JSONDecodeError, KeyError):
            log.warning(
                f"Failed to load existing IDs from {CHUNKS_FILE}, starting fresh."
            )
            return set()

    def _make_chunk(
        self,
        content: str,
        order: int,
        source_url: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        raw_text: str | None = None,
        version: str = "v1",
        doc_id: str | None = None,
    ) -> Chunk | None:
        cleaned = clean_text(content)
        if len(cleaned) < 50:
            self.skip_stats["too_short"] += 1
            return None
        for validator in self._validators:
            if not validator(cleaned):
                self.skip_count += 1
                self.skip_stats["validator_reject"] += 1
                return None
        cid = chunk_id(cleaned)
        if cid in self._existing_ids:
            self.skip_count += 1
            self.skip_stats["duplicate"] += 1
            return None
        self._existing_ids.add(cid)
        _doc_id = doc_id or make_doc_id(self.source_name, source_url)
        return Chunk(
            id=cid,
            doc_id=_doc_id,
            chunk_id=f"{_doc_id}#c{order:04d}",
            content_hash=content_hash(cleaned),
            version=version,
            content=cleaned,
            raw_text=raw_text if raw_text is not None else cleaned,
            cleaned_text=cleaned,
            source_name=self.source_name,
            source_url=source_url,
            chunk_order=order,
            token_count=rough_token_count(cleaned),
            source_type=self.source_type,
            tags=tags or [],
            metadata=metadata or None,
        )

    def add(
        self,
        texts: list[str],
        source_url: str | None = None,
        tags: list[str] | None = None,
        start_order: int = 0,
        metadata: list[dict[str, Any] | None] | None = None,
    ) -> list[Chunk]:
        added: list[Chunk] = []
        for i, text in enumerate(texts):
            row_meta = metadata[i] if metadata and i < len(metadata) else None
            chunk = self._make_chunk(text, start_order + i, source_url, tags, row_meta)
            if chunk:
                self._buffer.append(chunk)
                self.new_count += 1
                added.append(chunk)
        return added

    def add_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """
        Accept pre-built Chunk objects (e.g. from HierarchicalSemanticChunker).

        Runs, in order:
          1. Minimum-length guard  (< 50 chars)
          2. Validator chain
          3. Exact dedup          (content_hash[:16])
          4. Near-dup detection   (SimHash, if enabled)

        Args:
            chunks: Chunk objects with all fields already populated.

        Returns:
            List of chunks accepted into the buffer.
        """
        added: list[Chunk] = []
        for chunk in chunks:
            # 1. Minimum length guard
            if len(chunk.cleaned_text) < 50:
                self.skip_stats["too_short"] += 1
                self.skip_count += 1
                continue

            # 2. Validator chain
            rejected = False
            for validator in self._validators:
                if not validator(chunk.cleaned_text):
                    self.skip_count += 1
                    self.skip_stats["validator_reject"] += 1
                    rejected = True
                    break
            if rejected:
                continue

            # 3. Exact dedup by short ID (content_hash[:16])
            if chunk.id in self._existing_ids:
                self.skip_count += 1
                self.skip_stats["duplicate"] += 1
                continue

            # 4. SimHash near-duplicate detection
            if self._near_dup and self._near_dup.is_duplicate(chunk.cleaned_text):
                self.skip_count += 1
                self.skip_stats["near_duplicate"] += 1
                continue

            self._existing_ids.add(chunk.id)
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

        nd_stats = self._near_dup.stats() if self._near_dup else {}
        log.info(
            f"[{self.source_name}] wrote {len(self._buffer)} new chunks "
            f"| skipped: exact_dup={self.skip_stats['duplicate']} "
            f"near_dup={self.skip_stats['near_duplicate']} "
            f"short={self.skip_stats['too_short']} "
            f"validator={self.skip_stats['validator_reject']} "
            f"→ total {len(existing)}"
        )
        if nd_stats:
            log.info(
                f"  SimHash stats: checked={nd_stats['total_checked']} "
                f"rejected={nd_stats['near_dup_rejected']} "
                f"unique_fps={nd_stats['unique_fingerprints']} "
                f"threshold={nd_stats['threshold']}"
            )
        self._buffer.clear()
        return len(existing)

    def _total_count(self) -> int:
        if not CHUNKS_FILE.exists():
            return 0
        with CHUNKS_FILE.open("r", encoding="utf-8") as f:
            return len(json.load(f))
