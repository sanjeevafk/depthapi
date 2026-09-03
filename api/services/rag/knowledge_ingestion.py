"""Local ingestion compatibility helpers.

Compatibility adapter preserving the IngestionWorker chunking interface
for offline callers and test suites, backed by depth_engine with pure-Python fallback.
"""
from __future__ import annotations

from typing import Any

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


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
        if _HAS_DEPTH_ENGINE and depth_engine is not None:
            try:
                chunks = depth_engine.chunk_markdown(
                    markdown=text,
                    doc_id=doc_id,
                    source_name=source_name,
                    source_url=source_url,
                    dataset_version="v2",
                    max_tokens=512,
                    min_tokens=5,
                )
                return [
                    {
                        "doc_id": c["doc_id"],
                        "chunk_id": f"{doc_id}#c{c['chunk_order']:03d}",
                        "content": c["content"],
                        "token_count": c["token_count"],
                        "chunk_order": c["chunk_order"],
                        "source_name": source_name,
                        "source_url": source_url,
                        "section_title": (c.get("metadata") or {}).get("hierarchy", [""])[-1] if (c.get("metadata") or {}).get("hierarchy") else "Overview",
                        "metadata": c.get("metadata") or {},
                    }
                    for c in chunks
                ]
            except Exception:
                pass

        # Fallback pure-Python block splitting
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
