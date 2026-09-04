"""
license_appender.py — Middleware: Append license information to metadata.

Adds specific license provenance to the document metadata so it is preserved
throughout chunking and embedding.
"""

from __future__ import annotations

from typing import Any

from api.services.rag.pipeline.interfaces import BaseMiddleware
from api.services.rag.pipeline.models import ParsedDocument

_MW_NAME = "LicenseAppender"
_MW_VERSION = "1.0.0"


class LicenseAppender(BaseMiddleware):
    """
    Middleware: Append a hardcoded or configured license string to metadata.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._license = self._config.get("license", "unknown")

    @property
    def name(self) -> str:
        return _MW_NAME

    @property
    def version(self) -> str:
        return _MW_VERSION

    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """Add license to metadata."""
        new_metadata = dict(doc.metadata)
        new_metadata["license"] = self._license

        doc_updated = doc.with_middleware_applied(
            middleware_name=self.name,
            middleware_version=self.version,
            new_content=doc.markdown_content,
            config=self._config,
        )

        return doc_updated.model_copy(update={"metadata": new_metadata})
