# -*- coding: utf-8 -*-
"""Web Intelligence (sección 7): búsqueda y lectura real de internet.

- web_search_results: DDG Lite (+reintentos) -> endpoint clásico -> Wikipedia.
  Con caché de 10 min para no machacar a DDG.
- web_fetch: descarga y devuelve TEXTO LIMPIO (charset real, sin menús).
- web_frontpage: titulares RSS o h1/h2.
"""
import logging
import time
import urllib.request
from urllib.parse import quote, unquote

log = logging.getLogger("tools.web")

_SEARCH_CACHE = {}
_SEARCH_TTL = 600


def _simple_fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0 Safari/537.36"),
        "Accept-Language": "es-ES,es;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(300_000)
        charset = None
        try:
            charset = resp.headers.get_content_charset()
        except Exception:
            charset = None
    if not charset:
        import re as _re
        head = raw[:4000].decode("ascii", errors="ignore")
        m = _re.search(r'charset=["\']?([\w\-]+)', head, _re.I)
        charset = (m.group(1) if m else "utf-8")
    try:
        return raw.decode(charset, errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    """HTML -> texto legible: fuera scripts, menús y relleno; dentro párrafos."""
    import html as _html
    import re as _re
    t = _re.sub(r"(?is)<(script|style|noscript|template|svg|form|nav|header|footer|aside)[^>]*>.*?</\1>", " ", html)
    t = _re.sub(r"(?is)<!--.*?-->", " ", t)
    paras = _re.findall(r"(?is)<(p|h1|h2|h3|li|article|blockquote)[^>]*>(.*?)</\1>", t)
    if paras:
        chunks = []
        for _, inner in paras:
            c = _re.sub(r"(?is)<[^>]+>", " ", inner)
            c = _html.unescape(c)
            c = _re.sub(r"[ \t\xa0]+", " ", c).strip()
            words = len(c.split())
            if words >= 4:
                chunks.append(c)
        t = "\n".join(chunks)
    else:
        t = _re.sub(r"(?is)<[^>]+>", " ", t)
        t = _html.unescape(t)
    t = _re.sub(r"[ \t\xa0]+", " ", t)
    t = _re.sub(r"\n\s*\n+", "\n", t)
    lines = [l.strip() for l in t.split("\n")]
    lines = [l for l in lines if len(l) > 2]
    # recorta repeticiones (menús duplicados)
    seen, out = set(), []
    for l in lines:
        k = l[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append(l)
    return "\n".join(out)


def web_fetch_handler(params: dict) -> dict:
    url = params.get("url", "")
    if not url.startswith("http"):
        url = "https://" + url
    try:
        html = _simple_fetch(url, timeout=int(params.get("timeout", 20)))
    except Exception as e:
        return {"url": url, "content": "",
                "error": f"No pude leerla ({e}). Prueba con otro enlace."}
    return {"url": url, "content": _html_to_text(html)[:8000]}


def _search_lite(query: str) -> list[dict]:
    """DuckDuckGo Lite (aguanta bots). Devuelve [{title, url}]. Reintenta
    ante 403/202 (rate-limit intermitente de DDG)."""
    import re
    import time as _t
    html = ""
    for wait in (0, 4, 9):
        if wait:
            _t.sleep(wait)
        try:
            html = _simple_fetch("https://lite.duckduckgo.com/lite/?q=" + quote(query))
            if len(html) > 8000:
                break
        except Exception as e:
            log.debug("lite search: %s", e)
            html = ""
    out, seen = [], set()
    for u, t in re.findall(r'href="([^"]+uddg=[^"]+)"[^>]*>(.*?)</a>', html):
        m = re.search(r"uddg=([^&]+)", u)
        url = unquote(m.group(1)) if m else u
        title = re.sub(r"<[^>]+>", "", t).strip()
        if not title or not url.startswith("http"):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": title[:140], "url": url[:160]})
        if len(out) >= 8:
            break
    return out


def web_search_results_handler(params: dict) -> dict:
    """Busca en la web (DDG Lite + fallback) con caché de 10 min.
    Preguntas enciclopédicas (qué es/quién fue/…) van a Wikipedia primero."""
    import re as _re2
    query = (params.get("query", "") or "").strip()
    if not query:
        return {"results": [], "error": "Sin query."}
    ck = query.lower()
    hit = _SEARCH_CACHE.get(ck)
    if hit and time.time() - hit[0] < _SEARCH_TTL:
        return {"results": hit[1][:6], "cached": True}
    if _re2.search(r"^(qu[ée] es|qu[ée] son|qui[ée]n (fue|es)|d[óo]nde est[áa]|cu[áa]ndo|por ?qu[ée])\b",
                   query, _re2.I):
        try:
            wiki = _search_wiki(query)
            if wiki:
                _SEARCH_CACHE[ck] = (time.time(), wiki)
                return {"results": wiki[:6], "via": "wikipedia"}
        except Exception as e:
            log.debug("wiki-first: %s", e)
    res = {"results": []}
    try:
        lite = _search_lite(query)
        if lite:
            res = {"results": lite[:6]}
    except Exception as e:
        log.debug("lite search: %s", e)
    if not res["results"]:
        from html.parser import HTMLParser

        class _Lite(HTMLParser):
            def __init__(self):
                super().__init__()
                self.texts = []
                self.skip = 0

            def handle_data(self, data):
                if self.skip:
                    return
                self.texts.append(data.strip())

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style"):
                    self.skip += 1

            def handle_endtag(self, tag):
                if tag in ("script", "style") and self.skip:
                    self.skip -= 1

        try:
            html = _simple_fetch("https://html.duckduckgo.com/html/?q=" + quote(query))
            import re
            blocks = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html, re.S)
            urls = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html)
            results = []
            for i, b in enumerate(blocks):
                clean_b = re.sub(r"<[^>]+>", "", b).strip()
                u = urls[i].split("&uddg=")[1].split("&rut=")[0] if len(urls) > i and "&uddg=" in urls[i] else (urls[i] if i < len(urls) else "")
                if clean_b and u.startswith("http"):
                    results.append({"title": clean_b[:140], "url": u[:160]})
            if results:
                res = {"results": results[:6]}
        except Exception as e:
            log.warning("web_search_results falló: %s", e)
    if not res["results"]:
        try:
            res = {"results": _search_wiki(query)}
        except Exception as e:
            log.warning("wiki search falló: %s", e)
            res = {"results": [], "error": str(e)}
    if res.get("results"):
        _SEARCH_CACHE[ck] = (time.time(), res["results"])
        # poda suave
        if len(_SEARCH_CACHE) > 60:
            old = sorted(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])[:20]
            for k in old:
                _SEARCH_CACHE.pop(k, None)
    return res


def _search_wiki(query: str) -> list[dict]:
    """Wikipedia API (sin claves): [{title, url}]."""
    import json as _js
    raw = _simple_fetch(
        "https://es.wikipedia.org/w/api.php?action=query&list=search"
        "&srsearch=" + quote(query) + "&srlimit=6&format=json")
    data = _js.loads(raw).get("query", {}).get("search", [])
    out = []
    for it in data:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        url = "https://es.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        out.append({"title": title[:140], "url": url[:160]})
    return out


def web_frontpage_handler(params: dict) -> dict:
    """Titulares de portada de un medio: RSS si hay, si no h1/h2 de la home."""
    import re
    site = (params.get("site", "") or "").strip().lower()
    feeds = {
        "marca": ["https://e00-marca.uecdn.es/rss/portada.xml"],
        "as": ["https://as.com/rss/diarioas.xml"],
        "sport": ["https://www.sport.es/es/rss/"],
        "mundo deportivo": ["https://www.mundodeportivo.com/rss/"],
        "md": ["https://www.mundodeportivo.com/rss/"],
        "elpais": ["https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"],
        "el país": ["https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"],
        "elmundo": ["https://e00-elmundo.uecdn.es/rss/portada.xml"],
        "abc": ["https://www.abc.es/rss/feeds/abc-portada.xml"],
        "bbc": ["https://feeds.bbci.co.uk/mundo/rss.xml"],
        "cnn": ["http://rss.cnn.com/rss/edition.rss"],
    }
    urls = feeds.get(site, [])
    if not urls:
        page = site if site.startswith("http") else "https://" + site
        urls = [page]
    headlines = []
    for url in urls:
        try:
            html = _simple_fetch(url, timeout=15)
        except Exception as e:
            log.debug("frontpage %s: %s", url, e)
            continue
        if "<rss" in html[:2000] or "<feed" in html[:2000] or "<item>" in html:
            import html as _html
            for m in re.finditer(r"<item>(.*?)</item>", html, re.S):
                it = m.group(1)
                t = re.search(r"<title>(.*?)</title>", it, re.S)
                if not t:
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).strip()
                title = _html.unescape(title)
                title = re.sub(r"<[^>]+>", "", title)
                title = re.sub(r"\s+", " ", title).strip(" -–—")
                if title and title not in [h["title"] for h in headlines]:
                    headlines.append({"title": title[:160]})
                if len(headlines) >= 8:
                    break
        else:
            for tag in ("h1", "h2"):
                for m in re.finditer(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), html, re.S | re.I):
                    title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                    title = re.sub(r"\s+", " ", title)
                    if len(title) >= 20 and title not in [h["title"] for h in headlines]:
                        headlines.append({"title": title[:160]})
                    if len(headlines) >= 8:
                        break
        if headlines:
            break
    if not headlines:
        raise RuntimeError(f"No pude leer la portada de '{site}'")
    return {"site": site, "headlines": headlines[:8]}


TOOLS = [
    {"name": "web_fetch",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"url": "str"},
     "description": "Descarga el TEXTO LIMPIO de una URL para analizarla/resumirla.",
     "label": "Consultando la web",
     "handler": web_fetch_handler},
    {"name": "web_search_results",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"query": "str"},
     "description": "BUSCA en la web y devuelve títulos+enlaces legibles. Úsala siempre que pidan buscar, investigar o resumir algo de internet.",
     "label": "Buscando en la web",
     "handler": web_search_results_handler},
    {"name": "web_frontpage",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"site": "str: marca|as|sport|elpais|elmundo|abc|bbc|cnn o URL"},
     "description": "Trae los titulares de portada de un diario/medio (deportes, prensa).",
     "label": "Leyendo portada",
     "handler": web_frontpage_handler},
]
