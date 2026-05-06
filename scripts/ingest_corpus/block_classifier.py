"""
block_classifier.py — Structural block classification for technical markdown.

Classifies a markdown document into a sequence of typed MarkdownBlock objects.
Used by HierarchicalSemanticChunker to apply block-specific splitting rules.

Block types
-----------
prose        Regular paragraphs / sentence text
code         Fenced triple-backtick blocks
table        GFM pipe tables
exercise     Sections starting with Exercise / Problem / Quiz / Challenge
figure       Lines containing image placeholders or figure captions
shell_output Lines starting with shell prompts ($, >>>, C:\\>)
heading      Markdown heading (# … ######)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ─── Data type ────────────────────────────────────────────────────────────────
@dataclass
class MarkdownBlock:
    type: str                        # one of the block types listed above
    content: str                     # raw text of this block
    metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience
    @property
    def is_atomic(self) -> bool:
        """Blocks that must never be split across chunks."""
        return self.type in ("code", "table", "exercise")


# ─── Patterns ─────────────────────────────────────────────────────────────────
_FENCE_OPEN  = re.compile(r"^```(\w*)\s*$")          # opening fence (optional lang)
_FENCE_CLOSE = re.compile(r"^```\s*$")                # closing fence

_TABLE_ROW   = re.compile(r"^\|.*\|$")                # GFM table row
_TABLE_SEP   = re.compile(r"^\|[\s\-:|]+\|$")         # separator row

_EXERCISE    = re.compile(
    r"^(Exercise|Problem|Quiz|Challenge|Practice)\b",
    re.IGNORECASE,
)

_SHELL_PROMPT = re.compile(r"^(\$|>>>\s|C:\\>|#\s|%\s)")   # bash / Python REPL / cmd

_IMAGE        = re.compile(
    r"!\[.*?\]\(.*?\)"              # ![alt](url)
    r"|^\[Figure[^\]]*\]"          # [Figure n]
    r"|^\[Image[^\]]*\]"           # [Image n]
    r"|^Figure\s+\d",              # Figure 3.1 ...
    re.IGNORECASE,
)

_HEADING      = re.compile(r"^(#{1,6})\s+(.+)$")


# ─── Classifier ───────────────────────────────────────────────────────────────
class StructuralBlockClassifier:
    """
    Single-pass line scanner that emits typed MarkdownBlock objects.

    Usage::

        classifier = StructuralBlockClassifier()
        blocks = classifier.classify(markdown_text)
    """

    def classify(self, text: str) -> list[MarkdownBlock]:
        lines  = text.splitlines()
        blocks: list[MarkdownBlock] = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # ── Fenced code block ──────────────────────────────────────────
            m = _FENCE_OPEN.match(line)
            if m:
                lang        = m.group(1) or ""
                fence_lines = [line]
                i += 1
                while i < len(lines):
                    fence_lines.append(lines[i])
                    if _FENCE_CLOSE.match(lines[i]):
                        i += 1
                        break
                    i += 1
                blocks.append(MarkdownBlock(
                    type="code",
                    content="\n".join(fence_lines),
                    metadata={"language": lang},
                ))
                continue

            # ── Heading ───────────────────────────────────────────────────
            m = _HEADING.match(line)
            if m:
                blocks.append(MarkdownBlock(
                    type="heading",
                    content=line,
                    metadata={"level": len(m.group(1)), "title": m.group(2).strip()},
                ))
                i += 1
                continue

            # ── GFM Table ─────────────────────────────────────────────────
            if _TABLE_ROW.match(line):
                table_lines = [line]
                i += 1
                while i < len(lines) and _TABLE_ROW.match(lines[i]):
                    table_lines.append(lines[i])
                    i += 1
                blocks.append(MarkdownBlock(
                    type="table",
                    content="\n".join(table_lines),
                    metadata={"rows": len(table_lines)},
                ))
                continue

            # ── Empty line — paragraph separator ─────────────────────────
            if not line.strip():
                i += 1
                continue

            # ── Collect a non-code paragraph ─────────────────────────────
            para_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                # Stop before a fence or heading
                if _FENCE_OPEN.match(lines[i]) or _HEADING.match(lines[i]):
                    break
                para_lines.append(lines[i])
                i += 1

            if not para_lines:
                continue

            content = "\n".join(para_lines)
            blocks.append(MarkdownBlock(
                type=self._classify_para(para_lines),
                content=content,
                metadata={},
            ))

        return blocks

    # ── Paragraph-level heuristics ────────────────────────────────────────────
    def _classify_para(self, lines: list[str]) -> str:
        first = lines[0]

        if _EXERCISE.match(first):
            return "exercise"

        if _IMAGE.search("\n".join(lines)):
            return "figure"

        # Shell output: majority of non-empty lines start with a prompt
        non_empty = [l for l in lines if l.strip()]
        if non_empty:
            shell_hits = sum(1 for l in non_empty if _SHELL_PROMPT.match(l))
            if shell_hits / len(non_empty) >= 0.5:
                return "shell_output"

        return "prose"
