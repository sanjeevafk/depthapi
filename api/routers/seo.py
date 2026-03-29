"""SEO and crawler endpoints."""

from datetime import date
import os
from xml.sax.saxutils import escape
from fastapi import APIRouter
from fastapi.responses import Response, PlainTextResponse

router = APIRouter(tags=["seo"])


def _base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://yourdomain.com").rstrip("/")


@router.get("/robots.txt")
async def robots_txt():
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin",
            "Disallow: /api",
            f"Sitemap: {_base_url()}/sitemap.xml",
        ]
    )
    return PlainTextResponse(content=content, media_type="text/plain")


@router.get("/sitemap.xml")
async def sitemap_xml():
    today = date.today().isoformat()
    base = _base_url()
    urls = [
        {
            "loc": f"{base}/",
            "lastmod": today,
            "changefreq": "weekly",
        },
        {
            "loc": f"{base}/pricing",
            "lastmod": today,
            "changefreq": "monthly",
        },
        {
            "loc": f"{base}/features",
            "lastmod": today,
            "changefreq": "monthly",
        },
        {
            "loc": f"{base}/terms",
            "lastmod": today,
            "changefreq": "yearly",
        },
        {
            "loc": f"{base}/privacy",
            "lastmod": today,
            "changefreq": "yearly",
        },
    ]

    body = "".join(
        [
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
            *[
                "".join(
                    [
                        "<url>",
                        f"<loc>{escape(entry['loc'])}</loc>",
                        f"<lastmod>{escape(entry['lastmod'])}</lastmod>",
                        f"<changefreq>{escape(entry['changefreq'])}</changefreq>",
                        "</url>",
                    ]
                )
                for entry in urls
            ],
            "</urlset>",
        ]
    )

    return Response(content=body, media_type="application/xml")
