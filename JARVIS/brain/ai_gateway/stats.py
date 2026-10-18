"""Lo que JARVIS ha hecho de verdad con los modelos.

Cada petición deja una fila: qué proveedor, qué modelo, qué tarea, cuántos
tokens de entrada y de salida (**los que declara el proveedor**, nunca una
estimación), cuánto tardó, cuántos reintentos hizo y cómo acabó.

El coste se guarda tal cual lo da el proveedor. Groq no devuelve precio en la
respuesta, asi que queda `unknown` y la interfaz lo enseña como `unknown`. No
se calcula ningún coste inventado.

La tabla se lee para el centro de mando y para JARVIS Live; las pruebas usan el
mismo código que producción.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("brain.ai.stats")

_lock = threading.RLock()
_memoria: list[dict] = []          # lo último, para no depender del disco
_MEMORIA_MAX = 200


def registrar(provider: str = "", model: str = "", tarea: str = "",
              ok: bool = True, input_tokens: Optional[int] = None,
              output_tokens: Optional[int] = None, total_tokens: Optional[int] = None,
              latency_ms: float = 0.0, retries: int = 0,
              error_kind: str = "", streaming: bool = False,
              cost: str = "unknown", request_id: str = "") -> dict:
    """Guarda una petición terminada. Devuelve la fila."""
    fila = {
        "id": f"llm-{int(time.time() * 1000)}-{len(_memoria)}",
        "at": time.time(),
        "provider": str(provider or "")[:40],
        "model": str(model or "")[:120],
        "task": str(tarea or "")[:24],
        "ok": bool(ok),
        "input_tokens": _entero(input_tokens),
        "output_tokens": _entero(output_tokens),
        "total_tokens": _entero(total_tokens),
        "latency_ms": round(float(latency_ms or 0), 1),
        "retries": int(retries or 0),
        "error_kind": str(error_kind or "")[:40],
        "streaming": bool(streaming),
        "cost": str(cost or "unknown")[:40],
        "request_id": str(request_id or "")[:40],
    }
    with _lock:
        _memoria.append(fila)
        if len(_memoria) > _MEMORIA_MAX:
            del _memoria[:-_MEMORIA_MAX]
    try:
        from memory import sqlite_store as db

        db.add_llm_usage(fila)
    except Exception as exc:  # noqa: BLE001 - perder una métrica no rompe nada
        log.debug("no se pudo guardar la métrica: %s", exc)
    return fila


def _entero(valor) -> Optional[int]:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def resumen(minutos: int = 60 * 24) -> dict:
    """Cifras agregadas reales. Lo que no se ha medido, no se enseña."""
    filas = listar(minutos)
    ok = [f for f in filas if f["ok"]]
    fallos = [f for f in filas if not f["ok"]]
    entrada = _suma(ok, "input_tokens")
    salida = _suma(ok, "output_tokens")
    total = _suma(ok, "total_tokens")
    latencias = [f["latency_ms"] for f in filas if f.get("latency_ms")]
    tipos: dict = {}
    for f in fallos:
        clave = f.get("error_kind") or "desconocido"
        tipos[clave] = tipos.get(clave, 0) + 1
    por_proveedor: dict = {}
    for f in filas:
        clave = f.get("provider") or "?"
        entrada_p = por_proveedor.setdefault(
            clave, {"peticiones": 0, "fallos": 0, "tokens": 0, "latencia": 0.0})
        entrada_p["peticiones"] += 1
        if not f["ok"]:
            entrada_p["fallos"] += 1
        else:
            entrada_p["tokens"] += f.get("total_tokens") or 0
        entrada_p["latencia"] += f.get("latency_ms") or 0
    for valor in por_proveedor.values():
        valor["media_ms"] = round(valor.pop("latencia") / max(1, valor["peticiones"]), 1)
    return {
        "peticiones": len(filas),
        "ok": len(ok),
        "fallos": len(fallos),
        "errores": dict(sorted(tipos.items(), key=lambda kv: -kv[1])[:8]),
        "input_tokens": entrada,
        "output_tokens": salida,
        "total_tokens": total,
        "media_ms": round(sum(latencias) / len(latencias), 1) if latencias else None,
        "min_ms": round(min(latencias), 1) if latencias else None,
        "max_ms": round(max(latencias), 1) if latencias else None,
        "por_proveedor": por_proveedor,
        "ultima": filas[-1] if filas else None,
        "coste": "unknown",   # Groq no devuelve precio: no se inventa
        "ventana_min": int(minutos),
    }


def listar(minutos: int = 60 * 24, limite: int = 50) -> list[dict]:
    """Las últimas peticiones, de memoria y del disco (memoria gana)."""
    corte = time.time() - max(1, int(minutos)) * 60
    try:
        from memory import sqlite_store as db

        filas = db.list_llm_usage(corte, limite)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer el historial de peticiones: %s", exc)
        filas = []
    if filas:
        return filas[-limite:]
    with _lock:
        return [f for f in _memoria if f["at"] >= corte][-limite:]


def en_vuelo() -> int:
    """Peticiones abiertas ahora mismo (concurrencia real)."""
    from . import gateway as _gw

    return _gw.en_vuelo()


def _suma(filas: list[dict], clave: str) -> Optional[int]:
    valores = [f.get(clave) for f in filas if f.get(clave) is not None]
    return sum(valores) if valores else None


def exportar() -> str:
    """Volcado para depurar (sin claves: aquí no hay ninguna)."""
    return json.dumps({"resumen": resumen(), "ultimas": listar(60, 20)},
                      ensure_ascii=False, indent=2, default=str)