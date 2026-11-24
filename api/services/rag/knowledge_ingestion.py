"""Local ingestion compatibility helpers."""
from __future__ import annotations

from typing import Any


class IngestionWorker:
    """Compatibility facade for text chunking."""

    def __init__(self, worker_id: str = "default-worker") -> None:
        self.worker_id = worker_id

    def chunk_text_with_metadata(
        self,
        text: str,
        *,
        doc_id: str,
        source_name: str,
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Chunk Markdown and return dictionary representation."""
        lines = text.splitlines(keepends=True)
        blocks: list[str] = []
        cur: list[str] = []
        cur_title = "Overview"
        for line in lines:
            if line.startswith("#"):
                if cur:
                    blocks.append((cur_title, "".join(cur).strip()))
                    cur = []
                cur_title = line.strip("# \t\r\n")
            cur.append(line)
        if cur:
            blocks.append((cur_title, "".join(cur).strip()))

        return [
            {
                "doc_id": doc_id,
                "chunk_id": f"{doc_id}#c{i:03d}",
                "content": content,
                "token_count": max(1, len(content) // 4),
                "chunk_order": i,
                "source_name": source_name,
                "source_url": source_url,
                "section_title": title,
                "metadata": {"section_title": title},
            }
            for i, (title, content) in enumerate(blocks)
        ]
