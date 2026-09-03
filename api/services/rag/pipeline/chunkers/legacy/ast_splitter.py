"""
ast_splitter.py — AST-aware code block boundary detection.

Uses tree-sitter to split large code blocks at top-level definition
boundaries (class, function/method). Falls back to line-based splitting
when tree-sitter is unavailable or the language is unsupported.

Supported: Python, JavaScript, TypeScript (via tree-sitter language packages).
"""

from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger("ingest")

# ─── tree-sitter import (optional dependency) ─────────────────────────────────
_TS_AVAILABLE = False
try:
    import tree_sitter_python      as _tsp
    import tree_sitter_javascript  as _tsj
    import tree_sitter_typescript  as _tst
    from tree_sitter import Language, Parser
    _TS_AVAILABLE = True
except ImportError:
    pass  # Graceful degradation to line splitter

# Map fenced language tags → tree-sitter language loaders
_LANG_LOADERS: dict[str, Callable] = {}
if _TS_AVAILABLE:
    _LANG_LOADERS = {
        "python":     lambda: Language(_tsp.language()),
        "py":         lambda: Language(_tsp.language()),
        "javascript": lambda: Language(_tsj.language()),
        "js":         lambda: Language(_tsj.language()),
        "typescript": lambda: Language(_tst.language_typescript()),
        "ts":         lambda: Language(_tst.language_typescript()),
        "tsx":        lambda: Language(_tst.language_tsx()),
    }

# Node types that represent top-level definable units worth splitting at
_TOP_LEVEL_TYPES: set[str] = {
    # Python
    "function_definition", "class_definition", "decorated_definition",
    "async_function_definition",
    # JavaScript / TypeScript
    "function_declaration", "class_declaration",
    "export_statement", "lexical_declaration",
    "variable_declaration",
}

_MIN_SPLIT_LINES = 30   # Never split code blocks shorter than this


# ─── Public API ───────────────────────────────────────────────────────────────
def split_code_block(
    code: str,
    language: str,
    max_lines: int = 80,
) -> list[str]:
    """
    Split a (possibly very large) code block into syntactically coherent chunks.

    Args:
        code:      Full source code text (WITHOUT fence markers).
        language:  Fenced language hint (e.g. "python", "javascript").
        max_lines: Target maximum lines per chunk.

    Returns:
        List of code strings. Each is a self-contained slice; never breaks
        inside a function/class definition when tree-sitter is available.
    """
    lines = code.splitlines()
    if len(lines) <= _MIN_SPLIT_LINES:
        return [code]

    if _TS_AVAILABLE and language.lower() in _LANG_LOADERS:
        try:
            return _ast_split(code, language.lower(), max_lines)
        except Exception as e:
            log.debug(f"tree-sitter split failed for {language!r}: {e} — falling back")

    return _line_split(lines, max_lines)


# ─── tree-sitter based splitting ──────────────────────────────────────────────
def _ast_split(code: str, language: str, max_lines: int) -> list[str]:
    """Split at top-level AST node boundaries."""
    lang    = _LANG_LOADERS[language]()
    parser  = Parser(lang)
    tree    = parser.parse(code.encode("utf-8"))
    root    = tree.root_node

    # Collect (start_line, end_line) of top-level definitions
    boundaries: list[tuple[int, int]] = []
    for child in root.children:
        if child.type in _TOP_LEVEL_TYPES:
            boundaries.append((child.start_point[0], child.end_point[0]))

    if not boundaries:
        return _line_split(code.splitlines(), max_lines)

    all_lines = code.splitlines()
    chunks: list[str] = []
    current: list[str] = []

    def flush():
        if current:
            chunks.append("\n".join(current))
            current.clear()

    prev_end = 0
    for start, end in boundaries:
        # Lines before this definition
        interstitial = all_lines[prev_end:start]
        current.extend(interstitial)

        definition = all_lines[start : end + 1]

        if len(current) + len(definition) > max_lines and current:
            flush()

        current.extend(definition)

        if len(current) >= max_lines:
            flush()

        prev_end = end + 1

    # Remaining lines after last boundary
    current.extend(all_lines[prev_end:])
    flush()

    return chunks or [code]


# ─── Fallback: line-based splitting ───────────────────────────────────────────
def _line_split(lines: list[str], max_lines: int) -> list[str]:
    """
    Dumb line splitter: tries to break at blank lines, otherwise hard-cuts.
    Used when tree-sitter is unavailable or the language is unsupported.
    """
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if len(current) >= max_lines:
            # Try to find a blank line to split at
            split_idx = None
            for j in range(len(current) - 1, max(0, len(current) - 10), -1):
                if not current[j].strip():
                    split_idx = j
                    break
            if split_idx is not None:
                chunks.append("\n".join(current[:split_idx]))
                current = current[split_idx:]
            else:
                chunks.append("\n".join(current))
                current = []

    if current:
        chunks.append("\n".join(current))

    return [c for c in chunks if c.strip()]


# ─── Fence-aware wrapper ───────────────────────────────────────────────────────
def split_fenced_block(fenced: str, max_lines: int = 80) -> list[str]:
    """
    Split a fenced code block (including ``` markers) into multiple fenced
    chunks, preserving the language tag. Adds a [continued] comment marker
    to continuation chunks so downstream retrieval knows they are related.

    Args:
        fenced:    Full string including opening/closing ``` fence markers.
        max_lines: Target max lines of *code* per chunk (not counting fences).

    Returns:
        List of fenced strings. Only one element if no split needed.
    """
    fence_lines = fenced.splitlines()
    if not fence_lines:
        return [fenced]

    # Parse language from opening fence
    m = re.match(r"^```(\w*)", fence_lines[0])
    lang_tag = m.group(1) if m else ""

    # Extract body (strip opening/closing fences)
    body_lines = fence_lines[1:]
    if body_lines and re.match(r"^```\s*$", body_lines[-1]):
        body_lines = body_lines[:-1]
    body = "\n".join(body_lines)

    parts = split_code_block(body, lang_tag, max_lines=max_lines)
    if len(parts) <= 1:
        return [fenced]

    result: list[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            result.append(f"```{lang_tag}\n{part}\n```")
        else:
            # Inject [continued] marker as a language-appropriate comment
            continued_marker = _continued_comment(lang_tag, idx)
            result.append(f"```{lang_tag}\n{continued_marker}\n{part}\n```")

    return result


def _continued_comment(lang: str, part: int) -> str:
    """Return a language-appropriate [continued] comment string."""
    if lang in ("python", "py", "bash", "sh", "shell", "ruby", "r"):
        return f"# [continued: part {part}]"
    if lang in ("javascript", "js", "typescript", "ts", "tsx", "java", "c", "cpp", "go"):
        return f"// [continued: part {part}]"
    if lang in ("sql",):
        return f"-- [continued: part {part}]"
    if lang in ("html", "xml"):
        return f"<!-- [continued: part {part}] -->"
    return f"# [continued: part {part}]"
