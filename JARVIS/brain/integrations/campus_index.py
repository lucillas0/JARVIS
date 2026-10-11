# -*- coding: utf-8 -*-
"""Índice del campus: TODO lo del Moodle del señor,_barato y al instante.

La página de "Mis cursos" carga los cursos por JavaScript, así que no valía
con leer el HTML. La lista sale del servicio interno del propio Moodle
(`/lib/ajax/service.php`), que devuelve los cursos matriculados de verdad.

Aquí no se responde preguntando al modelo: se consulta un índice en disco que
se construye una vez y se refresca solo cuando el Moodle cambia de hora. Por
eso JARVIS responde en milisegundos y no se para a pensar.

Qué indexa de cada curso:
  - secciones y actividades (tareas, foros, archivos, cuestionarios)
  - notas y estados de entrega
  - avisos
  - entregas próximas
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("integrations.campus_index")

# El Moodle no cambia de contenido cada segundo. Si el índice es younger que
# esto, se sirve tal cual y no se toca la red.
_CACHE_HORAS = 3
_LOCK = threading.Lock()
_build_lock = threading.Lock()
_idx: dict | None = None
_idx_mtime = 0.0

# El menú y el pie están en todas las páginas y no aportan nada.
_RUIDO = re.compile(
    r"^(tama[ñn]o de fuente|color del sitio|archivos privados|curso|participantes|"
    r"actividades|competencias|expandir todo|colapsar todo|abrir [íi]ndice del curso|"
    r"ver|seleccionar (sección|actividad)|ir a secci[óo]n|usuario|acciones|"
    r"contactar con el soporte del sitio|usted se ha identificado como|"
    r"resumen de retenci[óo]n de datos|desarrollado por|moodle|idioma|"
    r"cambiar|editar|eliminar|descargar|mover|ajustes|ayuda|buscar|"
    r"informe general|informe del usuario|registro de notas|"
    r"todo|no hay|sin|atención|importante|descripción|calendario|"
    r"índice|volver|continuar|atrás|siguiente|anterior)$", re.I)


def _ruta() -> str:
    try:
        from config import DATA_DIR
        base = str(DATA_DIR)
    except Exception:
        base = os.path.expandvars(r"%APPDATA%\JARVIS\brain-data")
    return os.path.join(base, "campus_indice.json")


# --------------------------------------------------------------------------- #
# Lectura del Moodle
# --------------------------------------------------------------------------- #
def _ajax(metodo: str, args: dict) -> dict:
    """Llama al servicio interno del Moodle y devuelve su 'data'."""
    from integrations import campus
    s = campus._client()
    if s is None:
        return {}
    if not campus.login():
        return {}
    html = campus.get_page("/my/courses.php", timeout=20)
    m = re.search(r'"sesskey"\s*:\s*"([^"]+)"', html)
    sk = m.group(1) if m else ""
    if not sk:
        return {}
    cuerpo = json.dumps([{"index": 0, "methodname": metodo, "args": args}])
    try:
        r = s.post(campus.base_url() + "/lib/ajax/service.php?sesskey=" + sk,
                   data=cuerpo, timeout=25,
                   headers={"Content-Type": "application/json"})
        d = r.json()
    except Exception as e:
        log.debug("campus ajax %s: %s", metodo, e)
        return {}
    if not isinstance(d, list) or not d:
        return {}
    if d[0].get("error"):
        exc = d[0].get("exception") or {}
        log.debug("campus ajax %s: %s", metodo, exc.get("message", "error"))
        return {}
    return (d[0].get("data") or {})


def cursos() -> list[dict]:
    """Los cursos matriculados: [{id, fullname, shortname, progress}]."""
    d = _ajax("core_course_get_enrolled_courses_by_timeline_classification",
              {"classification": "all", "limit": 100, "offset": 0,
               "sort": "fullname"})
    out = []
    for c in d.get("courses", []) or []:
        cid = c.get("id")
        if not cid:
            continue
        out.append({
            "id": int(cid),
            "nombre": str(c.get("fullname") or c.get("shortname") or cid),
            "corto": str(c.get("shortname") or ""),
            "progreso": c.get("progress"),
            "url": f"/course/view.php?id={cid}",
        })
    return out


def _texto(html: str, desde_ruta: str | None = None) -> list[str]:
    """Texto útil de una página del Moodle, sin menú ni migas."""
    sin = re.sub(r"<script.*?</script>", " ", html or "", flags=re.S | re.I)
    sin = re.sub(r"<style.*?</style>", " ", sin, flags=re.S | re.I)
    if desde_ruta:
        m = re.search(desde_ruta, sin, re.S | re.I)
        if m:
            sin = sin[m.start():]
    txt = re.sub(r"<[^>]+>", "\n", sin)
    out: list[str] = []
    vistos: set = set()
    for x in txt.split("\n"):
        x = re.sub(r"&[a-zA-Z#0-9]+;", " ", x).strip()
        x = re.sub(r"\s+", " ", x)
        if len(x) < 4 or len(x) > 300 or _RUIDO.match(x) or _RUIDO.search(x):
            continue
        k = x.lower()
        if k in vistos:
            continue
        vistos.add(k)
        out.append(x)
    return out


def _secciones_curso(html: str) -> list[dict]:
    """Secciones y actividades de la portada del curso."""
    out = []
    for m in re.finditer(r'name="(?:collapse|expand)-?all"', html):
        break
    # Moodle 4: los enlaces de sección llevan "course/view.php?id=..#section-N"
    for m in re.finditer(r'<a[^>]+href="[^"]*#section-(\d+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        nombre = re.sub(r"<[^>]+>", " ", m.group(2))
        nombre = re.sub(r"\s+", " ", nombre).strip()
        if nombre and len(nombre) > 2:
            out.append({"tipo": "seccion", "n": int(m.group(1)),
                        "nombre": nombre[:120]})
    for m in re.finditer(r'<a[^>]+href="([^"]*mod/[a-z]+/[^"]+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        href, txt = m.group(1), m.group(2)
        nombre = re.sub(r"<[^>]+>", " ", txt)
        nombre = re.sub(r"\s+", " ", nombre).strip()
        if not nombre or len(nombre) < 3:
            continue
        tipo = re.search(r"/mod/([a-z]+)/", href)
        out.append({"tipo": tipo.group(1) if tipo else "actividad",
                    "nombre": nombre[:140], "url": href})
    return out


def _notas_curso(html: str) -> list[dict]:
    """Notas del curso desde la tabla del libro de calificaciones."""
    out = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        fila = m.group(1)
        celdas = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                  for c in re.findall(r"<td[^>]*>(.*?)</td>", fila, re.S)]
        celdas = [c for c in celdas if c]
        if len(celdas) < 2:
            continue
        nombre = celdas[0]
        if len(nombre) < 3 or _RUIDO.search(nombre):
            continue
        nota = ""
        for c in celdas[1:]:
            if re.fullmatch(r"[-+]?\d+([.,]\d+)?%?|sin nota|no[-\s]?presentado", c, re.I):
                nota = c
                break
        out.append({"item": nombre[:120], "nota": nota or "sin nota"})
    return out


def _tareas_curso(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a[^>]+href="([^"]*mod/assign/view\.php\?id=\d+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        nombre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        if nombre:
            out.append({"nombre": nombre[:140], "url": m.group(1)})
    for m in re.finditer(r'<a[^>]+href="([^"]*mod/(?:quiz|forum|page|resource|url|lesson|assign)/view\.php\?id=\d+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        nombre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        if nombre and not any(t["nombre"] == nombre for t in out):
            tipo = re.search(r"/mod/([a-z]+)/", m.group(1))
            out.append({"nombre": nombre[:140], "url": m.group(1),
                        "tipo": tipo.group(1) if tipo else "actividad"})
    return out


def _avisos(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a[^>]+href="([^"]*mod/forum/view\.php\?id=\d+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        nombre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        if nombre and len(nombre) > 3:
            out.append({"nombre": nombre[:140], "url": m.group(1)})
    return out[:20]


# --------------------------------------------------------------------------- #
# Construcción del índice
# --------------------------------------------------------------------------- #
SECCIONES = (
    ("contenido", "/course/view.php?id={id}"),
    ("tareas", "/mod/assign/index.php?id={id}"),
    ("notas", "/grade/report/index.php?id={id}"),
    ("avisos", "/mod/forum/index.php?id={id}"),
)


def construir(forzar: bool = False) -> dict:
    """Construye (o reutiliza) el índice. Devuelve el diccionario completo."""
    global _idx, _idx_mtime
    ruta = _ruta()
    if not forzar and _idx is not None and (time.time() - _idx_mtime) < 300:
        return _idx
    if not forzar and os.path.exists(ruta):
        try:
            if (time.time() - os.path.getmtime(ruta)) < _CACHE_HORAS * 3600:
                with open(ruta, "r", encoding="utf-8") as f:
                    _idx = json.load(f)
                _idx_mtime = time.time()
                return _idx
        except Exception:
            pass
    if not _build_lock.acquire(blocking=False):
        return _idx or {}
    try:
        from integrations import campus
        indice = {
            "generado": time.time(),
            "generado_txt": time.strftime("%d/%m/%Y %H:%M"),
            "url_mis_cursos": campus.base_url() + "/my/courses.php",
            "cursos": {},
        }
        for c in cursos():
            cid = c["id"]
            log.info("campus: indexando %s", c["nombre"])
            ficha = dict(c)
            ficha["secciones"] = []
            ficha["tareas"] = []
            ficha["notas"] = []
            ficha["avisos"] = []
            for etiqueta, patron in SECCIONES:
                try:
                    html = campus.get_page(patron.format(id=cid), timeout=18)
                except Exception as e:
                    log.debug("campus %s %s: %s", etiqueta, cid, e)
                    continue
                if etiqueta == "contenido":
                    ficha["secciones"] = _secciones_curso(html)
                elif etiqueta == "tareas":
                    ficha["tareas"] = _tareas_curso(html)
                elif etiqueta == "notas":
                    ficha["notas"] = _notas_curso(html)
                elif etiqueta == "avisos":
                    ficha["avisos"] = _avisos(html)
            indice["cursos"][str(cid)] = ficha
        indice["entregas"] = _entregas(indice)
        _idx = indice
        _idx_mtime = time.time()
        try:
            tmp = ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(indice, f, ensure_ascii=False, indent=1)
            os.replace(tmp, ruta)
            log.info("campus: índice listo con %d cursos",
                     len(indice["cursos"]))
        except Exception as e:
            log.debug("campus: no pude guardar el índice: %s", e)
        return indice
    finally:
        _build_lock.release()


def _entregas(indice: dict) -> list[dict]:
    """Tareas sin entregar, por orden, para poder decir qué urge."""
    out = []
    for ficha in indice.get("cursos", {}).values():
        for t in ficha.get("tareas", []):
            out.append({"curso": ficha.get("nombre", ""),
                        "tarea": t.get("nombre", ""),
                        "url": t.get("url", "")})
    return out[:40]


def refrescar() -> None:
    """Rehace el índice en segundo plano, sin molestar a nadie."""
    def _t():
        try:
            construir(forzar=True)
        except Exception as e:
            log.debug("campus refresh: %s", e)
    threading.Thread(target=_t, name="campus-refresh", daemon=True).start()


def indexar_al_arrancar() -> None:
    """Arranca el indexado en segundo plano, o al menos carga la caché."""
    def _t():
        try:
            if not _idx:
                construir()
            refrescar()
        except Exception as e:
            log.debug("campus index start: %s", e)
    threading.Thread(target=_t, name="campus-index", daemon=True).start()


# --------------------------------------------------------------------------- #
# Consultas (rápidas, sin modelo)
# --------------------------------------------------------------------------- #
def resumen() -> str:
    """Una línea por curso con lo esencial, para responder al instante."""
    idx = construir()
    cs = idx.get("cursos", {})
    if not cs:
        return ("No veo ningún curso en su Moodle, señor. "
                "Puede que aún no esté matriculado en ninguno.")
    lineas = []
    for f in cs.values():
        extra = []
        if f.get("tareas"):
            extra.append(f"{len(f['tareas'])} actividades")
        if f.get("notas"):
            extra.append(f"{len(f['notas'])} notas")
        if f.get("secciones"):
            extra.append(f"{len(f['secciones'])} secciones")
        sufijo = (" (" + ", ".join(extra) + ")") if extra else ""
        lineas.append(f"  · {f.get('nombre', '')}{sufijo}")
    return (f"Usted tiene {len(cs)} cursos en el Moodle, señor "
            f"(índice del {idx.get('generado_txt', '?')}):\n" + "\n".join(lineas))


def _tokens(texto: str) -> set:
    """Palabras sin tildes, para comparar nombres.

    Tienes que poder escribir "informatica" y que encuentre
    "Seguridade Informática", y "redes" que encuentre "Servizos en Rede".
    De ahí las dos formas de cada palabra: con y sin la s final.
    Se aceptan dígitos porque las unidades se llaman "UD1", "UD2"...
    """
    import unicodedata
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("\u00e7", "c")
    out: set = set()
    for p in re.findall(r"[a-z0-9][a-z0-9]{1,}", t):
        out.add(p)
        if len(p) > 3 and p.endswith("s"):
            out.add(p[:-1])        # "redes" -> "rede"
    return out


def mejor_curso(texto: str) -> str:
    """El curso del índice que mejor encaja con lo que dijo el señor.

    Los cursos están en gallego y el señor escribe en castellano, así que
    'redes' tiene que encontrar 'Servizos en Rede'. Se busca por palabras
    compartidas, no por coincidencia exacta.
    """
    idx = construir()
    q = _tokens(texto)
    if not q:
        return ""
    mejor, puntos = "", 0
    for ficha in idx.get("cursos", {}).values():
        nombre = ficha.get("nombre", "")
        nt = _tokens(nombre)
        comunes = len(q & nt)
        if not comunes:
            continue
        # Se bonifica si alguna palabra del señor aparece tal cual en el nombre.
        extra = 1 if any(p in _tokens(nombre) for p in q) else 0
        score = comunes * 2 + extra
        if score > puntos:
            mejor, puntos = nombre, score
    return mejor if puntos >= 3 else ""


def buscar(texto: str, limite: int = 12) -> list[dict]:
    """Busca 'texto' en cursos, secciones, actividades, notas y avisos.

    Si es una frase, se queda con la palabra más larga: buscar "tarea de lynis"
    tiene que encontrar "Auditoría Seg. Informática con Lynis", no rendirse.
    """
    idx = construir()
    q = (texto or "").strip().lower()
    if not q:
        return []
    palabras = sorted(_tokens(q), key=len, reverse=True)
    # Se prueba con la palabra más larga primero, que es la más específica.
    if len(palabras) > 1:
        r = _buscar_palabras(idx, palabras[:1], limite)
        if r:
            return r
    return _buscar_palabras(idx, palabras, limite)


def _buscar_palabras(idx: dict, palabras: list, limite: int) -> list[dict]:
    qt = set(palabras)
    if not qt:
        return []

    def _casa(texto: str) -> bool:
        t = _tokens(texto)
        return bool(t) and bool(t & qt)

    out = []
    for ficha in idx.get("cursos", {}).values():
        cn = ficha.get("nombre", "")
        if _casa(cn):
            out.append({"tipo": "curso", "curso": cn,
                        "texto": cn, "url": ficha.get("url", "")})
        for clave, etiqueta in (("secciones", "sección"), ("tareas", "actividad"),
                                ("avisos", "aviso"), ("notas", "nota")):
            for it in ficha.get(clave, []):
                nombre = it.get("nombre") or it.get("item") or ""
                if not nombre or not _casa(nombre):
                    continue
                if any(o["texto"] == nombre and o["curso"] == cn for o in out):
                    continue
                out.append({"tipo": etiqueta, "curso": cn,
                            "texto": nombre[:160],
                            "url": it.get("url", "") or ficha.get("url", ""),
                            "nota": it.get("nota", "")})
    return out[:limite]


def detalle(curso: str) -> str:
    """Todo lo indexado de un curso, para leerlo de un vistazo."""
    idx = construir()
    q = (curso or "").strip().lower()
    elegido = None
    for ficha in idx.get("cursos", {}).values():
        nombre = ficha.get("nombre", "")
        if (q and (q in nombre.lower() or q == str(ficha.get("id")))):
            elegido = ficha
            break
    if elegido is None:
        nombre = mejor_curso(curso)
        if not nombre:
            return f"No encuentro un curso llamado «{curso}», señor."
        for ficha in idx.get("cursos", {}).values():
            if ficha.get("nombre") == nombre:
                elegido = ficha
                break
    if elegido is None:
        return f"No encuentro un curso llamado «{curso}», señor."
    ficha = elegido
    partes = [f"El curso «{ficha.get('nombre', '')}», señor "
              f"({idx.get('url_mis_cursos', '')} → curso {ficha.get('id')})."]
    if ficha.get("secciones"):
        partes.append("  Secciones y actividades: "
                      + ", ".join(s.get("nombre", "") for s in ficha["secciones"][:14]))
    if ficha.get("tareas"):
        partes.append("  Actividades: "
                      + ", ".join(t.get("nombre", "") for t in ficha["tareas"][:14]))
    if ficha.get("notas"):
        partes.append("  Notas: "
                      + ", ".join(f"{n['item']} = {n['nota']}"
                                  for n in ficha["notas"][:14]))
    if ficha.get("avisos"):
        partes.append("  Avisos: "
                      + ", ".join(a.get("nombre", "") for a in ficha["avisos"][:8]))
    return "\n".join(partes)
