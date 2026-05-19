from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from .config import ChunkingConfig
from .models import ChunkRecord


CODE_FENCE_RE = re.compile(r"^```")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ChunkingReport:
    documents: int
    chunks: int
    avg_chunk_chars: float
    max_chunk_chars: int
    chunker_version: str
    tokenizer: str
    chunk_size: int
    overlap: int


class DeterministicChunker:
    def __init__(self, config: ChunkingConfig):
        self.config = config

    def chunk_documents(self, documents: list[dict]) -> tuple[list[dict], dict]:
        chunks: list[dict] = []
        total_chars = 0
        max_chunk_chars = 0
        for document in documents:
            doc_chunks = self.chunk_document(document)
            chunks.extend(doc_chunks)
            for chunk in doc_chunks:
                chunk_chars = len(chunk["content"])
                total_chars += chunk_chars
                max_chunk_chars = max(max_chunk_chars, chunk_chars)

        report = ChunkingReport(
            documents=len(documents),
            chunks=len(chunks),
            avg_chunk_chars=round(total_chars / max(1, len(chunks)), 2),
            max_chunk_chars=max_chunk_chars,
            chunker_version=self.config.chunker_version,
            tokenizer=self.config.tokenizer,
            chunk_size=self.config.chunk_size,
            overlap=self.config.overlap,
        )
        return chunks, report.__dict__

    def chunk_document(self, document: dict) -> list[dict]:
        blocks = self._parse_markdown_blocks(str(document.get("content") or ""))
        headings: list[str] = []
        emitted: list[dict] = []
        current: list[str] = []
        chunk_index = 0

        def flush() -> None:
            nonlocal current, chunk_index
            text = "\n\n".join(part for part in current if part.strip()).strip()
            if not text:
                current = []
                return
            normalized = text.strip()
            chunk_hash = sha256(normalized.encode("utf-8")).hexdigest()
            emitted.append(
                ChunkRecord(
                    chunk_id=f"{document['document_id']}:{chunk_index:05d}",
                    source=str(document.get("source") or "unknown"),
                    source_url=str(document.get("source_url") or ""),
                    upstream_license=str(document.get("upstream_license") or "unknown"),
                    document_id=str(document["document_id"]),
                    chunk_index=chunk_index,
                    retrieved_at=str(document.get("retrieved_at") or ""),
                    chunker_version=self.config.chunker_version,
                    content_hash=chunk_hash,
                    content=normalized,
                    title=str(document.get("title") or ""),
                    namespace=str(document.get("namespace") or ""),
                    token_count=max(1, len(normalized) // 4),
                    headings=list(headings),
                    metadata=dict(document.get("metadata") or {}),
                ).to_dict()
            )
            chunk_index += 1
            current = self._overlap_seed(normalized)

        for block_type, value in blocks:
            if block_type == "heading":
                flush()
                level, heading_text = value
                headings[:] = headings[: level - 1]
                headings.append(heading_text)
                current = [("#" * level) + " " + heading_text]
                continue

            candidate = "\n\n".join(current + [value]).strip()
            if len(candidate) <= self.config.chunk_size:
                current.append(value)
                continue

            if block_type == "code":
                flush()
                current = [value]
                flush()
                continue

            for sub_part in self._semantic_split(value):
                candidate = "\n\n".join(current + [sub_part]).strip()
                if len(candidate) > self.config.chunk_size and current:
                    flush()
                current.append(sub_part)

        flush()
        return emitted

    def _semantic_split(self, text: str) -> list[str]:
        soft_limit = int(self.config.semantic_thresholds["max_section_chars"])
        if len(text) <= soft_limit:
            return [text]

        sentences = [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > soft_limit:
                parts.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts or [text]

    def _overlap_seed(self, text: str) -> list[str]:
        if self.config.overlap <= 0:
            return []
        if text.count("```") % 2 == 1:
            return []
        tail = text[-self.config.overlap :].strip()
        if tail.count("```") % 2 == 1:
            return []
        return [tail] if tail else []

    def _parse_markdown_blocks(self, text: str) -> list[tuple[str, object]]:
        lines = text.splitlines()
        blocks: list[tuple[str, object]] = []
        buffer: list[str] = []
        in_code = False
        code_lines: list[str] = []

        def flush_buffer() -> None:
            nonlocal buffer
            if buffer:
                blocks.append(("text", "\n".join(buffer).strip()))
                buffer = []

        for line in lines:
            if CODE_FENCE_RE.match(line.strip()):
                if in_code:
                    code_lines.append(line)
                    blocks.append(("code", "\n".join(code_lines).strip()))
                    code_lines = []
                    in_code = False
                else:
                    flush_buffer()
                    in_code = True
                    code_lines = [line]
                continue

            if in_code:
                code_lines.append(line)
                continue

            heading_match = HEADING_RE.match(line.strip())
            if heading_match:
                flush_buffer()
                blocks.append(("heading", (len(heading_match.group(1)), heading_match.group(2).strip())))
                continue

            if line.strip():
                buffer.append(line.rstrip())
            else:
                flush_buffer()
        flush_buffer()
        if code_lines:
            blocks.append(("code", "\n".join(code_lines).strip()))
        return [(kind, value) for kind, value in blocks if value]
