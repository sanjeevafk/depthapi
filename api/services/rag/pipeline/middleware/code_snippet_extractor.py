"""
code_snippet_extractor.py — Middleware: Extract code blocks and tag languages.

Extracts the primary code language from the markdown content and adds it
to the document metadata, keeping the prose and code together but tagged.
"""

from __future__ import annotations

import re
from typing import Any

from api.services.rag.pipeline.interfaces import BaseMiddleware
from api.services.rag.pipeline.models import ParsedDocument

_MW_NAME = "CodeSnippetExtractor"
_MW_VERSION = "1.0.0"

# Pattern to find the first markdown code block language
_CODE_BLOCK_PATTERN = re.compile(r"```([a-zA-Z0-9_+-]+)\s*\n")


class CodeSnippetExtractor(BaseMiddleware):
    """
    Middleware: Detect the primary programming language from code blocks
    in the markdown content and tag it in metadata.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    @property
    def name(self) -> str:
        return _MW_NAME

    @property
    def version(self) -> str:
        return _MW_VERSION

    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """Find the first code block and extract its language."""
        match = _CODE_BLOCK_PATTERN.search(doc.markdown_content)
        code_lang = match.group(1).lower() if match else "unknown"

        new_metadata = dict(doc.metadata)
        new_metadata["code_lang"] = code_lang

        doc_updated = doc.with_middleware_applied(
            middleware_name=self.name,
            middleware_version=self.version,
            new_content=doc.markdown_content,
            config=self._config,
        )

        return doc_updated.model_copy(update={"metadata": new_metadata})
