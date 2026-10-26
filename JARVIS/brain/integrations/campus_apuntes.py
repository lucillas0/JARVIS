# -*- coding: utf-8 -*-
"""El contenido de los apuntes, leído y buscable al instante.

El índice de `campus_index` guardaba los títulos de los cursos. Esto va más
allá: se descarga el contenido de verdad de cada actividad (tareas, páginas,
libros y PDF) y se guarda en disco, para que el señor pueda preguntar por un
tema concreto y reciba el pasaje exacto, no una lista de capítulos.

Cómo se lee cada cosa:
  - Tarea, página, libro, foro: el texto de la página del Moodle.
  - Recurso (PDF): Moodle lo sirve en crudo en la misma dirección, así que se
    descarga como bytes y se lee con pypdf. Un PDF de 26 páginas son 13.000
    caracteres de texto de verdad.

Cómo se busca: no hay modelo ni nada que espere. Un índice inverso palabra ->
documento, y se devuelven los párrafos que más casan con la pregunta. Por eso
responde en milisegundos.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import unicodedata

from integrations.html_utils import html_text_and_markers

log = logging.getLogger("integrations.campus_apuntes")

_LOCK = threading.Lock()
_build = threading.Lock()
_cache: dict | None = None
_cache_t = 0.0

# Cuánto texto guardamos de cada documento. Suficiente de sobra para responder
# y evita que el índice se convierta en un fichero de 200 MB.
_MAX_CHARS = 40000

# Palabras que no sirven para buscar nada.
_VACIAS = set("""
de la el los las un una unos unas y o u en a al con por para sin sobre entre
para que quien cual como cuando donde porque pero si no ser estar tener hacer
se le lo su sus ya muy mas más menos todo todos toda todas otro otra este
esta estos estas ahi ahí aqui aquí mismo asi así entonces tambien también
del desde hasta cada tras segun según ante bajo sobre tras
""".split())

# Tipos de actividad cuyo contenido vale la pena leer.
_TIPOS = ("assign", "page", "book", "resource", "folder", "quiz", "lesson")


def _ruta() -> str:
    try:
        from config import DATA_DIR
        base = str(DATA_DIR)
    except Exception:
        base = os.path.expandvars(r"%APPDATA%\JARVIS\brain-data")
    return os.path.join(base, "campus_apuntes.json")


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_SUFIJOS = ("ciones", "cion", "ciones", "miento", "amiento", "imiento",
            "mente", "ados", "adas", "idos", "idas", "acion", "ución",
            "ando", "iendo", "ado", "ido", "ar", "er", "ir", "es", "s")


def _raiz(p: str) -> str:
    """Reduce a la raíz para que 'segmentación' encuentre 'segmento'.

    Sin esto, preguntar por 'enrutamiento' no encuentra 'enrutado' y parece
    que no hay nada escrito de ese tema.
    """
    if len(p) <= 4:
        return p
    for s in _SUFIJOS:
        if p.endswith(s) and len(p) - len(s) >= 4:
            return p[: -len(s)]
    return p


def _tokens(texto: str) -> list:
    out = []
    for p in re.findall(r"[a-z0-9][a-z0-9_.\-]{1,}", _norm(texto)):
        if p in _VACIAS or len(p) <= 1:
            continue
        r = _raiz(p)
        # Se comparan los cinco primeros caracteres de la raíz: así
        # 'enrutamiento', 'enrutado' y 'enruta' acaban siendo la misma cosa.
        out.append(r[:5] if len(r) > 5 else r)
    return out


# --------------------------------------------------------------------------- #
# Lectura del contenido
# --------------------------------------------------------------------------- #
# Solo cortes muy concretos. Antes estaba "Moodle" suelto y eso cortaba los
# apuntes de Debian enteros, porque la palabra sale en la cabecera de la página.
_CORTES = (
    "Estado de la entrega",
    "Editar entrega",
    "Borrar entrega",
    "Actividad previa",
    "Siguiente actividad",
    "Actividad siguiente",
    "Contactar con el soporte del sitio",
    "Usted se ha identificado como",
    "Desarrollado por Moodle",
    "Resumen de retención de datos",
    "Resumen de retenci",
    "Ayuda de Moodle",
)

def _a_texto(h: str) -> str:
    h = (h or "").replace("\xa0", " ")
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n\s*\n+", "\n\n", h).strip()
    corte = len(h)
    for c in _CORTES:
        i = h.find(c)
        if 0 <= i < corte:
            corte = i
    return h[:corte].strip()


def _html_a_texto(html: str) -> str:
    """Texto útil de una página del Moodle, sin scripts, menú ni formularios.

    Se prueban todas las marcas de contenido y se queda con la que más texto
    da. Cogía la primera que encontraba y eso, en las páginas de los ficheros,
    se quedaba con cuatro palabras.
    """
    texto, marcas = html_text_and_markers(html)
    mejor = _a_texto(texto)
    for inicio in marcas:
        t = _a_texto(texto[inicio:])
        if len(t) > len(mejor):
            mejor = t
    return mejor


def _descargar(url: str) -> tuple[str, str]:
    """(clase, texto). clase = 'pdf' | 'html' | ''"""
    from integrations import campus
    s = campus._client()
    if s is None:
        return "", ""
    try:
        r = s.get(url, timeout=90)
    except Exception as e:
        log.debug("descarga %s: %s", url, e)
        return "", ""
    if r.status_code != 200:
        return "", ""
    if r.content[:4] == b"%PDF":
        try:
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(r.content))
            txt = "\n".join((p.extract_text() or "") for p in rd.pages)
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            return "pdf", txt.strip()[:_MAX_CHARS]
        except Exception as e:
            log.debug("pdf %s: %s", url, e)
            return "pdf", ""
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "html" in ctype or not ctype:
        return "html", _html_a_texto(r.text)[:_MAX_CHARS]
    # .docx, .odt...: si Alguna vez se puede leer, se lee.
    for nombre, mod in (("docx", "docx"), ("odt", "odf"), ("pptx", "pptx")):
        if nombre in ctype:
            try:
                if mod == "docx":
                    import docx as _d
                    d = _d.Document(io.BytesIO(r.content))
                    txt = "\n".join(p.text for p in d.paragraphs)
                elif mod == "pptx":
                    import pptx as _p
                    pr = _p.Presentation(io.BytesIO(r.content))
                    txt = "\n".join(sh.text_frame.text for sl in pr.slides
                                    for sh in sl.shapes if sh.has_text_frame)
                else:
                    from odf import teletype
                    from odf import text as _t
                    from odf.opendocument import load
                    d = load(io.BytesIO(r.content))
                    txt = "\n".join(teletype.extractText(p)
                                    for p in d.getElementsByType(_t.P))
                return nombre, txt.strip()[:_MAX_CHARS]
            except Exception:
                return nombre, ""
    return "", ""


def construir(forzar: bool = False, progreso=None) -> dict:
    """Lee el contenido de todas las actividades y lo guarda en disco."""
    global _cache, _cache_t
    ruta = _ruta()
    if not forzar and _cache is not None and (time.time() - _cache_t) < 600:
        return _cache
    if not forzar and os.path.exists(ruta):
        try:
            if (time.time() - os.path.getmtime(ruta)) < 6 * 3600:
                with open(ruta, "r", encoding="utf-8") as f:
                    _cache = json.load(f)
                _cache_t = time.time()
                _preparar_indice(_cache)
                return _cache
        except Exception:
            pass
    if not _build.acquire(blocking=False):
        return _cache or {"docs": []}
    try:
        from integrations import campus, campus_index as ix
        indice = ix.construir()
        if not campus.login():
            return _cache or {"docs": []}
        base = campus.base_url()
        docs: list[dict] = []
        vistos: set = set()
        total = 0
        for cid, ficha in indice.get("cursos", {}).items():
            cn = ficha.get("nombre", "")
            # actividades con url, de la portada y de la lista de tareas
            candidatas: list[dict] = []
            for s in ficha.get("secciones", []):
                if s.get("url"):
                    candidatas.append({"nombre": s.get("nombre", ""),
                                       "url": s["url"],
                                       "tipo": s.get("tipo", "")})
            for t in ficha.get("tareas", []):
                if t.get("url"):
                    candidatas.append({"nombre": t.get("nombre", ""),
                                       "url": t["url"],
                                       "tipo": t.get("tipo") or "assign"})
            for act in candidatas:
                url = act["url"]
                if url in vistos:
                    continue
                tipo = (act.get("tipo") or "").lower()
                if tipo and tipo not in _TIPOS and "assign" not in tipo:
                    continue
                vistos.add(url)
                total += 1
                clase, texto = _descargar(url)
                if not texto or len(texto) < 200:
                    continue
                nombre = re.sub(r"\s+", " ", act["nombre"]).strip()[:160]
                nombre = nombre.replace("&nbsp;", "").strip(" -\u00a0")
                docs.append({"curso": cn, "curso_id": cid,
                             "actividad": nombre,
                             "tipo": tipo or "assign", "clase": clase,
                             "url": url, "texto": texto})
                if progreso:
                    progreso(len(docs), total, act["nombre"])
        _cache = {"generado": time.time(),
                  "generado_txt": time.strftime("%d/%m/%Y %H:%M"),
                  "docs": docs}
        _cache_t = time.time()
        _preparar_indice(_cache)
        try:
            tmp = ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_cache, f, ensure_ascii=False)
            os.replace(tmp, ruta)
            log.info("apuntes: %d documentos leidos de %d actividades",
                     len(docs), total)
        except Exception as e:
            log.debug("apuntes: no pude guardar: %s", e)
        return _cache
    finally:
        _build.release()


def indexar_al_arrancar() -> None:
    def _t():
        try:
            if _cache is None:
                construir()
            else:
                construir()          # refresca en segundo plano
        except Exception as e:
            log.debug("apuntes arranque: %s", e)
    threading.Thread(target=_t, name="apuntes-boot", daemon=True).start()


def refrescar() -> None:
    def _t():
        try:
            construir(forzar=True)
        except Exception:
            pass
    threading.Thread(target=_t, name="apuntes-refresh", daemon=True).start()


def estado() -> dict:
    # Se lee el índice de disco si hace falta: si no, decía "0 documentos"
    # teniendo 44 guardados, que es justo la clase de mentira que no vale.
    idx = _cache or construir()
    docs = idx.get("docs", [])
    cursos = sorted({d["curso"] for d in docs})
    return {"documentos": len(docs), "cursos": cursos,
            "generado": idx.get("generado_txt", "-"),
            "caracteres": sum(len(d["texto"]) for d in docs)}


# --------------------------------------------------------------------------- #
# Busqueda: sin modelo, sin red, en milisegundos
# --------------------------------------------------------------------------- #
# Índices ya calculados: una vez se trocea el texto de cada documento, no en
# cada pregunta. Antes buscar un tema costaba 73 ms porque repetía el trabajo
# de trocear 116.000 caracteres una y otra vez. Ahora es cuestión de restas.
_toks_doc: list = []        # [set de raíces] por documento
_idf: dict = {}             # raíz -> peso
_pas_doc: list = []         # [(set de raíces, texto)] por trozo de cada documento


def _preparar_indice(idx: dict) -> None:
    """Trocea una sola vez todos los documentos y mide qué palabra es rara.

    Se guardan también los trozos de cada documento ya troceados: al buscar un
    tema se puntúa trozo a trozo, y trocearlos cada vez costaba lo suyo.
    """
    global _toks_doc, _idf, _pas_doc
    docs = idx.get("docs", [])
    _toks_doc = [set(_tokens(d["texto"])) for d in docs]
    _pas_doc = [[(_p(p), p) for p in _pasajes(d["texto"])] for d in docs]
    n = len(docs) or 1
    cuenta: dict = {}
    for t in _toks_doc:
        for x in t:
            cuenta[x] = cuenta.get(x, 0) + 1
    import math
    _idf = {x: math.log(1 + n / c) for x, c in cuenta.items()}


def _indice_listo() -> None:
    if not _toks_doc and (_cache or {}).get("docs"):
        _preparar_indice(_cache)


def _p(texto: str) -> set:
    """Raíces de un trozo de texto."""
    return set(_tokens(texto))


def _pasajes(texto: str) -> list[str]:
    """Trocea el texto en trozos con sentido."""
    partes = re.split(r"\n\s*\n|(?<=[.;:])\s{2,}", texto)
    out = []
    for p in partes:
        p = p.strip()
        if len(p) < 40:
            continue
        out.append(p[:900])
    return out


def buscar(tema: str, limite: int = 6, por_curso: str = "") -> list[dict]:
    """Los pasajes que más se parezcan a 'tema'.

    Puntúa por rareza, no por cuenta: si el señor pregunta por 'dhcp', gana el
    documento que habla de dhcp y no el que menciona 'configuracion' seis
    veces. Y el pasaje que se devuelve tiene que contener la palabra buscada,
    que es donde están los comandos.
    """
    idx = _cache or construir()
    _indice_listo()
    docs = idx.get("docs", [])
    if not docs:
        return []
    palabras = list(dict.fromkeys(_tokens(tema)))
    if not palabras:
        return []
    idf = _idf
    max_idf = max((idf.get(p, 2.0) for p in palabras), default=2.0)
    resultados = []
    for i, d in enumerate(docs):
        if por_curso and por_curso not in d["curso"].lower():
            continue
        toks = _toks_doc[i] if i < len(_toks_doc) else set(_tokens(d["texto"]))
        comunes = [p for p in palabras if p in toks]
        if not comunes:
            continue
        score = sum(idf.get(p, 2.0) for p in comunes)
        # Que aparezca la palabra más rara del tema vale el doble que el resto.
        if max_idf >= 2.0 and any(idf.get(p, 0) >= max_idf - 0.01
                                  for p in comunes):
            score *= 1.6
        # Si el título de la actividad es el tema, ese es sin duda el bueno:
        # preguntar por 'nftables' tiene que salir 'Router Debian: enrutado y
        # nftables', no una tarea que lo mencione de pasada.
        ttit = set(_tokens(d.get("actividad", "")))
        if ttit & set(palabras):
            score *= 2.2
        if set(_tokens(d.get("curso", ""))) & set(palabras):
            score *= 1.4
        resultados.append((score, d, i))
    if not resultados:
        return []
    resultados.sort(key=lambda x: -x[0])
    salida = []
    for score, d, i in resultados[:limite]:
        mejor, mejor_p = "", -1.0
        for tp, texto in (_pas_doc[i] if i < len(_pas_doc)
                          else [(_p(p), p) for p in _pasajes(d["texto"])]):
            comunes = [t for t in palabras if t in tp]
            if not comunes:
                continue
            ps = sum(idf.get(t, 2.0) for t in comunes)
            if ps > mejor_p:
                mejor, mejor_p = texto, ps
        if not mejor:
            # Ningún pasaje con la palabra exacta: se da el principio del doc.
            mejor = d["texto"][:700]
        salida.append({"curso": d["curso"], "actividad": d["actividad"],
                       "tipo": d["tipo"], "clase": d["clase"],
                       "url": d["url"], "pasaje": mejor, "puntos": score})
    return salida


def _frase_pedir(tema: str, hallados: list) -> str:
    partes = []
    for h in hallados[:4]:
        p = h["pasaje"].strip()
        if len(p) > 700:
            p = p[:700] + "…"
        partes.append(f"  · De «{h['actividad']}» ({h['curso']}):\n    {p}")
    urls = [h["url"] for h in hallados[:3] if h.get("url")]
    if urls:
        partes.append("  Está en: " + ", ".join(urls))
    return "\n".join(partes)


def respuesta(tema: str, por_curso: str = "") -> str:
    """Respuesta directa, sin modelo, con los apuntes delante."""
    hallados = buscar(tema, limite=5, por_curso=por_curso)
    if not hallados:
        idx = _cache or construir()
        n = len(idx.get("docs", []))
        if not n:
            return ("Todavía no he leído los apuntes, señor. Los estoy indexando "
                    "ahora mismo; en un par de minutos le respondo.")
        return (f"No encuentro «{tema}» en sus apuntes, señor. "
                f"Tengo {n} documentos leídos de sus cursos.")
    c = f"En {hallados[0]['curso']}: " if por_curso else ""
    return (f"{c}esto es lo que dicen sus apuntes sobre «{tema}», señor:\n\n"
            + _frase_pedir(tema, hallados))


def contexto_para_modelo(tema: str, limite: int = 5) -> str:
    """Pasajes para dárselos al modelo cuando la pregunta es abierta."""
    hallados = buscar(tema, limite=limite)
    if not hallados:
        return ""
    lineas = ["APUNTES DEL SEÑOR (Moodle, verbatim; úsalos, no inventes):"]
    for h in hallados:
        p = h["pasaje"].strip()
        if len(p) > 1200:
            p = p[:1200] + "…"
        lineas.append(f"[{h['curso']} · {h['actividad']}] {p}")
    return "\n".join(lineas)
