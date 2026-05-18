"""
ingest_system_design_primer.py — Specialized RAG ingestion for system-design-primer.
Implements:
1. ASCII Diagram preservation and fence wrapping
2. Anchor link stripping in prose
3. Table of Contents (TOC) exclusion for main README
4. Image alt-text mapping and path verification
5. Multi-format support (English markdown READMEs & python/sql/js solutions)
6. True token counting with BAAI/bge-base-en-v1.5 tokenizer (safe chunk limit 480)
7. Dual-URL mapping (GitHub source URL + local workspace path URL)
8. Exact & SimHash near-duplicate filtering
"""

from __future__ import annotations

import argparse
import sys
import re
import hashlib
from pathlib import Path
from collections import Counter
from typing import Any
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    Chunk,
    REPO_ROOT,
    log,
    clean_text,
    content_hash,
    make_doc_id,
    make_min_word_validator,
    make_link_ratio_validator,
    make_markdown_toc_validator,
)
from scripts.ingest_corpus.block_classifier import StructuralBlockClassifier
from scripts.ingest_corpus.ast_splitter import split_fenced_block

# Initialize tokenizer
_TOKENIZER = AutoTokenizer.from_pretrained("BAAI/bge-base-en-v1.5")

def get_token_count(text: str) -> int:
    """Compute exact token count using BAAI/bge-base-en-v1.5 tokenizer."""
    return len(_TOKENIZER.encode(text, truncation=False))

def normalize_for_hash(text: str) -> str:
    """Normalize text for dedup hashing across duplicated summaries."""
    return " ".join(text.split()).lower()

def is_line_ascii_diagram(line: str) -> bool:
    """Detect if a line resembles part of an ASCII architecture diagram."""
    if len(line.strip()) < 3:
        return False
    has_box_corners = '+' in line and '-' in line
    has_arrows = '->' in line or '<-' in line or '-->' in line or '<--' in line
    has_vertical_bars = '|' in line and (line.count('|') >= 2 or '  ' in line)
    has_dashes = '---' in line and len(line.strip()) > 5
    return bool(has_box_corners or has_arrows or has_vertical_bars or has_dashes)

def preprocess_ascii_diagrams(text: str) -> str:
    """Preprocess text to wrap ASCII diagrams in dedicated ```diagram blocks."""
    # Find all existing code blocks to avoid modification
    code_block_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    code_spans = [(m.start(), m.end()) for m in code_block_pattern.finditer(text)]
    
    def is_inside_code(pos: int) -> bool:
        for start, end in code_spans:
            if start <= pos < end:
                return True
        return False

    lines = text.splitlines()
    new_lines = []
    current_diagram = []
    current_pos = 0
    
    for line in lines:
        line_len = len(line) + 1  # Include newline
        if is_inside_code(current_pos):
            if current_diagram:
                if len(current_diagram) >= 3:
                    new_lines.append("```diagram")
                    new_lines.extend(current_diagram)
                    new_lines.append("```")
                else:
                    new_lines.extend(current_diagram)
                current_diagram = []
            new_lines.append(line)
        else:
            is_table = line.strip().startswith('|') and line.strip().endswith('|')
            if not is_table and is_line_ascii_diagram(line):
                current_diagram.append(line)
            else:
                if current_diagram:
                    if len(current_diagram) >= 3:
                        new_lines.append("```diagram")
                        new_lines.extend(current_diagram)
                        new_lines.append("```")
                    else:
                        new_lines.extend(current_diagram)
                    current_diagram = []
                new_lines.append(line)
        current_pos += line_len
        
    if current_diagram:
        if len(current_diagram) >= 3:
            new_lines.append("```diagram")
            new_lines.extend(current_diagram)
            new_lines.append("```")
        else:
            new_lines.extend(current_diagram)
            
    return "\n".join(new_lines)

def extract_references(text: str) -> list[str]:
    """Extract external http/https references for metadata."""
    urls = re.findall(r"https?://[^\s\)<>]+", text)
    seen = set()
    deduped: list[str] = []
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped

def _replace_images_and_collect(text: str, current_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Replace markdown images with placeholders and collect metadata."""
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    images: list[dict[str, Any]] = []

    def _repl(match: re.Match) -> str:
        alt_text = match.group(1).strip()
        img_path_str = match.group(2).strip()
        is_local = not img_path_str.startswith(("http://", "https://", "ftp://", "file://"))
        exists = False
        if is_local:
            full_path = (current_dir / img_path_str).resolve()
            exists = full_path.exists()
        images.append({
            "alt": alt_text,
            "path": img_path_str,
            "exists": exists,
        })
        label = alt_text or "image"
        return f"[Image: {label}]"

    return image_pattern.sub(_repl, text), images

def _strip_markdown_links(text: str) -> str:
    """Strip markdown link targets while keeping the anchor text."""
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)

def _remove_inline_urls(text: str) -> str:
    """Remove inline URLs from prose to reduce token bloat."""
    return re.sub(r"https?://[^\s\)<>]+", "", text)

def process_block_text(text: str, current_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Process a markdown block outside code fences: images, links, URLs."""
    image_mappings: list[dict[str, Any]] = []
    output_lines: list[str] = []
    in_fence = False

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            output_lines.append(line)
            continue
        if in_fence:
            output_lines.append(line)
            continue

        processed, images = _replace_images_and_collect(line, current_dir)
        if images:
            image_mappings.extend(images)
        processed = _strip_markdown_links(processed)
        processed = _remove_inline_urls(processed)
        output_lines.append(processed)

    return "\n".join(output_lines), image_mappings

def auto_detect_language(code_content: str, current_lang: str) -> str:
    """Identify programming language of untagged code blocks."""
    if current_lang and current_lang.lower() not in ("plain", "text", ""):
        return current_lang.lower()
    code_stripped = code_content.strip()
    
    if any(k in code_stripped for k in ("def ", "import ", "print(", "class ", "self.", "elif ")):
        return "python"
    if any(k in code_stripped.upper() for k in ("SELECT ", "INSERT INTO ", "CREATE TABLE ", "UPDATE ", "DELETE FROM ")):
        return "sql"
    if any(k in code_stripped for k in ("const ", "let ", "console.log", "function ", "import {", "export const ")):
        return "javascript"
    return "plain"

def split_block_into_chunks(block_content: str, allowed_tokens: int) -> list[tuple[str, bool]]:
    """Fallback splitting using paragraph and sentence boundaries under token budget."""
    paragraphs = [p.strip() for p in block_content.split("\n\n") if p.strip()]
    sub_chunks: list[tuple[str, bool]] = []
    current_chunk = []
    current_tokens = 0
    current_warn = False
    
    for para in paragraphs:
        para_tokens = get_token_count(para)
        if para_tokens > allowed_tokens:
            if current_chunk:
                sub_chunks.append(("\n\n".join(current_chunk), current_warn))
                current_chunk = []
                current_tokens = 0
            current_warn = False
            
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            for sent in sentences:
                sent_tokens = get_token_count(sent)
                if sent_tokens > allowed_tokens:
                    sub_chunks.append((sent, True))
                    current_chunk = []
                    current_tokens = 0
                    current_warn = False
                    continue
                if current_tokens + sent_tokens + 1 > allowed_tokens:
                    if current_chunk:
                        sub_chunks.append((" ".join(current_chunk), current_warn or True))
                    current_chunk = [sent]
                    current_tokens = sent_tokens
                    current_warn = True
                else:
                    current_chunk.append(sent)
                    current_tokens += sent_tokens + 1
                    current_warn = True
            if current_chunk:
                sub_chunks.append((" ".join(current_chunk), current_warn or True))
                current_chunk = []
                current_tokens = 0
                current_warn = False
        else:
            if current_tokens + para_tokens + 2 > allowed_tokens:
                if current_chunk:
                    sub_chunks.append(("\n\n".join(current_chunk), current_warn))
                current_chunk = [para]
                current_tokens = para_tokens
                current_warn = False
            else:
                current_chunk.append(para)
                current_tokens += para_tokens + 2
                current_warn = False
                
    if current_chunk:
        sub_chunks.append(("\n\n".join(current_chunk), current_warn))
        
    return sub_chunks

def get_sections(markdown_text: str) -> list[dict[str, Any]]:
    """Partition Markdown content by heading hierarchies."""
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    matches = list(header_pattern.finditer(markdown_text))
    sections = []
    if not matches:
        return [{"level": 0, "title": "", "content": markdown_text}]
        
    pre_content = markdown_text[:matches[0].start()].strip()
    if pre_content:
        sections.append({"level": 0, "title": "", "content": pre_content})
        
    for idx, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        sec_content = markdown_text[start:end].strip()
        sections.append({
            "level": level,
            "title": title,
            "content": sec_content
        })
    return sections

def chunk_markdown(
    text: str,
    doc_id: str,
    source_name: str,
    source_url: str | None,
    local_path_url: str,
    tags: list[str],
    current_dir: Path,
    file_path: Path,
    strip_toc: bool,
) -> list[Chunk]:
    """Parse raw MD text into structured 480-token chunks."""
    # 1. Strip top-level TOC sections
    sections = get_sections(text)
    toc_ignore_titles = {"contents", "index of system design topics", "study guide"}
    if strip_toc:
        filtered_sections = [
            s for s in sections
            if s["title"].strip().lower() not in toc_ignore_titles
        ]
    else:
        filtered_sections = sections
    
    rebuilt_text = "\n\n".join(s["content"] for s in filtered_sections)
    
    # 2. Preprocess ASCII diagrams
    processed_text = preprocess_ascii_diagrams(rebuilt_text)
    
    # 3. Classify blocks structurally
    classifier = StructuralBlockClassifier()
    blocks = classifier.classify(processed_text)
    
    chunks: list[Chunk] = []
    hierarchy: list[str] = ["System Design Primer"]
    chunk_order = 0
    
    for block in blocks:
        if block.type == "heading":
            level = block.metadata.get("level", 2)
            title = block.metadata.get("title", "")
            hierarchy = hierarchy[: level - 1]
            hierarchy.append(title)
            continue
            
        # Standard lightweight breadcrumb prefix
        breadcrumb_parts = [p for p in hierarchy[-2:] if p]
        breadcrumb_prefix = "[" + " > ".join(breadcrumb_parts) + "]\n\n" if breadcrumb_parts else ""
        prefix_tokens = get_token_count(breadcrumb_prefix)
        allowed_tokens = 480 - prefix_tokens
        
        block_text = block.content
        references = extract_references(block_text)
        has_ascii_diagram = False
        image_mappings: list[dict[str, Any]] = []
        
        if block.type == "code":
            orig_lang = block.metadata.get("language", "")
            detected_lang = auto_detect_language(block_text, orig_lang)
            if detected_lang == "diagram" or "```diagram" in block_text:
                has_ascii_diagram = True
            block.metadata["language"] = detected_lang
            
            block_tokens = get_token_count(block_text)
            if block_tokens > allowed_tokens:
                # Split large code block via AST-aware splitter
                split_parts = split_fenced_block(block_text, max_lines=40)
                for part in split_parts:
                    cleaned_part = clean_text(part)
                    full_text = breadcrumb_prefix + cleaned_part
                    chash = content_hash(full_text)
                    chunks.append(Chunk(
                        id=chash[:16],
                        doc_id=doc_id,
                        chunk_id=f"{doc_id}#c{chunk_order:04d}",
                        content_hash=chash,
                        version="v2",
                        content=full_text,
                        raw_text=part,
                        cleaned_text=full_text,
                        source_name=source_name,
                        source_url=source_url,
                        chunk_order=chunk_order,
                        token_count=get_token_count(full_text),
                        source_type="markdown",
                        tags=tags + ["code", detected_lang],
                        metadata={
                            "doc_id": doc_id,
                            "hierarchy": list(hierarchy),
                            "block_types": ["code"],
                            "version": "v2",
                            "local_path_url": local_path_url,
                            "image_mappings": image_mappings,
                            "images": image_mappings,
                            "code_language": detected_lang,
                            "has_ascii_diagram": has_ascii_diagram,
                            "references": references,
                        }
                    ))
                    chunk_order += 1
            else:
                cleaned_block = clean_text(block_text)
                full_text = breadcrumb_prefix + cleaned_block
                chash = content_hash(full_text)
                chunks.append(Chunk(
                    id=chash[:16],
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}#c{chunk_order:04d}",
                    content_hash=chash,
                    version="v2",
                    content=full_text,
                    raw_text=block_text,
                    cleaned_text=full_text,
                    source_name=source_name,
                    source_url=source_url,
                    chunk_order=chunk_order,
                    token_count=get_token_count(full_text),
                    source_type="markdown",
                    tags=tags + ["code", detected_lang],
                    metadata={
                        "doc_id": doc_id,
                        "hierarchy": list(hierarchy),
                        "block_types": ["code"],
                        "version": "v2",
                        "local_path_url": local_path_url,
                        "image_mappings": image_mappings,
                        "images": image_mappings,
                        "code_language": detected_lang,
                        "has_ascii_diagram": has_ascii_diagram,
                        "references": references,
                    }
                ))
                chunk_order += 1
                
        elif block.type == "table":
            processed_text, image_mappings = process_block_text(block_text, current_dir)
            cleaned_block = clean_text(processed_text)
            full_text = breadcrumb_prefix + cleaned_block
            table_tokens = get_token_count(full_text)
            if table_tokens > 480:
                log.warning(
                    "Oversized table chunk kept intact file=%s tokens=%s",
                    file_path.as_posix(),
                    table_tokens,
                )
            chash = content_hash(full_text)
            chunks.append(Chunk(
                id=chash[:16],
                doc_id=doc_id,
                chunk_id=f"{doc_id}#c{chunk_order:04d}",
                content_hash=chash,
                version="v2",
                content=full_text,
                raw_text=block_text,
                cleaned_text=full_text,
                source_name=source_name,
                source_url=source_url,
                chunk_order=chunk_order,
                token_count=get_token_count(full_text),
                source_type="markdown",
                tags=tags + ["table"],
                metadata={
                    "doc_id": doc_id,
                    "hierarchy": list(hierarchy),
                    "block_types": ["table"],
                    "version": "v2",
                    "local_path_url": local_path_url,
                    "image_mappings": image_mappings,
                    "images": image_mappings,
                    "references": references,
                }
            ))
            chunk_order += 1
            
        else:
            # Prose blocks
            processed_text, image_mappings = process_block_text(block_text, current_dir)
            block_tokens = get_token_count(processed_text)
            
            if block_tokens > allowed_tokens:
                split_parts = split_block_into_chunks(processed_text, allowed_tokens)
                for part, truncation_warning in split_parts:
                    cleaned_part = clean_text(part)
                    full_text = breadcrumb_prefix + cleaned_part
                    chash = content_hash(full_text)
                    if truncation_warning:
                        header_label = " > ".join([p for p in hierarchy if p])
                        log.warning(
                            "Truncation warning for chunk file=%s header=%s",
                            file_path.as_posix(),
                            header_label,
                        )
                    chunks.append(Chunk(
                        id=chash[:16],
                        doc_id=doc_id,
                        chunk_id=f"{doc_id}#c{chunk_order:04d}",
                        content_hash=chash,
                        version="v2",
                        content=full_text,
                        raw_text=part,
                        cleaned_text=full_text,
                        source_name=source_name,
                        source_url=source_url,
                        chunk_order=chunk_order,
                        token_count=get_token_count(full_text),
                        source_type="markdown",
                        tags=tags + ["prose"],
                        metadata={
                            "doc_id": doc_id,
                            "hierarchy": list(hierarchy),
                            "block_types": [block.type],
                            "version": "v2",
                            "local_path_url": local_path_url,
                            "image_mappings": image_mappings,
                            "images": image_mappings,
                            "references": references,
                            "truncation_warning": truncation_warning,
                        }
                    ))
                    chunk_order += 1
            else:
                cleaned_block = clean_text(processed_text)
                full_text = breadcrumb_prefix + cleaned_block
                chash = content_hash(full_text)
                chunks.append(Chunk(
                    id=chash[:16],
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}#c{chunk_order:04d}",
                    content_hash=chash,
                    version="v2",
                    content=full_text,
                    raw_text=block_text,
                    cleaned_text=full_text,
                    source_name=source_name,
                    source_url=source_url,
                    chunk_order=chunk_order,
                    token_count=get_token_count(full_text),
                    source_type="markdown",
                    tags=tags + ["prose"],
                    metadata={
                        "doc_id": doc_id,
                        "hierarchy": list(hierarchy),
                        "block_types": [block.type],
                        "version": "v2",
                        "local_path_url": local_path_url,
                        "image_mappings": image_mappings,
                        "images": image_mappings,
                        "references": references,
                    }
                ))
                chunk_order += 1
                
    return chunks

def chunk_code_file(
    content: str,
    doc_id: str,
    source_name: str,
    source_url: str | None,
    local_path_url: str,
    tags: list[str],
    file_extension: str,
    hierarchy: list[str],
) -> list[Chunk]:
    """Parse pure solution files by wrapping in Markdown code fences."""
    lang_map = {
        ".py": "python",
        ".sql": "sql",
        ".js": "javascript"
    }
    lang = lang_map.get(file_extension.lower(), "plain")
    
    # Wrap in standard markdown code fence
    fenced_content = f"```{lang}\n{content}\n```"
    
    # Generate breadcrumb
    breadcrumb_parts = [p for p in hierarchy[-2:] if p]
    breadcrumb_prefix = "[" + " > ".join(breadcrumb_parts) + "]\n\n" if breadcrumb_parts else ""
    prefix_tokens = get_token_count(breadcrumb_prefix)
    allowed_tokens = 480 - prefix_tokens
    
    split_parts = split_fenced_block(fenced_content, max_lines=40)
    chunks = []
    chunk_order = 0
    
    for part in split_parts:
        cleaned_part = clean_text(part)
        full_text = breadcrumb_prefix + cleaned_part
        chash = content_hash(full_text)
        chunks.append(Chunk(
            id=chash[:16],
            doc_id=doc_id,
            chunk_id=f"{doc_id}#c{chunk_order:04d}",
            content_hash=chash,
            version="v2",
            content=full_text,
            raw_text=part,
            cleaned_text=full_text,
            source_name=source_name,
            source_url=source_url,
            chunk_order=chunk_order,
            token_count=get_token_count(full_text),
            source_type="markdown",
            tags=tags + ["code", lang, "solution-file"],
            metadata={
                "doc_id": doc_id,
                "hierarchy": hierarchy,
                "block_types": ["code"],
                "version": "v2",
                "local_path_url": local_path_url,
                "image_mappings": [],
                "code_language": lang
            }
        ))
        chunk_order += 1
        
    return chunks

def run() -> None:
    source_name = "System Design Primer"
    root_dir = REPO_ROOT / "datasets" / "system-design-primer"
    
    if not root_dir.exists():
        log.error(f"System Design Primer directory not found: {root_dir}")
        sys.exit(1)
        
    # Configure validators as required by base ingestor
    validators = [
        make_min_word_validator(10),
        make_link_ratio_validator(0.35),
        make_markdown_toc_validator(),
    ]
    
    ingestor = BaseIngestor(
        source_name=source_name,
        source_type="markdown",
        validators=validators,
        near_dup_threshold=4,
    )
    
    stats = Counter()
    dedup_index: dict[str, Chunk] = {}
    tags = ["system-design", "primer", "architecture", "interview", "P0"]
    
    # ─── File Discovery ──────────────────────────────────────────────────────
    # 1. Main README
    main_readme = root_dir / "README.md"
    files_to_process = [(main_readme, "README.md", ["primer", "overview"])]
    
    # 2. English Solution READMEs (skip other languages README-*.md)
    for path in (root_dir / "solutions").glob("**/README.md"):
        rel_path = path.relative_to(root_dir).as_posix()
        files_to_process.append((path, rel_path, ["solution", "explanation"]))
        
    # 3. Source files (.py, .sql, .js) under solutions/
    for ext in ("*.py", "*.sql", "*.js"):
        for path in (root_dir / "solutions").glob(f"**/{ext}"):
            # Skip python files that are package init
            if path.name == "__init__.py":
                continue
            rel_path = path.relative_to(root_dir).as_posix()
            files_to_process.append((path, rel_path, ["solution", "source-code"]))
            
    log.info(f"Discovered {len(files_to_process)} target files for ingestion.")
    
    for file_path, rel_path, file_tags in files_to_process:
        if not file_path.exists():
            continue
            
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            stats["skipped_empty"] += 1
            continue
            
        # Dual URLs
        source_url = f"https://github.com/donnemartin/system-design-primer/blob/master/{rel_path}"
        local_path_url = f"file://{file_path.resolve().as_posix()}"
        doc_id = make_doc_id(source_name, source_url)
        
        # Build breadcrumbs context
        hierarchy = ["System Design Primer"]
        parts = Path(rel_path).parent.parts
        for part in parts:
            if part not in ("solutions", "system_design", "object_oriented_design"):
                hierarchy.append(part.replace("_", " ").title())
        hierarchy.append(file_path.name)
        
        file_ext = file_path.suffix.lower()
        if file_ext == ".md":
            chunks = chunk_markdown(
                text=content,
                doc_id=doc_id,
                source_name=source_name,
                source_url=source_url,
                local_path_url=local_path_url,
                tags=tags + file_tags,
                current_dir=file_path.parent,
                file_path=file_path,
                strip_toc=(rel_path == "README.md"),
            )
        else:
            chunks = chunk_code_file(
                content=content,
                doc_id=doc_id,
                source_name=source_name,
                source_url=source_url,
                local_path_url=local_path_url,
                tags=tags + file_tags,
                file_extension=file_ext,
                hierarchy=hierarchy,
            )
            
        deduped_chunks: list[Chunk] = []
        for chunk in chunks:
            normalized = normalize_for_hash(chunk.cleaned_text)
            norm_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            existing = dedup_index.get(norm_hash)
            if existing is None:
                dedup_index[norm_hash] = chunk
                deduped_chunks.append(chunk)
                continue
            existing_meta = existing.metadata or {}
            dup_sources = existing_meta.get("duplicate_sources", [])
            if not isinstance(dup_sources, list):
                dup_sources = []
            dup_sources.append({
                "source_url": chunk.source_url,
                "local_path_url": (chunk.metadata or {}).get("local_path_url"),
                "hierarchy": (chunk.metadata or {}).get("hierarchy"),
                "chunk_id": chunk.chunk_id,
            })
            existing_meta["duplicate_sources"] = dup_sources
            existing.metadata = existing_meta
            stats["dedup_normalized"] += 1

        added = ingestor.add_chunks(deduped_chunks)
        stats["processed_files"] += 1
        stats["chunks_generated"] += len(chunks)
        stats["chunks_added"] += len(added)
        
    total_db_chunks = ingestor.flush()
    log.info(
        f"Completed Ingestion: files_processed={stats['processed_files']} "
        f"chunks_generated={stats['chunks_generated']} "
        f"chunks_added={stats['chunks_added']} "
        f"chunks_deduped={stats['dedup_normalized']} "
        f"total_buffered_in_db={total_db_chunks}"
    )

if __name__ == "__main__":
    run()
