from __future__ import annotations

import base64
import json
import os
import re
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlparse

from scrapling.spiders import Spider, Response


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._in_pre = False
        self._in_code = False
        self._code_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "pre":
            self._in_pre = True
            self._code_buffer = []
        elif tag == "code":
            if not self._in_pre:
                self._in_code = True
                self._code_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._in_pre:
            code_text = "".join(self._code_buffer).strip("\n")
            if code_text:
                self.parts.append("```\n" + code_text + "\n```")
            self._code_buffer = []
            self._in_pre = False
        elif tag == "code" and self._in_code:
            code_text = "".join(self._code_buffer).strip()
            if code_text:
                self.parts.append("`" + code_text + "`")
            self._code_buffer = []
            self._in_code = False
        elif tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_pre or self._in_code:
            self._code_buffer.append(data)
        else:
            text = data.replace("\u00a0", " ")
            if text.strip():
                self.parts.append(text)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class DLWPSpider(Spider):
    name = "dlwp_spider"
    start_urls = ["https://deeplearningwithpython.io/"]
    download_delay = 1.5
    concurrent_requests = 3
    concurrent_requests_per_domain = 3

    async def parse(self, response: Response):
        if response.url.rstrip("/") == "https://deeplearningwithpython.io":
            yield response.follow("/chapters/", callback=self.parse_chapters)
            return

        yield None

    async def parse_chapters(self, response: Response):
        links = []
        for link in response.css(".posts-list a[href]"):
            href = link.attrib.get("href", "")
            if href.startswith("/chapters/") and href != "/chapters/":
                links.append(response.urljoin(href))

        for url in sorted(set(links)):
            yield response.follow(url, callback=self.parse_chapter)

    async def parse_chapter(self, response: Response):
        title = response.css("h1::text").get() or ""
        title = title.strip()

        article_html = response.css("article").get() or ""
        if not article_html:
            return

        extractor = TextExtractor()
        extractor.feed(article_html)
        content = extractor.get_text()

        # Remove the internal contents navigation from the chapter page if present.
        content = re.sub(r"^Contents\s+", "", content, flags=re.I)

        slug = self._slug_from_url(response.url)
        os.makedirs("dlwp_pages", exist_ok=True)
        file_path = os.path.join("dlwp_pages", f"{slug}.txt")

        with open(file_path, "w", encoding="utf-8") as f:
            if title:
                f.write(title + "\n\n")
            f.write(content + "\n")

        yield None

    @staticmethod
    def _slug_from_url(url: str) -> str:
        path = urlparse(url).path.strip("/")
        if not path:
            return "index"
        return path.split("/")[-1]


if __name__ == "__main__":
    DLWPSpider(crawldir="crawldata").start()
