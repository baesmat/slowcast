"""Fetch a URL and extract clean article text.

Uses trafilatura for the main extraction with a regex fallback for the title.
Anything that is not HTML (PDFs, images) raises ExtractError with a clear message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
import trafilatura

USER_AGENT = "Slowcast/0.1 (+https://github.com/baesmat/slowcast; self-hosted reader)"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


class ExtractError(Exception):
    """Raised when a URL cannot be fetched or contains no extractable article."""


@dataclass
class Article:
    url: str
    title: str
    text: str
    image: str = ""


async def fetch(url: str, timeout: float = 25.0) -> tuple[str, str]:
    """Return (final_url, body). Raises ExtractError on failure."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractError(f"fetch failed: {exc}") from exc

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "xml" not in content_type:
        raise ExtractError(f"unsupported content-type '{content_type.split(';')[0]}' (HTML only)")
    return str(resp.url), resp.text


def _fallback_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    if not match:
        return ""
    title = _TAG_RE.sub("", match.group(1))
    return _WS_RE.sub(" ", title).strip()[:300]


def extract(html: str, url: str) -> Article:
    """Extract title + plain text from an HTML document."""
    title = ""
    image = ""
    text = ""
    try:
        meta = trafilatura.bare_extraction(
            html, url=url, include_images=True, with_metadata=True
        )
    except Exception:  # noqa: BLE001 - trafilatura raises many shapes; fall back below
        meta = None

    def meta_get(key: str) -> str:
        # dict in trafilatura 1.x, slots-based Document in 2.x
        if isinstance(meta, dict):
            return str(meta.get(key) or "")
        return str(getattr(meta, key, "") or "")

    if meta is not None:
        title = meta_get("title")
        image = meta_get("image")
        text = meta_get("text")

    if not title:
        title = _fallback_title(html)
    if not text:
        raise ExtractError("no article body found (page may be JS-rendered or not an article)")

    return Article(url=url, title=title, text=text, image=image)


def word_count(text: str) -> int:
    return len(text.split())

def estimated_minutes(words: int, wpm: int = 155) -> float:
    return round(words / wpm, 1) if words else 0.0
