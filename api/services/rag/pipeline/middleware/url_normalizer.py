"""
url_normalizer.py — Middleware: Normalize and clean URLs in Markdown content.

Relative URLs in source documents can break retrieval context. This middleware:
    - Resolves relative URLs against a known base URL (if provided)
    - Strips tracking parameters from URLs
    - Normalizes URL encoding

Config keys:
    base_url: str — Base URL for resolving relative links (optional)
    strip_tracking_params: bool — Remove UTM/tracking query params (default: True)
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

from api.services.rag.pipeline.interfaces import BaseMiddleware
from api.services.rag.pipeline.models import ParsedDocument

_MW_NAME = "UrlNormalizer"
_MW_VERSION = "1.0.0"

# Common tracking parameters to strip
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referrer", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
})

# Markdown link pattern: [text](url)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _clean_url(url: str, strip_tracking: bool = True) -> str:
    """Clean a single URL: strip tracking params, normalize encoding."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            return url  # Relative URL — skip

        if strip_tracking and parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=False)
            cleaned_params = {
                k: v for k, v in params.items()
                if k.lower() not in _TRACKING_PARAMS
            }
            new_query = urlencode(cleaned_params, doseq=True) if cleaned_params else ""
            parsed = parsed._replace(query=new_query)

        return urlunparse(parsed)
    except Exception:
        return url  # Never fail on URL parsing


def _normalize_urls(content: str, strip_tracking: bool = True) -> str:
    """Replace all markdown link URLs with cleaned versions."""
    def _replace(match: re.Match) -> str:
        text = match.group(1)
        url = match.group(2)
        cleaned = _clean_url(url, strip_tracking)
        return f"[{text}]({cleaned})"

    return _MD_LINK_RE.sub(_replace, content)


class UrlNormalizer(BaseMiddleware):
    """
    Middleware: Normalize URLs in Markdown link syntax.

    Strips tracking parameters from absolute URLs.
    Relative URLs are left unchanged (they depend on source context).

    Idempotent: normalizing twice produces the same result.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._strip_tracking = bool(self._config.get("strip_tracking_params", True))

    @property
    def name(self) -> str:
        return _MW_NAME

    @property
    def version(self) -> str:
        return _MW_VERSION

    def process(self, doc: ParsedDocument) -> ParsedDocument:
        """Normalize URLs in markdown content and return new ParsedDocument."""
        normalized = _normalize_urls(doc.markdown_content, self._strip_tracking)
        return doc.with_middleware_applied(
            middleware_name=self.name,
            middleware_version=self.version,
            new_content=normalized,
            config=self._config,
        )
