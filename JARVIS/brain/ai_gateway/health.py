"""Estado real de cada proveedor: CONNECTED, AUTH_ERROR, RATE_LIMITED…

Reglas:

- **No se hacen llamadas caras.** El estado sale de lo que ya ha pasado
  (peticiones registradas, ultimo error, ultimo uso). La unica llamada de
  coste es `probar()`, y solo cuando el señor la pide desde /api/ai/test o
  desde JARVIS Live.
- **No se confunde "sin datos" con "roto".** Un proveedor con clave pero sin
  peticiones todavía se enseña como `SIN DATOS`, no como `ERROR`.
- La clave **nunca** se devuelve ni se enseña: solo si hay o no hay.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from config import Config

from . import registry, stats

log = logging.getLogger("brain.ai.health")

# Cuánto tiempo vale lo que sabemos de un proveedor antes de dejarlo en
# "SIN DATOS" si no se mueve nada.
_TTL = 15 * 60

_cache: dict = {"at": 0.0, "datos": []}
_cache_lock = __import__("threading").RLock()

SIN_DATOS = "SIN_DATOS"
DESCONECTADO = "DISCONNECTED"
AUTH_ERROR = "AUTH_ERROR"
RATE_LIMITED = "RATE_LIMITED"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"
CONNECTED = "CONNECTED"
SIN_CLAVE = "SIN_CLAVE"
APAGADO = "APAGADO"


def proveedores() -> list[dict]:
    """Un estado por proveedor, listo para pintar."""
    with _cache_lock:
        if time.time() - _cache["at"] < 5.0 and _cache["datos"]:
            return [dict(p) for p in _cache["datos"]]
    datos = []
    hab = registry.habilitados()
    claves = {"groq": bool(Config.GROQ_API_KEY),
              "gemini": bool(Config.GEMINI_API_KEY),
              "openrouter": bool(Config.OPENROUTER_API_KEY)}
    filas = stats.listar(_TTL, 200)
    for nombre in ("groq", "gemini", "openrouter", "ollama"):
        propias = [f for f in filas if f.get("provider") == nombre]
        ultima = propias[-1] if propias else None
        ultimo_error = next((f for f in reversed(propias) if not f["ok"]), None)
        estado = _estado(nombre, hab, claves, propia=propias, ultima=ultima,
                         ultimo_error=ultimo_error)
        datos.append({
            "id": nombre,
            "estado": estado,
            "habilitado": bool(hab.get(nombre, True)),
            "tiene_clave": bool(claves.get(nombre, False)) if nombre in claves else True,
            "peticiones": len(propias),
            "fallos": sum(1 for f in propias if not f["ok"]),
            "ultimo_uso": ({"at": ultima["at"], "model": ultima.get("model"),
                            "latency_ms": ultima.get("latency_ms"),
                            "tokens": ultima.get("total_tokens")}
                           if ultima else None),
            "ultimo_error": ({"at": ultimo_error["at"], "tipo": ultimo_error.get("error_kind")}
                             if ultimo_error else None),
            "detalle": _detalle(estado, nombre),
        })
    with _cache_lock:
        _cache["at"] = time.time()
        _cache["datos"] = datos
    return [dict(d) for d in datos]


def _estado(nombre: str, hab: dict, claves: dict, *, propia: list, ultima: Optional[dict],
            ultimo_error: Optional[dict]) -> str:
    if nombre in claves and not claves[nombre]:
        return SIN_CLAVE
    if not hab.get(nombre, True):
        return APAGADO
    if not propia:
        return SIN_DATOS
    if ultimo_error and (ultima is None or not ultima["ok"]):
        return _estado_de_error(ultimo_error.get("error_kind") or "")
    return CONNECTED


def _estado_de_error(tipo: str) -> str:
    return {"auth_error": AUTH_ERROR, "forbidden": AUTH_ERROR,
            "rate_limited": RATE_LIMITED, "timeout": TIMEOUT,
            "network": DESCONECTADO, "no_key": SIN_CLAVE,
            "unavailable": ERROR, "not_found": ERROR,
            "malformed": ERROR}.get(tipo, ERROR)


def _detalle(estado: str, nombre: str) -> str:
    return {
        CONNECTED: "Ha contestado en la ultima prueba real.",
        SIN_DATOS: "Con clave, pero todavia no ha recibido ninguna peticion.",
        SIN_CLAVE: "No hay clave configurada para este proveedor.",
        APAGADO: "El señor lo ha apagado en JARVIS Live.",
        AUTH_ERROR: "La clave no vale (401/403).",
        RATE_LIMITED: "Sin cuota ahora mismo (429).",
        TIMEOUT: "Se paso el plazo esperando.",
        DESCONECTADO: "No se pudo ni abrir la conexion.",
        ERROR: "El proveedor fallo.",
    }.get(estado, "Estado desconocido.")


def estado_global() -> str:
    """El peor estado entre los proveedores que se pueden usar de verdad.

    Un proveedor sin clave no cuenta como averiado: sencillamente no esta. El
    modelo local (ollama) tampoco hace decaer el estado si hay alguien en linea
    que conteste; solo si no queda nadie mas.
    """
    datos = proveedores()
    con_clave = [p for p in datos if p.get("tiene_clave")]
    locales = [p for p in datos if p["id"] == "ollama"]
    candidatos = con_clave or locales
    estados = [p["estado"] for p in candidatos if p["habilitado"]] or \
              [p["estado"] for p in candidatos]
    for malo in (AUTH_ERROR, RATE_LIMITED, TIMEOUT, DESCONECTADO, APAGADO,
                 ERROR):
        if malo in estados:
            return malo
    if CONNECTED in estados:
        return CONNECTED
    return estados[0] if estados else SIN_DATOS


def probar(proveedor: str = "groq", clave: str = "") -> dict:
    """Pide una llamada real de 1 token. Solo cuando se pide."""
    from . import gateway as _gateway

    resultado = _gateway.probar(proveedor, clave)
    with _cache_lock:                      # el estado acaba de cambiar
        _cache["at"] = 0.0
    log.info("prueba de %s: %s", proveedor, resultado.get("estado"))
    return resultado