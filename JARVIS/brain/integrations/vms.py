# -*- coding: utf-8 -*-
"""Las máquinas virtuales, sin本公司 de por medio.

El señor no escribe «userver 26»: habla, y el micrófono devuelve «su punto
26», o «usuario 26», o «el server». Aquí se arregla eso solo:

  - La lista se lee de VirtualBox una vez y se guarda. Preguntar cuáles
    tiene no cuesta 800 ms, cuesta cero.
  - Se busca por lo que ha dicho, con好几 reglas: nombre exacto, apodo, el
    número («26» solo aparece en una máquina, esa es), parecido de letras y
    distancia de escritura.
  - Encender o apagar se lanza y se responde al instante: el señor no espera
    a que arranque Debian para oír que le he entendido.

Y siempre dice qué ha entendido: «¿«userver 26»? Vale, señor» o «¿Quiere
decir «srvLucas»?». Nunca adivina en silencio.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata

log = logging.getLogger("integrations.vms")

_cache: list[dict] = []
_cache_t = 0.0
_lock = threading.Lock()
_LEER = threading.Lock()

# Cuánto se considera reciente la lista. El estado cambia, pero no cada
# segundo, y preguntar cuántas máquinas tiene no puede costar medio segundo.
_VIGENCIA = 45.0

# Números escritos, por si el señor los dice con letras.
_NUMEROS = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
}

# Cómo se oye cada máquina cuando el micrófono se equivoca. La clave es lo
# que hay QUE BUSCAR en el nombre real de la máquina; el valor son trozos.
_APODOS = {
    "userver": ("userver",), "usuario": ("userver",), "usrver": ("userver",),
    "userber": ("userver",), "uservar": ("userver",), "userve": ("userver",),
    "server": ("userver", "srvlucas"), "servidor": ("userver", "srvlucas"),
    "srv": ("srvlucas",), "servrlucas": ("srvlucas",), "lucas": ("srvlucas",),
    "mint": ("clientemint",), "cliente": ("clientemint",),
    "minto": ("clientemint",), "mints": ("clientemint",),
    "win": ("windows",), "windows": ("windows",), "ventanas": ("windows",),
    "ventana": ("windows",), "wins": ("windows",),
    "debian": ("debian",), "debians": ("debian",), "debiane": ("debian",),
    "ubuntu": ("ubuntu",), "userv": ("userver",),
}

# Para que se entienda de un vistazo de qué va cada una.
_ROLES = (
    ("userver", "servidor con Ubuntu"),
    ("srvlucas", "su servidor"),
    ("ubuntu server", "servidor Ubuntu"),
    ("ubuntu", "equipo Ubuntu"),
    ("debian", "equipo Debian, la práctica"),
    ("clientemint", "cliente Linux Mint"),
    ("windows server", "Windows Server"),
    ("windows 11", "Windows 11"),
    ("ubuntu clonar", "copia de Ubuntu"),
)


def _txt(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _clave(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _txt(texto))


# --------------------------------------------------------------------------- #
# Lectura de la lista
# --------------------------------------------------------------------------- #
def _leer() -> list[dict]:
    """Lee nombre y estado de todas las máquinas, en dos llamadas."""
    global _cache, _cache_t
    if not _LEER.acquire(blocking=False):
        return _cache
    try:
        from tools import virtualbox_tools as vt
        # 'list vms' trae TODAS (encendidas y apagadas). 'list runningvms'
        # solo las encendidas: sirve para marcar el estado, NO para añadir
        # nombres, que era como aparecían duplicadas.
        base = []
        try:
            for d in (vt._parse_list_vms(vt._run("list", "vms", timeout=20) or "")
                      or []):
                n = d.get("name") if isinstance(d, dict) else d
                if n:
                    base.append({"nombre": n,
                                 "uuid": (d.get("uuid", "")
                                          if isinstance(d, dict) else ""),
                                 "estado": "apagada"})
        except Exception as e:
            log.debug("list vms: %s", e)
        encendidas = set()
        try:
            txt = vt._run("list", "runningvms", timeout=15) or ""
            for m in re.finditer(r'"([^"]+)"\s*\{([0-9a-fA-F-]+)\}', txt):
                encendidas.add(m.group(2).lower())
        except Exception as e:
            log.debug("runningvms: %s", e)
        if not base:
            return _cache
        vistos = set()
        lista = []
        for m in base:
            k = (m["uuid"] or m["nombre"]).lower()
            if k in vistos:
                continue
            vistos.add(k)
            if m["uuid"] and m["uuid"].lower() in encendidas:
                m["estado"] = "encendida"
            elif m["nombre"] in encendidas:
                m["estado"] = "encendida"
            lista.append(m)
        for m in lista:
            m["clave"] = _clave(m["nombre"])
            m["rol"] = _rol(m["nombre"])
            nums = re.findall(r"\d+", _txt(m["nombre"]))
            m["num"] = nums[0] if nums else ""
        lista.sort(key=lambda x: x["nombre"].lower())
        _cache = lista
        _cache_t = time.time()
        return lista
    finally:
        _LEER.release()


def _rol(nombre: str) -> str:
    n = _txt(nombre)
    for clave, texto in _ROLES:
        if clave in n:
            return texto
    return "equipo"


def listar(forzar: bool = False) -> list[dict]:
    if not forzar and _cache and (time.time() - _cache_t) < _VIGENCIA:
        return _cache
    if _cache:
        _refrescar()          # lista vieja: se sirve ya y se actualiza detrás
        return _cache
    return _leer()


def _refrescar() -> None:
    global _cache_t
    with _lock:
        if time.time() - _cache_t < _VIGENCIA:
            return
        _cache_t = time.time()

    def _t():
        try:
            _leer()
        except Exception as e:
            log.debug("refrescar vms: %s", e)
    threading.Thread(target=_t, name="vms-refresh", daemon=True).start()


def refrescar() -> None:
    threading.Thread(target=lambda: _leer(), name="vms-force",
                     daemon=True).start()


def al_arrancar() -> None:
    """Deja la lista lista antes de que haga falta: pregunta instantánea."""
    def _t():
        try:
            _leer()
        except Exception as e:
            log.debug("vms arranque: %s", e)
    threading.Thread(target=_t, name="vms-boot", daemon=True).start()


# --------------------------------------------------------------------------- #
# Buscar una máquina por lo que ha dicho
# --------------------------------------------------------------------------- #
def _distancia(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 9
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(actual[-1] + 1, anterior[j] + 1,
                              anterior[j - 1] + (ca != cb)))
        anterior = actual
    return anterior[-1]


def buscar(texto: str) -> dict:
    """Entender qué máquina ha dicho. Nunca adivina en silencio.

    Devuelve {nombre, como} si hay una sola que encaja, o {candidatos} si
    hay varias, para preguntarle cuál. Vacío si no se entiende nada.
    """
    maquinas = listar()
    vacio = {"nombre": "", "como": "", "candidatos": []}
    if not maquinas:
        return vacio
    q = _txt(texto or "").strip()
    if not q:
        return vacio
    cq = _clave(q)
    palabras = re.findall(r"[a-z0-9]+", q)

    def _cands(patrones, como):
        c = [m["nombre"] for m in maquinas
             if any(p in _txt(m["nombre"]) for p in patrones)]
        if len(c) == 1:
            return {"nombre": c[0], "como": como, "candidatos": []}
        if len(c) > 1:
            return {"nombre": "", "como": como, "candidatos": c}
        return None

    # 1) El nombre tal cual, con o sin espacios y sin tildes.
    for m in maquinas:
        if m["clave"] == cq or _txt(m["nombre"]) == q:
            return {"nombre": m["nombre"], "como": "nombre exacto",
                    "candidatos": []}

    # 2) Apodo: 'usuario 26', 'el server', 'ventanas'.
    for palabra in palabras:
        if palabra in _APODOS:
            r = _cands(_APODOS[palabra], "por el apodo «%s»" % palabra)
            if not r:
                continue
            if r["nombre"]:
                return r
            # Varias encajan: a ver si el número que dijo las distingue.
            r2 = _con_numero(r["candidatos"], palabras)
            if r2:
                return r2
            return r

    # 3) El número: 'su punto 26'. Si solo una máquina lo tiene, es esa.
    r = _con_numero([m["nombre"] for m in maquinas], palabras)
    if r:
        return r

    # 4) Empieza igual, con buena cobertura ('mint' ya pasó; 'clientemint').
    mejor, puntos = "", 0.0
    for m in maquinas:
        c = m["clave"]
        if len(cq) >= 3 and (c.startswith(cq) or cq.startswith(c)):
            cobertura = min(len(c), len(cq)) / max(len(c), len(cq))
            if cobertura >= 0.4 and cobertura > puntos:
                mejor, puntos = m["nombre"], cobertura
    if mejor:
        return {"nombre": mejor, "como": "empieza igual", "candidatos": []}

    # 5) Palabras sueltas: dice casi todas las del nombre.
    utiles = [p for p in palabras if p not in
              ("servidor", "server", "maquina", "virtual", "equipo", "el", "la",
               "los", "las", "de", "del", "por", "para", "con")]
    for m in maquinas:
        nm = _txt(m["nombre"])
        if utiles and all(p in nm for p in utiles):
            return {"nombre": m["nombre"], "como": "por sus palabras",
                    "candidatos": []}

    # 6) Fallos de tecleo o de oído: 'userser' -> 'userver 26'.
    if len(cq) >= 4:
        mejor, d = "", 99
        for m in maquinas:
            for parte in [m["clave"]] + [_clave(p) for p in m["nombre"].split()]:
                if len(parte) < 3:
                    continue
                dd = _distancia(cq, parte)
                if dd < d:
                    mejor, d = m["nombre"], dd
        if mejor and d <= 2:
            return {"nombre": mejor, "como": "por parecido (%d letras)" % d,
                    "candidatos": []}
    return vacio


def _con_numero(candidatos: list, palabras: list) -> dict | None:
    """Si dijo un número y solo una máquina lo tiene, esa es."""
    numeros = re.findall(r"\d+", " ".join(palabras))
    for p in palabras:
        if p in _NUMEROS and _NUMEROS[p]:
            numeros.append(str(_NUMEROS[p]))
    if not numeros:
        return None
    con_n = [c for c in candidatos if any(n in _txt(c) for n in numeros)]
    if len(con_n) == 1:
        return {"nombre": con_n[0], "como": "por el número %s" % numeros[0],
                "candidatos": []}
    if len(con_n) > 1:
        return {"nombre": "", "como": "", "candidatos": con_n}
    return None


# --------------------------------------------------------------------------- #
# Acciones: se lanzan y se responde al instante
# --------------------------------------------------------------------------- #
def _en_segundo_plano(fn, *a, **k):
    threading.Thread(target=fn, args=a, kwargs=k, daemon=True,
                     name="vms-accion").start()


def _resolver_o_preguntar(pedido: str) -> tuple[str, str]:
    """(nombre, frase para preguntar). Si hay duda, pregunta."""
    r = buscar(pedido)
    if r["nombre"]:
        return r["nombre"], ""
    if r["candidatos"]:
        c = r["candidatos"][:4]
        return "", ("¿Cuál de estas, señor: " + ", ".join(c) + "?")
    return "", ""


def encender(nombre: str) -> str:
    real, pregunta = _resolver_o_preguntar(nombre)
    if pregunta:
        return pregunta
    if not real:
        return ""
    if _estado_de(real) == "encendida":
        return f"«{real}» ya está encendida, señor."
    _en_segundo_plano(_arrancar, real)
    return f"Encendiendo «{real}», señor. En un par de minutos la tiene."


def _arrancar(real: str) -> None:
    try:
        from tools.virtualbox_tools import vbox_start_handler
        vbox_start_handler({"name": real})
    except Exception as e:
        log.warning("encender %s: %s", real, e)
    refrescar()


def _estado_de(nombre: str) -> str:
    for m in listar():
        if m["nombre"] == nombre:
            return m["estado"]
    return "desconocida"


def apagar(nombre: str) -> str:
    real, pregunta = _resolver_o_preguntar(nombre)
    if pregunta:
        return pregunta
    if not real:
        return ""
    if _estado_de(real) == "apagada":
        return f"«{real}» ya estaba apagada, señor."
    _en_segundo_plano(_apagar_real, real)
    return f"Apagando «{real}», señor."


def _apagar_real(real: str) -> None:
    try:
        from tools.virtualbox_tools import vbox_stop_handler
        vbox_stop_handler({"name": real})
    except Exception as e:
        log.warning("apagar %s: %s", real, e)
    refrescar()


def pausar(nombre: str) -> str:
    real, pregunta = _resolver_o_preguntar(nombre)
    if pregunta:
        return pregunta
    if not real:
        return ""
    _en_segundo_plano(_pausar_real, real)
    return f"Pauso «{real}», señor."


def _pausar_real(real: str) -> None:
    try:
        from tools import virtualbox_tools as vt
        vt._run("controlvm", real, "pause", timeout=40)
    except Exception as e:
        log.warning("pausar %s: %s", real, e)
    refrescar()


def estado(nombre: str) -> str:
    real, pregunta = _resolver_o_preguntar(nombre)
    if pregunta:
        return pregunta
    if not real:
        return ""
    est, rol = "desconocida", ""
    for x in listar(forzar=True):
        if x["nombre"] == real:
            est, rol = x["estado"], x["rol"]
            break
    if est == "encendida":
        return f"«{real}» está encendida, señor ({rol})."
    return f"«{real}» está apagada, señor ({rol})."


def resumen(preguntar: bool = False) -> str:
    """La lista de las máquinas, para leerla de un vistazo.

    No fuerza a releer: si la lista tiene menos de 45 segundos sirve la que hay
    y actualiza por detrás. Preguntar cuáles son cuesta cero, no 800 ms.
    """
    maquinas = listar()
    if not maquinas:
        return ("No veo ninguna máquina en VirtualBox, señor. ¿Quería que "
                "mire si está instalado?")
    encendidas = [m for m in maquinas if m["estado"] == "encendida"]
    partes = [f"Tiene {len(maquinas)} máquinas en VirtualBox, señor."]
    if encendidas:
        partes.append("Encendidas ahora mismo: "
                      + ", ".join(m["nombre"] for m in encendidas) + ".")
    lineas = []
    for m in maquinas:
        marca = "encendida" if m["estado"] == "encendida" else "apagada"
        lineas.append(f"  · {m['nombre']} — {m['rol']} ({marca})")
    partes.append("Para abrir una, solo diga su nombre: «abre userver 26».")
    return "\n".join(partes + lineas)
