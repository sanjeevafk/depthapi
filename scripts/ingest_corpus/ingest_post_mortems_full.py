"""
ingest_post_mortems_full.py — Comprehensive scraper and ingestor for the full-text post-mortems.

Parses datasets/post-mortems/README.md, downloads full-text reports,
converts HTML/PDF to clean Markdown, and ingests them into the 'system_design' namespace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
try:
    from scrapling import Fetcher
except ImportError:
    Fetcher = None
from bs4 import BeautifulSoup
import markdownify

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingest_corpus.base_ingestor import (
    BaseIngestor,
    REPO_ROOT,
    log,
    split_text_semantic,
)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "postmortems_raw"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_postmortem_entries() -> list[dict[str, Any]]:
    readme_path = REPO_ROOT / "datasets" / "post-mortems" / "README.md"
    if not readme_path.exists():
        log.error(f"post-mortems README.md not found at {readme_path}")
        return []
        
    content = readme_path.read_text(encoding="utf-8")
    lines = content.split('\n')
    entries = []
    current_section = "None"
    
    ignored_sections = {"Other lists of postmortems", "Analysis", "Contributors", "None"}
    
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("## "):
            current_section = line_stripped[3:].strip()
            continue
            
        if current_section in ignored_sections:
            continue
            
        # Match [Company](URL) or similar at the beginning of the line
        match = re.search(r'^(?:[\-\*\s\d\.]+)?\[([^\]]+)\]\((https?://[^\)]+)\)', line_stripped)
        if match:
            company = match.group(1).strip()
            url = match.group(2).strip()
            description = line_stripped[match.end():].strip()
            
            if description.startswith('.') or description.startswith(':'):
                description = description[1:].strip()
                
            entries.append({
                "company": company,
                "url": url,
                "description": description,
                "category": current_section,
                "line_num": idx + 1
            })
            
    return entries

def get_cache_path(url: str) -> Path:
    # Use MD5/SHA256 of URL for unique filename, append .pdf or .html
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    parsed = urlparse(url)
    ext = ".pdf" if parsed.path.lower().endswith(".pdf") else ".html"
    return CACHE_DIR / f"{url_hash}{ext}"

def download_file(url: str, cache_path: Path, fetcher: Any = None, client: httpx.Client | None = None) -> bool:
    try:
        log.info(f"Downloading: {url}")
        if fetcher:
            response = fetcher.get(url, timeout=20)
            status_code = getattr(response, "status_code", 200)
            if status_code == 200:
                content = getattr(response, "content", None) or getattr(response, "body", None)
                if isinstance(content, str):
                    cache_path.write_text(content, encoding="utf-8")
                elif isinstance(content, bytes):
                    cache_path.write_bytes(content)
                else:
                    text = getattr(response, "text", "")
                    cache_path.write_text(text, encoding="utf-8")
                return True
            else:
                log.warning(f"Failed download status={status_code} for {url}")
                return False
        else:
            if client is None:
                with httpx.Client(follow_redirects=True, timeout=20.0) as temp_client:
                    response = temp_client.get(url, headers=HEADERS, timeout=20.0)
                    if response.status_code == 200:
                        cache_path.write_bytes(response.content)
                        return True
                    else:
                        log.warning(f"Failed download status={response.status_code} for {url}")
                        return False
            else:
                response = client.get(url, headers=HEADERS, timeout=20.0, follow_redirects=True)
                if response.status_code == 200:
                    cache_path.write_bytes(response.content)
                    return True
                else:
                    log.warning(f"Failed download status={response.status_code} for {url}")
                    return False
    except Exception as e:
        log.error(f"Error downloading {url}: {e}")
        return False

def extract_pdf_to_text(pdf_path: Path) -> str:
    try:
        import tempfile
        import opendataloader_pdf  # lazy — allows module import without Java/PDF deps
        with tempfile.TemporaryDirectory() as tmpdir:
            opendataloader_pdf.convert(
                input_path=[str(pdf_path)],
                output_dir=tmpdir,
                format="markdown",
            )
            # Find candidate markdown file
            for candidate in Path(tmpdir).rglob("*.md"):
                return candidate.read_text(encoding="utf-8")
        log.warning(f"No markdown output found from opendataloader-pdf for {pdf_path.name}")
        return ""
    except Exception as e:
        log.error(f"opendataloader-pdf failed for {pdf_path.name}: {e}")
        return ""

def clean_html_content(html_content: str, url: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Remove script, style, iframe, noscript, etc.
    for tag in soup(["script", "style", "iframe", "noscript", "form", "svg", "header", "footer", "nav", "aside"]):
        tag.decompose()
        
    # Strip elements that are commonly sidebar, comments, share widgets, etc.
    for element in soup.find_all(id=re.compile(r"(comments|sidebar|footer|menu|nav|share|widget|cookie|banner)", re.I)):
        element.decompose()
    for element in soup.find_all(class_=re.compile(r"(comments|sidebar|footer|menu|nav|share|widget|cookie|banner|social|related|popup|modal)", re.I)):
        element.decompose()
        
    # Wayback Machine specific cleanup
    if "web.archive.org" in url:
        for element in soup.find_all(id=re.compile(r"^(wm-|wb-|playback-|archive-)", re.I)):
            element.decompose()
        for element in soup.find_all(class_=re.compile(r"^(wm-|wb-|playback-|archive-)", re.I)):
            element.decompose()
            
    # Locate main content body
    main_content = None
    for selector in ["article", "main", "[role='main']", ".post-content", ".entry-content", "#content", ".content"]:
        found = soup.select_one(selector)
        if found:
            main_content = found
            break
            
    if not main_content:
        main_content = soup.body if soup.body else soup
        
    # Convert HTML to markdown
    markdown = markdownify.markdownify(str(main_content), heading_style="ATX").strip()
    
    # Remove multiple blank lines
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    return markdown

def run(max_files: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    entries = get_postmortem_entries()
    log.info(f"Loaded {len(entries)} postmortem entries from README.md")
    
    if max_files is not None:
        entries = entries[:max_files]
        log.info(f"Limited to first {max_files} entries")
        
    if dry_run:
        log.info("Dry run enabled. No files will be downloaded or ingested.")
        return {"loaded_entries": len(entries)}
        
    source_name = "Tech Postmortems Full"
    ingestor = BaseIngestor(source_name, source_type="markdown", validators=[])
    
    stats = Counter()
    chunk_order = 0
    namespace = "system_design"
    tags = ["postmortem", "incident", "architecture", "devops", "P2"]
    
    # Store domains to throttle requests per domain
    last_request_time: dict[str, float] = {}
    
    fetcher = Fetcher() if Fetcher else None
    if fetcher:
        log.info("Using Scrapling Fetcher for incident report scraping.")
    else:
        log.info("Scrapling not available, falling back to httpx Client.")

    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
        for idx, entry in enumerate(entries):
            company = entry["company"]
            url = entry["url"]
            category = entry["category"]
            description = entry["description"]
            
            log.info(f"[{idx+1}/{len(entries)}] Processing: {company} ({url})")
            
            cache_path = get_cache_path(url)
            success = False
            
            # Check cache
            if cache_path.exists():
                log.info(f"  Found in cache: {cache_path.name}")
                success = True
                stats["cache_hits"] += 1
            else:
                # Domain-based throttling to respect site rate limits
                parsed_url = urlparse(url)
                domain = parsed_url.netloc
                
                # Check when we last requested this domain
                now = time.time()
                elapsed = now - last_request_time.get(domain, 0)
                if elapsed < 1.5:
                    sleep_time = 1.5 - elapsed
                    log.info(f"  Throttling domain {domain} for {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                
                # Download
                success = download_file(url, cache_path, fetcher=fetcher, client=client)
                last_request_time[domain] = time.time()
                
                if success:
                    stats["downloads_success"] += 1
                else:
                    stats["downloads_failed"] += 1
            
            extracted_text = ""
            if success:
                try:
                    if cache_path.suffix == ".pdf":
                        extracted_text = extract_pdf_to_text(cache_path)
                        stats["pdf_parsed"] += 1
                    else:
                        html_content = cache_path.read_text(encoding="utf-8", errors="ignore")
                        extracted_text = clean_html_content(html_content, url)
                        stats["html_parsed"] += 1
                except Exception as e:
                    log.error(f"Error parsing cached file {cache_path}: {e}")
                    stats["parse_errors"] += 1
            
            # Enrich text: Combine company name, source URL, README summary, and full-text content
            title = f"{company}"
            full_content_text = f"Incident Report / Postmortem: {title}\nCategory: {category}\nSource: {url}\n\n"
            full_content_text += f"Summary:\n{description}\n\n"
            
            if extracted_text.strip():
                full_content_text += f"Full Report:\n{extracted_text}"
                stats["with_full_text"] += 1
            else:
                log.warning(f"No full-text extracted for {company}. Falling back to README summary only.")
                stats["readme_only_fallback"] += 1
                
            # Chunking the enriched text
            chunks = split_text_semantic(full_content_text, chunk_size=1000, overlap_words=40)
            if not chunks and len(full_content_text.strip()) > 10:
                chunks = [full_content_text]
                
            if not chunks:
                stats["skip_empty"] += 1
                continue
                
            metadata = [
                {
                    "dataset_root": "datasets/post-mortems",
                    "relative_path": f"README.md#L{entry['line_num']}",
                    "namespace": namespace,
                    "company": company,
                    "category": category,
                    "url": url,
                }
                for _ in chunks
            ]
            
            added = ingestor.add(
                chunks,
                source_url=url,
                tags=tags,
                start_order=chunk_order,
                metadata=metadata,
            )
            
            chunk_order += len(chunks)
            stats["chunks_candidate"] += len(chunks)
            stats["chunks_added"] += len(added)
            stats["processed_entries"] += 1
            
    total = ingestor.flush()
    log.info(f"[{source_name}] processed={stats['processed_entries']} added={stats['chunks_added']} total_chunks_after_flush={total}")
    
    return dict(stats)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape and ingest full-text post-mortems")
    parser.add_argument("--max-files", type=int, default=None, help="Maximum number of entries to process")
    parser.add_argument("--dry-run", action="store_true", help="Print loaded entries count and exit")
    args = parser.parse_args()
    
    start_time = time.time()
    results = run(max_files=args.max_files, dry_run=args.dry_run)
    elapsed = time.time() - start_time
    
    print("\n--- Ingestion Job Completed ---")
    print(f"Elapsed Time: {elapsed:.2f} seconds")
    print("Results:")
    for k, v in results.items():
        print(f"  {k}: {v}")
