# -*- coding: utf-8 -*-
"""Analiza un archivo en local, y rápido.

El señor le pasa un fichero y quiere saber qué es, de qué va y qué tiene
dentro, sin esperar. Aquí no se pregunta a ningún modelo para el primer
"de qué va": se lee el texto, se cuentan cosas y se sacan las partes
interesantes. Determinista y en milisegundos.

Cuando la pregunta de verdad necesita pensar ("¿está bien hecho este
trabajo?"), el texto extraído se le da al modelo como contexto, que es lo
que ya hace el lector de ficheros.

El texto se cachea por archivo y fecha de modificación: la segunda vez que
se pregunta por el mismo documento, la respuesta es inmediata.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata

log = logging.getLogger("brain.file_analysis")

_LOCK = threading.Lock()
_cache: dict = {}

TEXTOS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".cmd",
    ".sql", ".html", ".htm", ".css", ".scss", ".tex", ".srt", ".vtt",
}
BINARIOS_ESPECIALES = {
    ".pdf": "PDF", ".docx": "Word", ".doc": "Word", ".odt": "ODT",
    ".pptx": "PowerPoint", ".ppt": "PowerPoint", ".odp": "ODP",
    ".xlsx": "Excel", ".xls": "Excel", ".ods": "ODS", ".rtf": "RTF",
    ".zip": "ZIP", ".7z": "7Z", ".rar": "RAR", ".tar": "TAR", ".gz": "GZIP",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio",
    ".mp4": "vídeo", ".mkv": "vídeo", ".mov": "vídeo", ".avi": "vídeo",
    ".png": "imagen", ".jpg": "imagen", ".jpeg": "imagen", ".gif": "imagen",
    ".bmp": "imagen", ".webp": "imagen", ".ico": "imagen",
}
IMAGENES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico")

_LENGUAJE = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".java": "Java", ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL", ".html": "HTML",
    ".css": "CSS", ".json": "JSON", ".xml": "XML", ".md": "Markdown",
    ".tex": "LaTeX", ".yml": "YAML", ".yaml": "YAML", ".bat": "Lote",
    ".cmd": "Lote", ".vbs": "VBScript",
}


def _ruta_cache() -> str:
    try:
        from config import DATA_DIR
        base = str(DATA_DIR)
    except Exception:
        base = os.path.expandvars(r"%APPDATA%\JARVIS\brain-data")
    return os.path.join(base, "archivos_cache.json")


def _cargar_cache() -> dict:
    global _cache
    with _LOCK:
        if _cache:
            return _cache
        try:
            ruta = _ruta_cache()
            if os.path.exists(ruta):
                with open(ruta, "r", encoding="utf-8") as f:
                    _cache = json.load(f)
        except Exception:
            _cache = {}
        if not isinstance(_cache, dict):
            _cache = {}
        return _cache


def _guardar_cache() -> None:
    try:
        ruta = _ruta_cache()
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
        os.replace(tmp, ruta)
    except Exception as e:
        log.debug("cache de archivos: %s", e)


def _sin_tildes(t: str) -> str:
    return unicodedata.normalize(
        "NFKD", (t or "").lower()).encode("ascii", "ignore").decode()


def _human(bytes_: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024 or unidad == "GB":
            return f"{bytes_:.0f} {unidad}" if unidad == "B" else \
                f"{bytes_:.1f} {unidad}"
        bytes_ /= 1024.0
    return ""


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
def _extrae(path: str) -> tuple[str, str, bool]:
    """(texto, clase). clase: texto | documento | imagen | binario | vacio"""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGENES:
        return "", "imagen", False
    try:
        from file_reader import preview
    except Exception:
        return "", "binario", False
    try:
        r = preview(path)
    except Exception as e:
        log.debug("file_reader: %s", e)
        r = {"ok": False}
    texto = (r.get("text") or "").strip() if r.get("ok") else ""
    # Si el lector ha recortado por tamaño, hay que decirlo: si no, el señor
    # cree que lo ha leído entero cuando solo ha visto el principio.
    if r.get("truncated"):
        truncado = True
    else:
        truncado = False
    if not texto and ext in TEXTOS:
        # file_reader no cubre todos los formatos de texto; estos se leen
        # directamente, que para un .cmd o un .log estrivial.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                texto = f.read().strip()
        except Exception:
            texto = ""
    if not texto and not ext:
        # Sin extensión: se huele el contenido (el hosts, por ejemplo).
        try:
            with open(path, "rb") as f:
                trozo = f.read(4096)
            if b"\x00" not in trozo:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    texto = f.read().strip()
        except Exception:
            texto = ""
    if not texto:
        return "", "binario", truncado
    return texto, ("texto" if ext in TEXTOS or not ext else "documento"), truncado


def _clave(path: str) -> str:
    try:
        stt = os.stat(path)
        return f"{os.path.abspath(path).lower()}|{int(stt.st_mtime)}|{stt.st_size}"
    except Exception:
        return os.path.abspath(path).lower()


def texto_de(path: str) -> str:
    """El texto del archivo, cacheado por fecha y tamaño."""
    if not path or not os.path.exists(path):
        return ""
    k = _clave(path)
    c = _cargar_cache()
    hit = c.get(k)
    if hit and "texto" in hit:
        return hit["texto"]
    texto, _clase, truncado = _extrae(path)
    c[k] = {"texto": texto, "truncado": truncado, "ts": time.time()}
    # No guardar más de 60 archivos: si no, la caché crece sin parar.
    if len(c) > 60:
        recientes = sorted(c.items(), key=lambda kv: kv[1].get("ts", 0))
        c.clear()
        c.update(recientes[-60:])
    _guardar_cache()
    return texto


# --------------------------------------------------------------------------- #
# Descripción rápida, sin modelo
# --------------------------------------------------------------------------- #
def _partes_interesantes(texto: str, n: int = 3) -> list[str]:
    """Trocea y se queda con los trozos que parecen contenido, no relleno."""
    if not texto:
        return []
    partes = re.split(r"\n\s*\n|(?<=[.;:])\s{2,}", texto)
    out, vistos = [], set()
    relleno = re.compile(
        r"^(índice|indice|contenido|summary|resumen|abstract|introducci[óo]n|"
        r"abstract|referencias|bibliograf[ií]a|ap[eé]ndice|anexo|"
        r"\d+\.?$|p[aá]gina \d+|-\s*$)", re.I)
    for p in partes:
        p = re.sub(r"[ \t]+", " ", p).strip()
        if len(p) < 60 or len(p) > 700:
            continue
        if relleno.match(p):
            continue
        # Un trozo con muchos numeros y pocas palabras suele ser una tabla.
        letras = sum(c.isalpha() for c in p)
        if letras < len(p) * 0.5:
            continue
        k = p[:60].lower()
        if k in vistos:
            continue
        vistos.add(k)
        out.append(p)
        if len(out) >= n:
            break
    return out


# Ruido que se cuela en el resumen de código.
_NOMBRES_RUIDO = {
    "open", "print", "len", "str", "int", "float", "list", "dict", "set",
    "tuple", "range", "self", "if", "for", "while", "return", "super",
    "log", "logging", "os", "sys", "re", "json", "time", "type", "input",
    "encoding", "utf-8", "coding", "get", "join", "append", "update",
}


def _codigo(texto: str, ext: str) -> list[str]:
    """De un fichero de código: funciones, clases y apartados de verdad."""
    out: list[str] = []
    patrones = (
        (r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", "definición"),
        (r"^\s*class\s+([A-Za-z_]\w*)", "clase"),
        (r"^\s*(?:export\s+)?(?:function|const|let|var|class)\s+([A-Za-z_]\w*)",
         "definición"),
        (r"^\s*func\s+([A-Za-z_]\w*)", "función"),
        (r"^\s*#{1,4}\s+(.+\S)", "apartado"),
        (r"^\s*#\s*([A-Za-z_]\w+)\s*$", "sección"),
    )
    for linea in texto.split("\n"):
        # Comentarios, separadores y líneas de adorno: no son el resumen.
        s = linea.strip()
        if not s or set(s) <= set("=-*+#/_ "):
            continue
        if s.startswith(("#!", "//", "*", "/*", "#;", "#:")):
            continue
        if s.startswith("#") and not s.startswith("# "):
            continue        # comentario de código, no un apartado de Markdown
        for pat, tipo in patrones:
            m = re.match(pat, linea)
            if m and m.group(1):
                nombre = m.group(1).strip().strip(":.")
                if (nombre and len(nombre) > 1
                        and nombre.lower() not in _NOMBRES_RUIDO):
                    out.append(f"{tipo}: {nombre[:60]}")
                break
        if len(out) >= 14:
            break
    return out


def _json_de(texto: str) -> list[str]:
    try:
        d = json.loads(texto)
    except Exception:
        return []
    if isinstance(d, dict):
        return [f"clave: {k}" for k in list(d)[:14]]
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return [f"elemento con campos: {', '.join(list(d[0])[:6])}"]
    return []


def _caracteristicas(texto: str) -> dict:
    return {
        "caracteres": len(texto),
        "palabras": len(texto.split()),
        "lineas": texto.count("\n") + 1 if texto else 0,
    }


def _truncado(path: str) -> bool:
    c = _cargar_cache()
    return bool((c.get(_clave(path)) or {}).get("truncado"))


def analizar(path: str) -> dict:
    """Todo lo que se puede decir del archivo sin preguntar a nadie."""
    path = os.path.expanduser(os.path.expandvars((path or "").strip().strip('"')))
    if not path or not os.path.exists(path):
        return {"ok": False, "razon": "Ese archivo no existe, señor."}
    if os.path.isdir(path):
        return {"ok": False, "razon": "Eso es una carpeta, no un archivo, señor."}
    ext = os.path.splitext(path)[1].lower()
    try:
        tam = os.path.getsize(path)
        mod = time.strftime("%d/%m/%Y %H:%M",
                            time.localtime(os.path.getmtime(path)))
    except Exception:
        tam, mod = 0, "?"
    lenguaje = _LENGUAJE.get(ext, "")
    if not ext:
        tipo = "texto"
    elif lenguaje:
        tipo = lenguaje          # "PY en Python" sonaba a robot
    else:
        tipo = BINARIOS_ESPECIALES.get(ext, ext.lstrip(".").upper() or "desconocido")
    texto = texto_de(path)
    clase = "texto" if ext in TEXTOS or not ext else (
        "documento" if texto else ("imagen" if ext in IMAGENES else "binario"))
    info = {
        "ok": True, "ruta": os.path.abspath(path),
        "nombre": os.path.basename(path), "extension": ext,
        "tipo": tipo, "lenguaje": lenguaje,
        "tamano": _human(tam), "modificado": mod,
    }
    if clase == "imagen":
        info["nota"] = "Es una imagen: se la enseño a la vista."
        return info
    if not texto:
        info["nota"] = (f"Es un archivo {tipo} y no sé leer su contenido. "
                        f"Puedo abrirlo, señor.")
        return info
    if ext == ".json":
        c = _json_de(texto)
        if c:
            info["resumen"] = c
            info["nota"] = f"Datos en JSON, con estas claves: {', '.join(c[:6])}."
            return info
    info.update(_caracteristicas(texto))
    if _truncado(path):
        info["aviso"] = ("Es un archivo grande: le he leído el principio, "
                         "no las 60.000 primeras letras. Si quiere un trozo "
                         "concreto, dígamelo.")
    if clase == "texto" and ext not in (".md", ".txt", ".log", ".csv", ".tsv"):
        info["resumen"] = _codigo(texto, ext)
    else:
        info["resumen"] = _partes_interesantes(texto)
        if not info["resumen"]:
            # _partes_interesantes solo acepta trozos de entre 60 y 700
            # caracteres, porque esta pensada para documentos largos. Un .txt
            # corto (una nota, un config, un log de tres lineas) no califica
            # en ninguno de los dos extremos y se quedaba sin resumen: el
            # senor preguntaba por su nota y le decian "tiene 1 palabra".
            # Cuando no hay nada mejor, se devuelve el principio tal cual.
            cab = texto.strip()
            if cab:
                info["resumen"] = ([cab[:1500]] if len(cab) <= 1500
                                   else [cab[:1500] + "…"])
                info["nota"] = ("Es un archivo corto: le he leído entero."
                                if len(cab) <= 1500 else
                                "Es largo: le he leído el principio.")
    return info


def respuesta_rapida(path: str) -> str:
    """La respuesta inmediata a '¿qué es esto?', sin modelo."""
    info = analizar(path)
    if not info.get("ok"):
        return info.get("razon", "No pude leerlo, señor.")
    p = [f"Es «{info['nombre']}», señor: {info['tipo']}, "
         f"{info['tamano']}, modificado el {info['modificado']}."]
    if info.get("nota"):
        p.append(info["nota"])
    elif "palabras" in info:
        p.append(f"Tiene {info['palabras']} palabras en {info['lineas']} líneas.")
    if info.get("aviso"):
        p.append(info["aviso"])
    resumen = info.get("resumen") or []
    if resumen:
        p.append("De qué va, por encima:")
        for r in resumen[:5]:
            p.append(f"  · {r}")
    return "\n".join(p)
