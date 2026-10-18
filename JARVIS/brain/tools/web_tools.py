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
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlparse

log = logging.getLogger("tools.web")

_SEARCH_CACHE = {}
_SEARCH_TTL = 600
_QUICK_SEARCH_CACHE = {}
_QUICK_SEARCH_TTL = 300
_QUICK_SEARCH_TIMEOUT = 4


class _DDGResultParser(HTMLParser):
    """Extrae título, URL y fragmento del HTML ligero de DuckDuckGo."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.current = None
        self.last_result = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if self.current is None:
            if tag == "a" and "result__a" in classes:
                self.current = {"kind": "title", "href": attrs.get("href", ""),
                                "tag": tag, "text": [], "depth": 0}
            elif "result__snippet" in classes:
                self.current = {"kind": "snippet", "href": "", "tag": tag,
                                "text": [], "depth": 0}
        elif tag not in ("br", "img", "meta", "input", "source", "wbr"):
            self.current["depth"] += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == self.current["tag"] and self.current["depth"] == 0:
            item, self.current = self.current, None
        else:
            if tag not in ("br", "img", "meta", "input", "source", "wbr"):
                self.current["depth"] = max(0, self.current["depth"] - 1)
            return

        text = " ".join(" ".join(item["text"]).split())
        if item["kind"] == "snippet":
            if self.last_result is not None and not self.last_result.get("snippet"):
                self.last_result["snippet"] = text[:360]
            return
        href = item["href"]
        try:
            params = parse_qs(urlparse(href).query)
            href = params.get("uddg", [href])[0]
        except Exception:
            pass
        if not href.startswith("http") or not text:
            return
        result = {"title": text[:160], "url": href[:500], "snippet": ""}
        self.results.append(result)
        self.last_result = result
        if len(self.results) > 8:
            removed = self.results.pop(0)
            if self.last_result is removed:
                self.last_result = None


def _parse_ddg_results(html: str) -> list[dict]:
    parser = _DDGResultParser()
    try:
        parser.feed(html or "")
    except Exception as e:
        log.debug("parse DDG results: %s", e)
    return parser.results[:6]


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
        enc = (resp.headers.get("Content-Encoding") or "").lower()
    # Algunos servidores envían gzip aunque no se pidan: si viene magic bytes,
    # descomprimimos o el texto sale con caracteres rotos.
    if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
        try:
            import gzip
            raw = gzip.decompress(raw)
        except Exception:
            try:
                import zlib
                raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
            except Exception:
                pass
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
    """Texto legible: aísla el contenido principal y quita el cromo."""
    import re as _r
    h = html or ""
    main = ""
    for pat in (r'(?is)<main[^>]*>(.*?)</main>',
                r'(?is)<article[^>]*>(.*?)</article>',
                r'(?is)<div[^>]+id=["\']mw-content-text["\'][^>]*>(.*?)</div>\s*</div>',
                r'(?is)<div[^>]+id=["\'](?:content|mainContent|post|entry)["\'][^>]*>(.*?)</div>'):
        m = _r.search(pat, h)
        if m and len(_r.sub(r"(?s)<[^>]+>", " ", m.group(1))) > 400:
            main = m.group(1)
            break
    h = main or h
    h = _r.sub(r"(?is)<(script|style|noscript|svg|head|form|table)[^>]*>.*?</\1>", " ", h)
    h = _r.sub(r"(?is)<!--.*?-->", " ", h)
    h = _r.sub(r"(?is)<nav[^>]*>.*?</nav>", " ", h)
    h = _r.sub(r"(?is)<footer[^>]*>.*?</footer>", " ", h)
    h = _r.sub(r"(?is)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", h)
    h = _r.sub(r"(?s)<[^>]+>", " ", h)
    import html as _h
    h = _h.unescape(h)
    h = _r.sub(r"[ \t\xa0]+", " ", h)
    h = _r.sub(r"\n\s*\n\s*\n+", "\n\n", h)
    lines = [l.strip() for l in h.split("\n")]
    # Fuera migas de navegación y listas de idiomas: fragmentos muy cortos.
    out = [l for l in lines if len(l) > 30]
    seen, uniq = set(), []
    for l in (out or lines):
        k = l[:60]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(l)
    return "\n".join(uniq)


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


# Techo de una búsqueda web. Antes no existía: la única espera era la de los
# reintentos, 13 segundos fijos, y a eso se le suman los intentos de los otros
# dos motores. Ahora hay un número, y el bucle se para al llegar.
PLAZO_BUSQUEDA_S = 12.0


def _search_lite(query: str) -> list[dict]:
    """DuckDuckGo Lite (aguanta bots). Devuelve [{title, url}]. Reintenta
    ante 403/202 (rate-limit intermitente de DDG).

    OJO con las esperas entre reintentos: antes eran 0 + 4 + 9 segundos, y
    eso son 13 segundos de silencio ANTES de saber que no hay red. Medido: sin
    salida a internet, este bucle tardaba 13,0 s exactos. En un contenedor, que
    suele salir por una IP que DuckDuckGo trata peor, es justo el caso normal:
    el señor ve a JARVIS parado y lo que cuenta es que se ha quedado colgado.
    Ahora los intentos caben dentro de PLAZO_BUSQUEDA_S.
    """
    import re
    import time as _t
    html = ""
    fin = _t.monotonic() + PLAZO_BUSQUEDA_S
    for wait in (0, 1.5, 3.0):
        if wait:
            if _t.monotonic() + wait > fin:
                break
            _t.sleep(wait)
        if _t.monotonic() > fin:
            break
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


def quick_web_search_handler(params: dict) -> dict:
    """Búsqueda rápida: una sola petición con plazo corto, sin reintentos,
    descargas de páginas ni llamadas a un LLM. Devuelve títulos, fragmentos y
    enlaces para que JARVIS responda directamente al dato.
    """
    query = " ".join((params.get("query", "") or "").split())
    if not query:
        return {"results": [], "error": "Sin query."}
    key = query.casefold()
    now = time.time()
    hit = _QUICK_SEARCH_CACHE.get(key)
    if hit and now - hit[0] < _QUICK_SEARCH_TTL:
        return {"results": hit[1][:4], "cached": True}

    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    try:
        html = _simple_fetch(url, timeout=_QUICK_SEARCH_TIMEOUT)
    except Exception as e:
        log.info("búsqueda rápida agotó/falló (%s): %s", query[:80], e)
        return {"results": [], "error": "timeout" if isinstance(e, TimeoutError)
                else str(e)[:160]}

    results = _parse_ddg_results(html)
    if not results:
        return {"results": [], "error": "Sin resultados en la respuesta rápida."}
    _QUICK_SEARCH_CACHE[key] = (now, results)
    if len(_QUICK_SEARCH_CACHE) > 100:
        for old_key, _ in sorted(_QUICK_SEARCH_CACHE.items(), key=lambda kv: kv[1][0])[:30]:
            _QUICK_SEARCH_CACHE.pop(old_key, None)
    return {"results": results[:4], "cached": False}


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
    {"name": "web_search_quick",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"query": "str"},
     "description": "Búsqueda web RÁPIDA: una petición con plazo corto; devuelve directamente títulos, fragmentos y enlaces SIN abrir páginas ni llamar al modelo. Úsala cuando el señor pida un resultado concreto o diga rápido.",
     "label": "Búsqueda rápida",
     "handler": quick_web_search_handler},
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
