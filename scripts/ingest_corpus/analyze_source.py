"""
analyze_source.py — Pre-flight diagnostic tool for analyzing raw corpus source files.
Counts characters, estimates tokens using BAAI/bge-base-en-v1.5, detects headers, 
identifies large sections exceeding limits, detects ASCII diagrams outside code blocks, 
and compiles unique code block languages.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from transformers import AutoTokenizer

def is_line_ascii_diagram(line: str) -> bool:
    """Detect if a line resembles part of an ASCII architecture diagram."""
    # Check for box-drawing characters, arrows, or specific patterns
    # forming boxes like '+---+', '|   |', '---->', '<----'
    # Must have some combinations of box structure
    if len(line.strip()) < 3:
        return False
    # Check for '+' followed by '-' or '|' and arrows
    has_box_corners = '+' in line and '-' in line
    has_arrows = '->' in line or '<-' in line or '-->' in line or '<--' in line
    has_vertical_bars = '|' in line and (line.count('|') >= 2 or '  ' in line)
    has_dashes = '---' in line and len(line.strip()) > 5
    
    return bool(has_box_corners or has_arrows or has_vertical_bars or has_dashes)

def analyze(input_file: Path, tokenizer_name: str = "BAAI/bge-base-en-v1.5") -> dict[str, Any]:
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
        
    content = input_file.read_text(encoding="utf-8")
    
    # Init tokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
    total_tokens = len(tokenizer.encode(content, truncation=False))
    
    # 1. Gather all headers
    headers = []
    # Match headers starting with #, ##, or ###
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    for match in header_pattern.finditer(content):
        level = len(match.group(1))
        title = match.group(2).strip()
        start_pos = match.start()
        headers.append({
            "level": level,
            "title": title,
            "start": start_pos
        })
        
    # 2. Divide sections by header and analyze size
    sections = []
    for i, h in enumerate(headers):
        start = h["start"]
        end = headers[i + 1]["start"] if i + 1 < len(headers) else len(content)
        sec_content = content[start:end]
        
        # Calculate tokens for the section
        sec_tokens = len(tokenizer.encode(sec_content, truncation=False))
        
        sections.append({
            "header": f"{'#' * h['level']} {h['title']}",
            "level": h["level"],
            "title": h["title"],
            "character_count": len(sec_content),
            "estimated_tokens": sec_tokens,
            "exceeds_512": sec_tokens > 512
        })
        
    large_sections = [s for s in sections if s["exceeds_512"]]
    
    # 3. Code block detection and language counting
    code_block_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    code_blocks = []
    languages = set()
    for match in code_block_pattern.finditer(content):
        lang = match.group(1) or "plain"
        code_blocks.append({
            "language": lang,
            "start": match.start(),
            "end": match.end()
        })
        languages.add(lang)
        
    # Helper to check if a position is inside any code block
    def in_code_block(pos: int) -> bool:
        for block in code_blocks:
            if block["start"] <= pos < block["end"]:
                return True
        return False

    # 4. ASCII Diagram detection
    lines = content.splitlines()
    ascii_diagram_lines = []
    current_diagram = []
    
    # Track character positions roughly for code block detection
    current_pos = 0
    for line in lines:
        line_len = len(line) + 1  # include newline
        
        # Only check outside code blocks
        if not in_code_block(current_pos):
            if is_line_ascii_diagram(line):
                current_diagram.append((current_pos, line))
            else:
                if len(current_diagram) >= 3:
                    ascii_diagram_lines.append(list(current_diagram))
                current_diagram = []
        else:
            if len(current_diagram) >= 3:
                ascii_diagram_lines.append(list(current_diagram))
            current_diagram = []
            
        current_pos += line_len
        
    # Append the last diagram if any
    if len(current_diagram) >= 3:
        ascii_diagram_lines.append(list(current_diagram))
        
    # Extract sample diagrams (lines 10-20 or first matching diagram)
    diagram_samples = []
    for diag in ascii_diagram_lines:
        diagram_samples.append("\n".join([line for _, line in diag]))
        if len(diagram_samples) >= 3:
            break
            
    report = {
        "file_name": input_file.name,
        "total_characters": len(content),
        "total_estimated_tokens": total_tokens,
        "header_counts": {
            "H1": len([h for h in headers if h["level"] == 1]),
            "H2": len([h for h in headers if h["level"] == 2]),
            "H3": len([h for h in headers if h["level"] == 3]),
            "H4": len([h for h in headers if h["level"] >= 4])
        },
        "total_sections": len(sections),
        "oversized_sections_count": len(large_sections),
        "oversized_sections": [
            {
                "header": s["header"],
                "character_count": s["character_count"],
                "estimated_tokens": s["estimated_tokens"]
            }
            for s in large_sections
        ],
        "ascii_diagrams_detected": len(ascii_diagram_lines),
        "ascii_diagram_samples": diagram_samples,
        "unique_code_block_languages": sorted(list(languages))
    }
    
    return report

def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-flight validation tool for RAG ingestion")
    parser.add_argument("--input", required=True, help="Path to input markdown file")
    parser.add_argument("--report", required=True, help="Path to output JSON report file")
    args = parser.parse_args()
    
    try:
        report = analyze(Path(args.input))
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
        print(f"Pre-flight analysis completed successfully! Report saved to {args.report}")
        print(f"Total Characters: {report['total_characters']}")
        print(f"Total Estimated Tokens: {report['total_estimated_tokens']}")
        print(f"Oversized Sections (>512 tokens): {report['oversized_sections_count']}")
        print(f"ASCII Diagrams Detected: {report['ascii_diagrams_detected']}")
        print(f"Unique Code Block Languages: {report['unique_code_block_languages']}")
    except Exception as e:
        print(f"Error executing pre-flight validation: {e}")
        raise

if __name__ == "__main__":
    main()
