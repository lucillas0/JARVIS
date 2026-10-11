# -*- coding: utf-8 -*-
"""Atajos: nombres cortos que el señor dice y JARVIS abre al instante.

Sin esto, "abre userver26" tardaba diez segundos: ninguna ruta rápida conocía
las máquinas virtuales, así que la pregunta iba al modelo, y de paso el índice
de apps contestaba con el manual en PDF de VirtualBox (porque "userver26" empieza
por "user").

Aquí se resuelve de forma determinista, en milisegundos:
  - máquinas virtuales de VirtualBox (con el nombre tal cual lo llama)
  - páginas web por su nombre ("marca", "el pais") o por su dominio
  - páginas del propio campus
  - programas de siempre

Las máquinas se leen de VirtualBox cada vez, para no quedarse obsoleto si el
señor añade o renombra una.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata

_VM_CACHE: list[str] = []
_VM_T = 0.0
_vm_lock = threading.Lock()

# Sitios por nombre propio. El dominio tal cual ya funciona por otra vía; esto
# es para cuando el señor dice solo "abre marca" o "el país".
SITIOS = {
    "marca": "https://www.marca.com",
    "as": "https://as.com",
    "mundo deportivo": "https://www.mundodeportivo.com",
    "el pais": "https://elpais.com",
    "el mundo": "https://www.elmundo.es",
    "abc": "https://www.abc.es",
    "lavanguardia": "https://www.lavanguardia.com",
    "el CONFIDENTIAL": "https://www.elconfidencial.com",
    "el confidencial": "https://www.elconfidencial.com",
    "20 minutos": "https://www.20minutos.es",
    "eldiario": "https://www.eldiario.es",
    "publico": "https://www.publico.es",
    "expansion": "https://www.expansion.com",
    "eleconomista": "https://www.eleconomista.es",
    "lavozdegalicia": "https://www.lavozdegalicia.es",
    "la voz de galicia": "https://www.lavozdegalicia.es",
    "elgossip": "https://www.elgossip.com",
    "atresplayer": "https://www.atresplayer.com",
    "mitele": "https://www.mitele.es",
    "netflix": "https://www.netflix.com",
    "hbo max": "https://play.max.com",
    "prime video": "https://www.primevideo.com",
    "twitch": "https://www.twitch.tv",
    "tiktok": "https://www.tiktok.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "gmail": "https://mail.google.com",
    "drive": "https://drive.google.com",
    "wikipedia": "https://es.wikipedia.org",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "perplexity": "https://www.perplexity.ai",
    "gemini": "https://gemini.google.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "ave": "https://ave.junta.gal",
    "adif": "https://www.adif.es",
    "renfe": "https://www.renfe.com",
    "movistar": "https://www.movistar.es",
    "vodafone": "https://www.vodafone.es",
    "orange": "https://www.orange.es",
    "amazon": "https://www.amazon.es",
    "wallapop": "https://es.wallapop.com",
    "idealista": "https://www.idealista.com",
    "booking": "https://www.booking.com",
    "airbnb": "https://www.airbnb.es",
    "spotify web": "https://open.spotify.com",
    "canva": "https://www.canva.com",
    "figma": "https://www.figma.com/files",
    "notion": "https://www.notion.so",
    "trello": "https://trello.com",
    "goodreads": "https://www.goodreads.com",
    "letterboxd": "https://letterboxd.com",
    "strava": "https://www.strava.com",
    "duolingo": "https://www.duolingo.com",
    "khan academy": "https://es.khanacademy.org",
    "coursera": "https://www.coursera.org",
    "wolfram": "https://www.wolframalpha.com",
    "traductor": "https://translate.google.com",
    "translate": "https://translate.google.com",
    "mapas": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "yahoo": "https://finance.yahoo.com",
    "steam": "https://store.steampowered.com",
    "epic games": "https://store.epicgames.com",
    "moodle": "https://av.ciclosallerulloa.gal",
    "campus": "https://av.ciclosallerulloa.gal/my/courses.php",
    "aviesau": "https://av.ciclosallerulloa.gal/my/courses.php",
}

# Apodo -> como lo llama él de verdad.
_APODOS = {
    "userver26": "userver 26",
    "userver": "userver 26",
    "server26": "userver 26",
    "userver 2026": "userver 26",
    "win11": "windows 11",
    "windows11": "windows 11",
    "win 11": "windows 11",
    "windows": "windows 11",
    "srv": "srvLucas",
    "srvlucas": "srvLucas",
    "mint": "clientemint22",
    "clientemint": "clientemint22",
    "debian": "debian practica",
    "ubuntu server": "ubuntu server 24.04",
    "practica": "debian practica",
}

# La página del ciclo: sale del campus de verdad, no de una URL fija.
_CAMPUS_RE = re.compile(r"\b(pagina\s+(?:del\s+)?ciclo|ciclo|aviesau|campus|"
                        r"aula\s+virtual|moodle|mis\s+cursos)\b", re.I)


def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _clave(t: str) -> str:
    """'El País.' -> 'elpais'"""
    return re.sub(r"[^a-z0-9]", "", _sin_tildes(t))


def vms() -> list[str]:
    """Nombres reales de las máquinas, para no quedarse obsoleto.

    Nunca bloquea: si la lista es vieja pero existe, se sirve tal cual y la
    lectura nueva se hace en segundo plano. Consultar VirtualBox puede costar
    treinta segundos, y eso no debe retrasar una respuesta de medio segundo.
    """
    global _VM_T
    if _VM_CACHE and (time.time() - _VM_T) < 600:
        return _VM_CACHE
    if _VM_CACHE:
        # Lista vieja: vale para responder ya, y se actualiza por detrás.
        _refrescar_vms()
        return _VM_CACHE
    # Sin lista todavía: NO se lee aquí. Consultar VirtualBox puede tardar
    # treinta segundos y el señor solo ha dicho "abre marca". La lectura se
    # lanza por detrás y las máquinas ya están en la tabla de apodos.
    _refrescar_vms()
    return []


def _leer_vms() -> bool:
    nombres: list[str] = []
    try:
        from tools.virtualbox_tools import vbox_list_handler
        r = vbox_list_handler({})
        for v in (r.get("vms") or r.get("machines") or []):
            n = (v.get("name") or "").strip()
            if n:
                nombres.append(n)
    except Exception:
        pass
    if nombres:
        _VM_CACHE[:] = nombres
        return True
    return False


def _refrescar_vms() -> None:
    global _VM_T
    with _vm_lock:
        if _VM_T and (time.time() - _VM_T) < 600:
            return
        _VM_T = time.time()          # marca antes, para no lanzar dos a la vez

    def _t():
        global _VM_T
        if _leer_vms():
            _VM_T = time.time()
    threading.Thread(target=_t, name="vms-refresh", daemon=True).start()


def _encaja_vm(pedido: str) -> str:
    """'userver26' -> 'userver 26', sin preguntar a VirtualBox si se puede evitar.

    Primero la tabla de apodos, que ya trae el nombre bueno. Solo si no hay
    apodo se mira la lista real de máquinas, y esa lista puede tardar, así que
    se devuelve lo que haya sin esperar a nadie.
    """
    p = _clave(pedido)
    if not p:
        return ""
    # 1) Apodo: no hace falta ni mirar VirtualBox.
    for alias, real in _APODOS.items():
        if p == _clave(alias):
            return real
    # 2) Lo que tenemos ya en caché, sea vieja o no.
    for v in vms():
        if p == _clave(v):
            return v
    # 3) Prefijo con buena cobertura, para no abrir "windows 11" por "win".
    mejor, puntos = "", 0
    for v in vms():
        kv = _clave(v)
        if len(kv) >= 4 and (kv.startswith(p) or p.startswith(kv)):
            cobertura = min(len(kv), len(p)) / max(len(kv), len(p))
            if cobertura >= 0.6 and len(min(kv, p)) >= 4:
                score = int(cobertura * 100)
                if score > puntos:
                    mejor, puntos = v, score
    return mejor


def sitio(pedido: str) -> str:
    """URL de una web por su nombre, o '' si no la conozco."""
    p = _clave(pedido)
    if not p:
        return ""
    for nombre, url in SITIOS.items():
        if p == _clave(nombre):
            return url
    # coincidencia por prefijo generosa: "marc" -> marca
    cands = [(n, u) for n, u in SITIOS.items()
             if _clave(n).startswith(p) and len(p) >= 3]
    if len(cands) == 1:
        return cands[0][1]
    return ""


def campus_url() -> str:
    try:
        from integrations import campus_index as ix
        return ix.construir().get("url_mis_cursos", "") or \
            "https://av.ciclosallerulloa.gal/my/courses.php"
    except Exception:
        return "https://av.ciclosallerulloa.gal/my/courses.php"


def resolver(pedido: str) -> dict | None:
    """Qué es 'pedido'. None si no lo sabemos: entonces toca el modelo.

    Orden: página del ciclo -> web por nombre -> dominio -> máquina -> app.
    """
    q = (pedido or "").strip(" .¿?¡!\"'")
    if not q or len(q) < 2:
        return None
    # Ruido que se cuela: "la aplicación de virtualbox" -> "virtualbox"
    q = re.sub(r"^\s*(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+|mis\s+)*"
               r"(?:aplicacion|programa|app|juego|herramienta|software|pagina|"
               r"web|sitio)\s+(?:de\s+|del\s+|para\s+)?", "", q, flags=re.I).strip()
    if len(q) < 2:
        return None

    # 1) La página del ciclo: va al campus de verdad, no a una URL inventada.
    if _CAMPUS_RE.search(_sin_tildes(q)):
        return {"tipo": "campus", "nombre": "la página del ciclo",
                "url": campus_url()}

    # 2) Web por su nombre.
    url = sitio(q)
    if url:
        return {"tipo": "web", "nombre": q, "url": url}

    # 3) URL completa o dominio escrito tal cual.
    if re.match(r"^https?://\S+$", q, re.I):
        return {"tipo": "web", "nombre": q, "url": q}
    if re.match(r"^[a-z0-9][a-z0-9\-]*(\.[a-z0-9\-]+)+$", _sin_tildes(q)):
        return {"tipo": "web", "nombre": q,
                "url": ("http://" + q if q.startswith("http")
                        else "https://" + q)}

    # 4) Máquina virtual.
    vm = _encaja_vm(q)
    if vm:
        return {"tipo": "vm", "nombre": vm}
    return None
