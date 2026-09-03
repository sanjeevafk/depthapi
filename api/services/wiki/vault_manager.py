"""
Karpathy LLM-Wiki Vault Manager.
Synchronizes knowledge concepts and graph edges into an on-disk Obsidian/LLM markdown vault,
maintains index.md and append-only log.md, and integrates with the Rust vault linter.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = hasattr(depth_engine, "lint_wiki_vault")
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
_SLUG_CLEAN_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def slugify_concept_name(name: str) -> str:
    """Normalize a concept name to a safe filename slug."""
    clean = _SLUG_CLEAN_RE.sub("_", name.strip().lower()).strip("_")
    safe = os.path.basename(clean) if clean else "concept"
    return safe


class WikiVaultManager:
    """Manages the on-disk markdown vault under docs/wiki/."""

    def __init__(self, vault_dir: Path | str | None = None) -> None:
        if vault_dir is None:
            vault_dir = Path(os.getenv("DEPTHAPI_WIKI_DIR", "docs/wiki"))
        self.vault_dir = Path(vault_dir).resolve()
        self.concepts_dir = self.vault_dir / "concepts"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.concepts_dir.mkdir(parents=True, exist_ok=True)

    def export_concepts_to_vault(
        self,
        concepts: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Materializes concepts and edges to:
        - docs/wiki/concepts/<slug>.md
        - docs/wiki/index.md
        - docs/wiki/log.md
        """
        self._ensure_dirs()
        edges = edges or []

        # Map concept ID and name to concept record
        id_to_concept: dict[str, dict[str, Any]] = {}
        name_to_concept: dict[str, dict[str, Any]] = {}
        for c in concepts:
            c_id = str(c.get("id", ""))
            c_name = str(c.get("name", "")).strip()
            if c_id:
                id_to_concept[c_id] = c
            if c_name:
                name_to_concept[c_name.lower()] = c

        # Group edges by source concept
        out_edges: dict[str, list[tuple[str, str]]] = {}
        for edge in edges:
            src_id = str(edge.get("source_concept_id", "") or edge.get("source_concept", ""))
            tgt_id = str(edge.get("target_concept_id", "") or edge.get("target_concept", ""))
            rel_type = str(edge.get("relation_type", "relates_to"))

            # Resolve names
            src_name = id_to_concept.get(src_id, {}).get("name") or edge.get("source_concept") or src_id
            tgt_name = id_to_concept.get(tgt_id, {}).get("name") or edge.get("target_concept") or tgt_id

            if src_name and tgt_name:
                out_edges.setdefault(src_name.lower(), []).append((tgt_name, rel_type))

        now_iso = datetime.now(timezone.utc).isoformat()
        exported_files: list[str] = []
        base_dir = os.path.abspath(str(self.concepts_dir))

        # Write each concept note
        for c in concepts:
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            slug = slugify_concept_name(name)
            c_type = str(c.get("concept_type", "topic"))
            desc = str(c.get("description", "") or "No description recorded.")
            metadata = c.get("metadata", {}) or {}

            related = out_edges.get(name.lower(), [])
            related_md = "\n".join(
                f"- [[{tgt}]] ({rel})" for tgt, rel in sorted(set(related))
            ) if related else "None recorded."

            meta_lines = [
                "---",
                f'title: "{name}"',
                f'concept_type: "{c_type}"',
                f'slug: "{slug}"',
                f'updated_at: "{now_iso}"',
            ]
            if metadata:
                meta_lines.append(f'metadata: {metadata}')
            meta_lines.append("---\n")

            content = (
                "\n".join(meta_lines)
                + f"\n# {name}\n\n"
                + f"**Type:** `{c_type}`\n\n"
                + f"## Description\n{desc}\n\n"
                + f"## Related Concepts\n{related_md}\n"
            )

            safe_filename = os.path.basename(f"{slug}.md")
            full_path = os.path.abspath(os.path.join(base_dir, safe_filename))
            if os.path.commonpath([base_dir, full_path]) != base_dir:
                raise ValueError("Invalid concept note path")

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            exported_files.append(str(Path(full_path).relative_to(self.vault_dir.resolve())))

        # Update index.md
        index_lines = [
            "# Knowledge Vault Index",
            "",
            f"**Total Concepts:** {len(concepts)}",
            f"**Last Materialized:** {now_iso}",
            "",
            "## Concepts",
            "",
        ]
        for c in sorted(concepts, key=lambda x: str(x.get("name", "")).lower()):
            c_name = str(c.get("name", "")).strip()
            c_type = str(c.get("concept_type", "topic"))
            c_desc = str(c.get("description", "") or "").strip()
            summary_snippet = (c_desc[:80] + "...") if len(c_desc) > 80 else c_desc
            desc_part = f" — {summary_snippet}" if summary_snippet else ""
            index_lines.append(f"- [[{c_name}]] (`{c_type}`){desc_part}")

        index_file = self.vault_dir / "index.md"
        index_file.write_text("\n".join(index_lines) + "\n", encoding="utf-8")

        # Append to log.md
        log_file = self.vault_dir / "log.md"
        log_entry = (
            f"\n## [{now_iso}] Vault Materialization\n"
            f"- Exported {len(concepts)} concept notes to `concepts/`\n"
            f"- Linked {len(edges)} directed graph edges\n"
            f"- Updated master index `index.md`\n"
        )
        if not log_file.exists():
            log_file.write_text(f"# Vault Activity Log\n{log_entry}", encoding="utf-8")
        else:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

        return {
            "status": "ok",
            "exported_count": len(exported_files),
            "files": exported_files,
            "vault_dir": str(self.vault_dir),
        }

    def save_qa_insight(
        self,
        query: str,
        answer: str,
        collection_id: str | None = None,
        referenced_concepts: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Compounding Q&A loop: writes synthesized insight back to vault.
        Creates/updates synthesis note and records in log.md.
        """
        self._ensure_dirs()
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_slug = slugify_concept_name(query)[:40]
        slug = f"synthesis_{clean_slug}"

        ref_concepts = referenced_concepts or []
        concept_links = (
            "\n".join(f"- [[{c}]]" for c in ref_concepts)
            if ref_concepts
            else "- Direct synthesis query"
        )

        content = (
            "---\n"
            f'title: "Synthesis: {query}"\n'
            'concept_type: "synthesis"\n'
            f'updated_at: "{now_iso}"\n'
            "---\n\n"
            f"# Synthesis: {query}\n\n"
            f"## Question\n{query}\n\n"
            f"## Answer\n{answer}\n\n"
            f"## Context & Concepts\n{concept_links}\n"
        )

        safe_filename = os.path.basename(f"{slug}.md")
        base_dir = os.path.abspath(str(self.concepts_dir))
        full_path = os.path.abspath(os.path.join(base_dir, safe_filename))

        if os.path.commonpath([base_dir, full_path]) != base_dir:
            raise ValueError("Invalid synthesized note path")

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Ensure index links to it so it is not an orphan
        index_file = self.vault_dir / "index.md"
        if index_file.exists():
            curr_index = index_file.read_text(encoding="utf-8")
            link_entry = f"- [[{slug}]] (`synthesis`) — Q&A Synthesis for: {query[:60]}"
            if f"[[{slug}]]" not in curr_index:
                index_file.write_text(curr_index.rstrip() + f"\n{link_entry}\n", encoding="utf-8")

        # Append to log.md
        log_file = self.vault_dir / "log.md"
        snippet = (answer[:120] + "...") if len(answer) > 120 else answer
        log_entry = (
            f"\n### [{now_iso}] Q&A Insight Saved\n"
            f"- **Query:** {query}\n"
            f"- **Artifact:** `concepts/{slug}.md`\n"
            f"- **Summary:** {snippet}\n"
        )
        if not log_file.exists():
            log_file.write_text(f"# Vault Activity Log\n{log_entry}", encoding="utf-8")
        else:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

        return {
            "status": "saved",
            "slug": slug,
            "path": str(Path(full_path).relative_to(self.vault_dir.resolve())),
        }

    def lint_vault(self) -> dict[str, Any]:
        """
        Runs the high-speed Rust vault linter if available,
        falling back to Python graph analysis if compiled engine is missing.
        """
        if _HAS_DEPTH_ENGINE and depth_engine is not None:
            try:
                return depth_engine.lint_wiki_vault(str(self.vault_dir))
            except Exception:
                pass

        # Python fallback implementation
        return self._python_lint_fallback()

    def _python_lint_fallback(self) -> dict[str, Any]:
        """Pure-Python linter verifying broken links, orphans, and cycles."""
        all_md_files = list(self.vault_dir.rglob("*.md"))
        notes: dict[str, dict[str, Any]] = {}
        slug_map: dict[str, str] = {}

        for p in all_md_files:
            rel = str(p.relative_to(self.vault_dir)).replace("\\", "/")
            stem = p.stem
            content = p.read_text(encoding="utf-8")
            title = None
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break

            notes[rel] = {
                "rel": rel,
                "stem": stem,
                "title": title,
                "lines": content.splitlines(),
            }
            clean_stem = re.sub(r"[^a-zA-Z0-9]+", "", stem.lower())
            slug_map[clean_stem] = rel
            slug_map[rel.lower()] = rel
            if rel.endswith(".md"):
                slug_map[rel[:-3].lower()] = rel
            if title:
                clean_title = re.sub(r"[^a-zA-Z0-9]+", "", title.lower())
                slug_map[clean_title] = rel

        broken_links = []
        in_degrees = {rel: 0 for rel in notes}
        adj: dict[str, set[str]] = {rel: set() for rel in notes}

        for rel, note_info in notes.items():
            for line_idx, line in enumerate(note_info["lines"], 1):
                for match in _WIKILINK_RE.finditer(line):
                    target = match.group(1).strip()
                    if not target:
                        continue
                    clean_target = re.sub(r"[^a-zA-Z0-9]+", "", target.lower())
                    resolved = (
                        slug_map.get(clean_target)
                        or slug_map.get(target.lower())
                        or slug_map.get(f"concepts/{target.lower()}")
                    )
                    if resolved:
                        if resolved not in adj[rel]:
                            adj[rel].add(resolved)
                            if resolved != rel:
                                in_degrees[resolved] += 1
                    else:
                        broken_links.append({
                            "source_file": rel,
                            "target": target,
                            "line": line_idx,
                        })

        orphan_nodes = [
            rel for rel, deg in in_degrees.items()
            if deg == 0 and not (rel.endswith("index.md") or rel.endswith("log.md") or rel == "index.md" or rel == "log.md")
        ]
        orphan_nodes.sort()

        # Cycle detection
        cycles: list[list[str]] = []
        visited: set[str] = set()
        on_stack: set[str] = set()
        stack: list[str] = []

        def dfs(u: str):
            visited.add(u)
            on_stack.add(u)
            stack.append(u)

            for v in sorted(adj[u]):
                if v in on_stack:
                    idx = stack.index(v)
                    cycle_nodes = [notes[x]["stem"] for x in stack[idx:]]
                    cycle_nodes.append(notes[v]["stem"])
                    if cycle_nodes not in cycles:
                        cycles.append(cycle_nodes)
                elif v not in visited:
                    dfs(v)

            stack.pop()
            on_stack.remove(u)

        for node in sorted(notes.keys()):
            if node not in visited:
                dfs(node)

        valid = len(broken_links) == 0 and len(cycles) == 0
        return {
            "total_notes": len(notes),
            "broken_links": broken_links,
            "orphan_nodes": orphan_nodes,
            "cycles": cycles,
            "valid": valid,
        }

    def list_concepts(self) -> list[dict[str, Any]]:
        """Lists all concept notes found in concepts/."""
        if not self.concepts_dir.exists():
            return []

        base_dir = self.concepts_dir.resolve()
        results = []
        for entry in base_dir.iterdir():
            if entry.is_file() and entry.suffix == ".md":
                resolved = entry.resolve()
                if not resolved.is_relative_to(base_dir):
                    continue

                content = resolved.read_text(encoding="utf-8")
                title = resolved.stem
                c_type = "topic"
                for line in content.splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"\'')
                    elif line.startswith("concept_type:"):
                        c_type = line.split(":", 1)[1].strip().strip('"\'')

                wikilinks = [m.group(1).strip() for m in _WIKILINK_RE.finditer(content)]
                results.append({
                    "name": title,
                    "slug": resolved.stem,
                    "concept_type": c_type,
                    "file_path": str(resolved.relative_to(self.vault_dir.resolve())),
                    "links": list(set(wikilinks)),
                })
        return results

    def read_concept(self, name_or_slug: str) -> dict[str, Any] | None:
        """Reads concept details and raw markdown content."""
        if not self.concepts_dir.exists():
            return None

        # Clean search keys (used purely for string comparison, never for path construction)
        target_slug = os.path.basename(slugify_concept_name(name_or_slug))
        raw_key = os.path.basename(name_or_slug.strip().lower())

        base_dir = self.concepts_dir.resolve()
        matched_file: Path | None = None

        for entry in base_dir.iterdir():
            if entry.is_file() and entry.suffix == ".md":
                stem_lower = entry.stem.lower()
                if stem_lower == target_slug or stem_lower == raw_key:
                    resolved = entry.resolve()
                    if resolved.is_relative_to(base_dir):
                        matched_file = resolved
                        break

        if matched_file is None:
            return None

        content = matched_file.read_text(encoding="utf-8")
        return {
            "name": name_or_slug,
            "slug": matched_file.stem,
            "file_path": str(matched_file.relative_to(self.vault_dir.resolve())),
            "content": content,
        }


_vault_manager_instance: WikiVaultManager | None = None


def get_vault_manager(vault_dir: Path | str | None = None) -> WikiVaultManager:
    """Returns or initializes the singleton WikiVaultManager."""
    global _vault_manager_instance
    if _vault_manager_instance is None or vault_dir is not None:
        _vault_manager_instance = WikiVaultManager(vault_dir)
    return _vault_manager_instance
