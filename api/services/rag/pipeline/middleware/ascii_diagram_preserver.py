"""
ascii_diagram_preserver.py — Middleware: Detect and preserve ASCII diagrams.

ASCII art diagrams (boxes, arrows, grids) are common in system design docs.
This middleware detects them and wraps them in fenced code blocks so chunkers
treat them as atomic units rather than splitting across lines.

Config keys:
    preserve_box_drawings: bool — Wrap box-drawing chars in code fences (default: True)
    min_diagram_lines: int — Min consecutive box lines to trigger wrapping (default: 3)
"""

from __future__ import annotations

import re
from typing import Any

from api.services.rag.pipeline.interfaces import BaseMiddleware
from api.services.rag.pipeline.models import ParsedDocument

_MW_NAME = "AsciiDiagramPreserver"
_MW_VERSION = "1.0.0"

# Characters used in ASCII box drawings
_BOX_CHARS = set("┌┐└┘│─├┤┬┴┼╔╗╚╝║═╠╣╦╩╬+|-=<>^v")

# Regex for ASCII art: lines with box chars or pipe/dash grid patterns
_BOX_LINE_RE = re.compile(r"^[│─┌┐└┘├┤┬┴┼╔╗╚╝║═╠╣╦╩╬+|\-=\s<>^v]{3,}$")
_PIPE_GRID_RE = re.compile(r"\|.+\|")  # |col1|col2|


def _is_box_line(line: str) -> bool:
    """Return True if line looks like part of an ASCII diagram."""
    stripped = line.strip()
    if not stripped:
        return False
    return bool(_BOX_LINE_RE.match(stripped)) or bool(_PIPE_GRID_RE.match(stripped))


def _preserve_ascii_diagrams(content: str, min_lines: int = 3) -> str:
    """
    Detect consecutive ASCII diagram lines and wrap in ```text fences.

    Already-fenced blocks are left unchanged.
    """
    lines = content.splitlines()
    result: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        # Track existing fences to avoid double-wrapping
        if line.strip().startswith("```"):
            in_fence = not in_fence
            result.append(line)
            i += 1
            continue

        if in_fence:
            result.append(line)
            i += 1
            continue

        # Check for consecutive box lines
        if _is_box_line(line):
            diagram_lines = [line]
            j = i + 1
            while j < len(lines) and _is_box_line(lines[j]):
                diagram_lines.append(lines[j])
                j += 1

            if len(diagram_lines) >= min_lines:
                # Wrap as code fence
                result.append("```text")
                result.extend(diagram_lines)
                result.append("```")
                i = j
                continue

        result.append(line)
        i += 1

    return "\n".join(result)


class AsciiDiagramPreserver(BaseMiddleware):
    """
    Middleware: Detect ASCII art diagrams and wrap them in code fences.

    Extracted from AsciiPreserver logic in existing ingest scripts.
    Ensures diagram lines are treated as atomic blocks by downstream chunkers.

    Idempotent: already-fenced diagrams are not double-wrapped.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._preserve_box_drawings = bool(self._config.get("preserve_box_drawings", True))
        self._min_lines = int(self._config.get("min_diagram_lines", 3))

    @property
    def name(self) -> str:
        return _MW_NAME

    @property
    def version(self) -> str:
        return _MW_VERSION

    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """Wrap ASCII diagrams in code fences and return a new ParsedDocument."""
        if not self._preserve_box_drawings:
            # Config says don't preserve — pass through
            return doc.with_middleware_applied(
                middleware_name=self.name,
                middleware_version=self.version,
                new_content=doc.markdown_content,
                config=self._config,
            )

        preserved = _preserve_ascii_diagrams(
            doc.markdown_content, min_lines=self._min_lines
        )
        return doc.with_middleware_applied(
            middleware_name=self.name,
            middleware_version=self.version,
            new_content=preserved,
            config=self._config,
        )
