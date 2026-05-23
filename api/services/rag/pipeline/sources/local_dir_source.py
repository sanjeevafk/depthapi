"""
local_dir_source.py — Source plugin for local filesystem directories.

Scans a directory for matching files and yields Document objects.
Supports incremental mode via SHA-256 content hash comparison.

Config keys:
    base_path: str — Path to directory to scan (relative to repo root or absolute)
    include:   List[str] — Glob patterns to include (e.g. ["*.md", "*.rst"])
    recursive: bool — Recurse into subdirectories (default: True)
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from api.services.rag.pipeline.interfaces import BaseSource
from api.services.rag.pipeline.models import Document, SourceFingerprint

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[5]

# MIME type map for extensions not in mimetypes stdlib
_EXTENSION_MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".txt": "text/plain",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".json": "application/json",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
}


def _resolve_path(base_path: str) -> Path:
    """Resolve base_path relative to repo root if not absolute."""
    p = Path(base_path)
    if p.is_absolute():
        return p
    return _REPO_ROOT / p


def _mime_for_path(path: Path) -> str:
    """Determine MIME type from file extension."""
    ext = path.suffix.lower()
    if ext in _EXTENSION_MIME:
        return _EXTENSION_MIME[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class LocalDirSource(BaseSource):
    """
    Source plugin: yields Documents from all matching files in a directory.

    Supports:
        - Glob-based file filtering (include patterns)
        - Recursive or flat directory scan
        - Incremental mode: skip files with unchanged SHA-256 hash
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._base_path = _resolve_path(config.get("base_path", "."))
        self._include = config.get("include", ["*.md"])
        self._recursive = config.get("recursive", True)

    @property
    def name(self) -> str:
        return "LocalDirSource"

    def validate_config(self, config: dict[str, Any]) -> bool:
        base = _resolve_path(config.get("base_path", "."))
        if not base.exists():
            raise ValueError(f"LocalDirSource: base_path does not exist: {base}")
        return True

    def fetch(
        self,
        since: dict[str, SourceFingerprint] | None = None,
    ) -> Iterator[tuple[Document, SourceFingerprint]]:
        """Yield (Document, Fingerprint) for each matching file."""
        if not self._base_path.exists():
            log.warning(f"LocalDirSource: base_path does not exist: {self._base_path}")
            return

        for pattern in self._include:
            glob_fn = self._base_path.rglob if self._recursive else self._base_path.glob
            for file_path in sorted(glob_fn(pattern)):
                if not file_path.is_file():
                    continue

                try:
                    raw_content = file_path.read_bytes()
                except OSError as exc:
                    log.warning(f"Cannot read {file_path}: {exc}")
                    continue

                content_hash = hashlib.sha256(raw_content).hexdigest()
                source_uri = f"file://{file_path.resolve()}"

                # Incremental: skip if fingerprint unchanged
                if since and source_uri in since:
                    prev_fp = since[source_uri]
                    if prev_fp.content_hash == content_hash:
                        log.debug(f"Skipping unchanged file: {file_path.name}")
                        continue

                doc = Document.from_bytes(
                    source_uri=source_uri,
                    raw_content=raw_content,
                    mime_type=_mime_for_path(file_path),
                    source_last_modified=datetime.fromtimestamp(
                        file_path.stat().st_mtime
                    ),
                    metadata={"file_name": file_path.name, "file_path": str(file_path)},
                )

                fp = SourceFingerprint(
                    source_uri=source_uri,
                    last_fetch_timestamp=datetime.utcnow(),
                    content_hash=content_hash,
                )

                log.debug(f"Yielding document: {file_path.name}")
                yield doc, fp
