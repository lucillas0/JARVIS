"""Web Intelligence: noticias recientes de tecnología/IA y búsqueda general.

Usa feeds RSS (sin API key) y, si hay apertura de URL, permite resumir contenido.
"""
import json
import logging
import re
import urllib.request
from html.parser import HTMLParser

log = logging.getLogger("integrations.web_intel")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self._text.append(t)

    def text(self) -> str:
        return " ".join(self._text)


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _parse_rss(html: str, limit: int = 5) -> list[dict]:
    items = re.findall(r"<item>(.*?)</item>", html, re.S)
    out = []
    for it in items[:limit]:
        title = re.findall(r"<title>(.*?)</title>", it, re.S)
        link = re.findall(r"<link>(.*?)</link>", it, re.S)
        desc = re.findall(r"<description>(.*?)</description>", it, re.S)
        extract = _TextExtractor()
        extract.feed(desc[0] if desc else "")
        out.append({"title": (title[0] if title else "").strip(),
                    "url": (link[0] if link else "").strip(),
                    "summary": extract.text()[:300]})
    return out


def tech_news(limit: int = 5) -> list[dict]:
    """Noticias recientes de tech/IA desde varias fuentes RSS."""
    feeds = [
        "https://www.xataka.com/feed",
        "https://www.muycomputer.com/feed/",
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://www.theverge.com/rss/index.xml",
    ]
    for url in feeds:
        try:
            html = _fetch(url, timeout=15)
            items = _parse_rss(html, limit=limit)
            if items:
                return items
        except Exception as e:
            log.info("Feed %s falló: %s", url, e)
    return []


def search_news(query: str, limit: int = 4) -> list[dict]:
    """Búsqueda de noticias por término (DuckDuckGo News RSS)."""
    try:
        from urllib.parse import quote
        html = _fetch(f"https://news.google.com/rss/search?q={quote(query)}&hl=es&gl=ES&ceid=ES:es")
        items = _parse_rss(html, limit=limit)
        if items:
            return items
    except Exception as e:
        log.info("News RSS falló: %s", e)
    return []


def latest_tech_headlines() -> list[str]:
    """Titulares simples para resúmenes cortos."""
    n = tech_news(limit=4)
    if n:
        return [i["title"] for i in n]
    return []