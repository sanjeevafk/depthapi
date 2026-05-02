"""
ingest_docs.py — Scrape live documentation sites using Scrapling

Targets (P0): FastAPI, Pydantic, SQLAlchemy
Targets (P1): Next.js, Vue, Docker, Redis

Usage:
    python scripts/ingest_corpus/ingest_docs.py --source fastapi
    python scripts/ingest_corpus/ingest_docs.py --source fastapi pydantic sqlalchemy
    python scripts/ingest_corpus/ingest_docs.py --source all
    python scripts/ingest_corpus/ingest_docs.py --source fastapi --max-pages 50
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ingest_corpus.base_ingestor import BaseIngestor, log, split_text

TARGETS: dict[str, dict] = {
    "fastapi":    {"source_name": "FastAPI Docs",     "start_url": "https://fastapi.tiangolo.com/tutorial/first-steps/",  "allowed_domain": "fastapi.tiangolo.com",   "content_selectors": ["article","main","div.md-content","body"],               "tags": ["fastapi","python","api","P0"],              "max_pages": 400, "delay": 0.3},
    "pydantic":   {"source_name": "Pydantic Docs",    "start_url": "https://docs.pydantic.dev/latest/usage/models/",      "allowed_domain": "pydantic.dev",           "content_selectors": ["article","div.md-content","main","body"],               "tags": ["pydantic","python","validation","P0"],"max_pages": 400, "delay": 0.3},
    "sqlalchemy": {"source_name": "SQLAlchemy Docs",  "start_url": "https://docs.sqlalchemy.org/en/20/orm/",              "allowed_domain": "docs.sqlalchemy.org",    "content_selectors": ["div.body","article","main","body"],          "tags": ["sqlalchemy","python","orm","database","P0"],"max_pages": 400, "delay": 0.3, "path_prefix": "/en/20/"},
    "nextjs":     {"source_name": "Next.js Docs",     "start_url": "https://nextjs.org/docs",                             "allowed_domain": "nextjs.org",             "content_selectors": ["article","main","div","body"],               "tags": ["nextjs","react","frontend","P1"],           "max_pages": 300, "delay": 0.3},
    "vue":        {"source_name": "Vue.js Docs",      "start_url": "https://vuejs.org/guide/introduction",                "allowed_domain": "vuejs.org",              "content_selectors": ["div.content","main","article","body"],        "tags": ["vue","frontend","javascript","P1"],         "max_pages": 200, "delay": 0.3},
    "docker":     {"source_name": "Docker Docs",      "start_url": "https://docs.docker.com/get-started/",               "allowed_domain": "docs.docker.com",        "content_selectors": ["article","main","div.content","body"],        "tags": ["docker","devops","containers","P2"],        "max_pages": 300, "delay": 0.3},
    "redis":      {"source_name": "Redis Docs",       "start_url": "https://redis.io/docs/latest/",                       "allowed_domain": "redis.io",               "content_selectors": ["article","main","div.main-content","body"],   "tags": ["redis","cache","database","P2"],            "max_pages": 200, "delay": 0.3},
}

_SKIP_RE = re.compile(
    r"/(changelog|release-notes|releases|versions|404|notfound|search|_next/|static/|assets/|cdn-cgi|captcha|login|signup)"
    r"|^/(de|es|fr|ja|ko|pt|ru|tr|uk|zh|zh-hans|zh-hant|ar|az|fa|he|id|pl|ro|sq|sv|ta|te|uk|ur|vi)(/|$)",
    re.IGNORECASE,
)


def _fetch(url: str, timeout: int = 15) -> str | None:
    """Fetch a URL and return raw HTML text, or None on error."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DepthAPI-Ingest/1.0; +https://github.com)",
            "Accept": "text/html,application/xhtml+xml",
        }
        r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning(f"  Fetch failed: {url} — {e}")
        return None


def _extract_text_from_html(raw_html: str, content_selectors: list[str]) -> str:
    """Extract main content text using lxml CSS selectors with regex fallback."""
    try:
        from lxml.html import fromstring as _fromstring
        doc = _fromstring(raw_html)

        # Remove noise elements
        for noise_sel in ["script", "style", "nav", "header", "footer", "aside",
                          ".toc", ".sidebar", ".headerlink"]:
            for el in doc.cssselect(noise_sel):
                el.getparent().remove(el)

        # Try selectors in order
        for sel in content_selectors:
            try:
                matches = doc.cssselect(sel)
                if matches:
                    text = " ".join(
                        " ".join(el.itertext()) for el in matches
                    ).strip()
                    if len(text) > 200:
                        return text
            except Exception:
                continue

        # Last resort: whole body
        return " ".join(doc.itertext()).strip()

    except Exception:
        # Pure regex fallback — strip all tags
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def _extract_links(raw_html: str, base_url: str) -> list[str]:
    """Extract all href links from raw HTML."""
    links = []
    for m in re.finditer(r'href=["\']([^"\'#][^"\']*)["\']', raw_html, re.IGNORECASE):
        href = m.group(1)
        # Skip JS templates, email, javascript
        if href and not href.startswith(("javascript:", "mailto:")) and "${" not in href:
            full = urljoin(base_url, href).split("#")[0].rstrip("/")
            links.append(full)
    return links


def _parse_html(raw_html: str, content_selectors: list[str], base_url: str) -> tuple[str, list[str]]:
    """Parse raw HTML and return (content_text, links)."""
    text = _extract_text_from_html(raw_html, content_selectors)
    links = _extract_links(raw_html, base_url)
    return text, links


def is_valid_url(url: str, allowed_domain: str, path_prefix: str | None = None) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or allowed_domain not in p.netloc:
            return False
        if _SKIP_RE.search(p.path):
            return False
        if path_prefix and not p.path.startswith(path_prefix):
            return False
        return True
    except Exception:
        return False


def crawl_target(target_key: str, max_pages: int | None = None) -> None:
    cfg = TARGETS[target_key]
    cap = max_pages or cfg["max_pages"]
    log.info(f"Starting crawl: {cfg['source_name']} (max {cap} pages)")

    ingestor = BaseIngestor(cfg["source_name"], source_type="html")
    visited: set[str] = set()
    queue: deque[str] = deque([cfg["start_url"]])
    chunk_order = 0
    pages_done = 0

    while queue and pages_done < cap:
        url = queue.popleft()
        url_clean = url.rstrip("/")
        if url_clean in visited:
            continue
        visited.add(url_clean)

        raw_html = _fetch(url)
        if not raw_html:
            continue

        text, links = _parse_html(raw_html, cfg["content_selectors"], url)
        if text and len(text) > 200:
            chunks = split_text(text, chunk_size=512, overlap=100)
            ingestor.add(chunks, source_url=url, tags=cfg["tags"], start_order=chunk_order)
            chunk_order += len(chunks)

        for link in links:
            link_clean = link.rstrip("/")
            if is_valid_url(link, cfg["allowed_domain"], cfg.get("path_prefix")) and link_clean not in visited:
                queue.append(link)

        pages_done += 1
        if pages_done % 25 == 0:
            log.info(f"  [{cfg['source_name']}] {pages_done}/{cap} pages | {ingestor.new_count} chunks")
        time.sleep(cfg["delay"])

    total = ingestor.flush()
    log.info(f"[{cfg['source_name']}] Done. Pages: {pages_done} | Total chunks: {total}")


def run(sources: list[str], max_pages: int | None = None) -> None:
    if "all" in sources:
        sources = list(TARGETS.keys())
    unknown = [s for s in sources if s not in TARGETS]
    if unknown:
        log.error(f"Unknown sources: {unknown}. Valid: {list(TARGETS.keys())}")
        sys.exit(1)
    for source in sources:
        crawl_target(source, max_pages=max_pages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape doc sites into corpus")
    parser.add_argument("--source", nargs="+", default=["fastapi","pydantic","sqlalchemy"],
                        help=f"Sources: {list(TARGETS.keys())} or 'all'")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()
    run(args.source, max_pages=args.max_pages)
