"""Qué modelos hay, qué sabe hacer cada uno y cuál toca para cada tarea.

El registro **no inventa capacidades**:

- Si el proveedor contesta a su `GET /models`, se usa lo que dice (la lista
  real y el `context_window` de cada modelo).
- Si no contesta, se usa lo declarado aquí, y lo declarado solo cubre modelos
  que existen de verdad y que JARVIS usa o puede usar.
- Una capacidad que no se conoce queda en `None` (desconocida). Ante una
  capacidad desconocida el gateway **no** ofrece herramientas: pedir tool
  calling a un modelo que no lo soporta acaba en un 400 y en un turno perdido.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from config import Config

log = logging.getLogger("brain.ai.registry")

# Tipos de tarea que el gateway entiende.
FAST = "FAST"            # conversación, comandos rápidos
GENERAL = "GENERAL"      # Charla normal
REASONING = "REASONING"  # análisis, planificación, diagnóstico
CODING = "CODING"        # programación → la lleva OpenCode, no Groq
VISION = "VISION"        # descripcion de lo que se ve en pantalla o camara

TAREAS = (FAST, GENERAL, REASONING, CODING, VISION)

_lock = threading.RLock()
_cache: dict = {"modelos": None, "at": 0.0, "fuente": ""}
_TTL = 300.0             # la lista de modelos cambia muy despacio

# Catálogo declarado. Solo modelos que existen en Groq de verdad. Lo que no esté
# aquí y tampoco lo diga el proveedor se conoce como "desconocido", nunca como
# "sí puede".
DECLARADOS: dict[str, dict] = {
    "openai/gpt-oss-120b": {
        "display_name": "GPT-OSS 120B (Groq)",
        "context_window": 131072, "supports_tools": True, "supports_reasoning": True,
    },
    "openai/gpt-oss-20b": {
        "display_name": "GPT-OSS 20B (Groq)",
        "context_window": 131072, "supports_tools": True, "supports_reasoning": True,
    },
    "llama-3.1-8b-instant": {
        "display_name": "Llama 3.1 8B Instant (Groq)",
        "context_window": 128000, "supports_tools": True, "supports_reasoning": False,
    },
    "llama-3.3-70b-versatile": {
        "display_name": "Llama 3.3 70B Versatile (Groq)",
        "context_window": 128000, "supports_tools": True, "supports_reasoning": False,
    },
    "deepseek-r1-distill-llama-70b": {
        "display_name": "DeepSeek R1 Distill (Groq)",
        "context_window": 131072, "supports_tools": False, "supports_reasoning": True,
    },
    "moonshotai/kimi-k2-instruct": {
        "display_name": "Kimi K2 Instruct (Groq)",
        "context_window": 131072, "supports_tools": True, "supports_reasoning": False,
    },
    "qwen/qwen3-32b": {
        "display_name": "Qwen3 32B (Groq)",
        "context_window": 131072, "supports_tools": True, "supports_reasoning": False,
    },
}


# --------------------------------------------------------------------------- #
# Lo que elige el señor en JARVIS Live (se guarda en settings)
# --------------------------------------------------------------------------- #
def _seleccion() -> dict:
    """Selección viva: primero JARVIS Live, después la configuración."""
    guardada = {}
    try:
        from memory import sqlite_store as db

        import json

        crudo = db.get_setting("ai_seleccion", "")
        if crudo:
            guardada = json.loads(crudo) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer la seleccion de modelos: %s", exc)
    return guardada if isinstance(guardada, dict) else {}


def seleccion() -> dict:
    """La selección vigente (la de Live si se cambió, si no la de siempre)."""
    return _seleccion()


def guardar_seleccion(datos: dict) -> dict:
    """Guarda qué proveedor/modelo se usa para cada tipo de tarea."""
    limpio = {}
    for tarea in TAREAS:
        valor = datos.get(tarea)
        if isinstance(valor, str) and valor.strip():
            limpio[tarea] = valor.strip()[:160]
    habil = {}
    for prov in ("groq", "gemini", "openrouter", "ollama"):
        if prov in datos:
            habil[prov] = bool(datos[prov])
    if habil:
        limpio["habilitados"] = habil
    if "fallback" in datos:
        limpio["fallback"] = bool(datos["fallback"])
    import json

    from memory import sqlite_store as db

    db.set_setting("ai_seleccion", json.dumps(limpio, ensure_ascii=False))
    with _lock:
        _cache["at"] = 0.0
    return limpio


def habilitados() -> dict:
    """Qué proveedores están activos. Sin esto: los que tengan clave."""
    sel = _seleccion()
    base = {"groq": bool(Config.GROQ_API_KEY), "gemini": bool(Config.GEMINI_API_KEY),
            "openrouter": bool(Config.OPENROUTER_API_KEY)}
    elegido = sel.get("habilitados")
    if isinstance(elegido, dict):
        for prov, valor in elegido.items():
            if prov in base:
                base[prov] = bool(valor) and base[prov]
    return base


def fallback_activo() -> bool:
    sel = _seleccion()
    return bool(sel.get("fallback", True))


# --------------------------------------------------------------------------- #
# Modelos configurados
# --------------------------------------------------------------------------- #
def _modelo_config(tarea: str) -> str:
    """El modelo que dice la configuración para ese tipo de tarea."""
    sel = _seleccion().get(tarea)
    if isinstance(sel, str) and sel:
        return sel.split("/", 1)[1] if sel.startswith("groq/") else sel
    if tarea == FAST:
        return Config.GROQ_FAST_MODEL or Config.GROQ_MODEL_LIGHT or Config.GROQ_MODEL_FAST
    if tarea == REASONING:
        return Config.GROQ_REASONING_MODEL or Config.GROQ_DEFAULT_MODEL or Config.GROQ_MODEL_FAST
    if tarea == VISION:
        return Config.GROQ_MODEL_VISION or Config.GROQ_MODEL_FAST
    return Config.GROQ_DEFAULT_MODEL or Config.GROQ_MODEL_FAST


def configurados() -> list[dict]:
    """Los modelos que JARVIS tiene asignados ahora mismo, uno por tarea."""
    salida = []
    for tarea in TAREAS:
        if tarea == CODING:
            salida.append({
                "tarea": tarea, "id": "opencode", "provider": "opencode",
                "display_name": "OpenCode (agente de programacion)",
                "enabled": True, "nota": "La programacion la lleva OpenCode, no Groq.",
                "capabilities": {"streaming": True, "tools": True, "reasoning": False,
                                 "vision": False, "files": True, "tests": True},
                "context_window": None, "supports_streaming": True,
                "supports_tools": True, "supports_reasoning": False,
            })
            continue
        nombre = _sin_proveedor(_modelo_config(tarea))
        meta = _meta(nombre)
        salida.append({"tarea": tarea, "enabled": bool(habilitados().get("groq")),
                       **meta})
    return salida


def _sin_proveedor(modelo_id: str) -> str:
    """`groq/openai/gpt-oss-120b` -> `openai/gpt-oss-120b`.

    Los ids de Groq llevan barras dentro del nombre del modelo, así que aquí no
    vale partir por la última: hay que quitar SOLO el prefijo del proveedor.
    """
    texto = str(modelo_id or "").strip()
    for prefijo in ("groq/", "gemini/", "openrouter/", "ollama/"):
        if texto.startswith(prefijo):
            return texto[len(prefijo):]
    return texto


def buscar(modelo_id: str) -> Optional[dict]:
    return _meta(_sin_proveedor(modelo_id))


def elegir(tarea: str = GENERAL) -> dict:
    """Qué modelo toca para una tarea. Devuelve el `id` completo `groq/...`."""
    tarea = (tarea or GENERAL).upper()
    if tarea not in TAREAS:
        tarea = GENERAL
    if tarea == CODING:
        return {"id": "opencode", "provider": "opencode", "tarea": CODING,
                "nota": "JARVIS delega el codigo en OpenCode."}
    return {"id": f"groq/{_modelo_config(tarea)}", "provider": "groq", "tarea": tarea}


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #
def _meta(nombre: str) -> dict:
    """Metadatos de un modelo de Groq, con lo que diga el proveedor si puede."""
    nombre = str(nombre or "").strip()
    declarado = DECLARADOS.get(nombre, {})
    vivo = _descubiertos().get(nombre, {})
    ventana = vivo.get("context_window") or declarado.get("context_window")
    return {
        "id": f"groq/{nombre}" if nombre else "",
        "provider": "groq",
        "model": nombre,
        "display_name": declarado.get("display_name") or vivo.get("nombre") or nombre,
        "context_window": ventana,
        # Groq documenta streaming en /chat/completions para todos sus modelos
        # de texto, así que aquí no es una suposición por modelo.
        "supports_streaming": True,
        "supports_tools": declarado.get("supports_tools"),
        "supports_reasoning": declarado.get("supports_reasoning"),
        "capabilities": {
            "streaming": True,
            "tools": declarado.get("supports_tools"),
            "reasoning": declarado.get("supports_reasoning"),
            "vision": True,       # el endpoint acepta imagen en línea
            "files": False,
        },
        "enabled": True,
        "fuente": "proveedor" if vivo else ("declarado" if declarado else "desconocido"),
        "declarado": bool(declarado),
        "descubierto": bool(vivo),
    }


def todos(consultar_proveedor: bool = False) -> list[dict]:
    """Catálogo: lo declarado, lo que se usa y lo que devuelve el proveedor."""
    with _lock:
        if (not _cache["modelos"] or time.time() - _cache["at"] > _TTL) \
                and consultar_proveedor:
            _descubrir()
    nombres: list[str] = []
    for nombre in DECLARADOS:
        nombres.append(nombre)
    for entrada in _descubiertos():
        if entrada not in nombres:
            nombres.append(entrada)
    for tarea in TAREAS:
        if tarea == CODING:
            continue
        nombre = _modelo_config(tarea)
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    salida = []
    for nombre in nombres:
        meta = _meta(nombre)
        meta["en_uso"] = [t for t in TAREAS if t != CODING and _modelo_config(t) == nombre]
        salida.append(meta)
    return salida


def _descubiertos() -> dict:
    with _lock:
        return dict(_cache["modelos"] or {})


def _descubrir() -> None:
    """Pregunta a Groq qué modelos tiene. Barato, pero no se pregunta cada vez."""
    try:
        from .providers import groq as _groq

        datos = _groq.listar_modelos()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo listar los modelos de Groq: %s", exc)
        return
    if not datos:
        return
    mapa = {}
    for item in datos:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("id") or "").strip()
        if not nombre:
            continue
        ventana = item.get("context_window")
        mapa[nombre] = {
            "nombre": nombre,
            "context_window": int(ventana) if isinstance(ventana, (int, float)) else None,
        }
    with _lock:
        _cache["modelos"] = mapa
        _cache["at"] = time.time()
        _cache["fuente"] = "groq"
    log.info("Groq declara %d modelos disponibles", len(mapa))


def olvidar_descubrimiento() -> None:
    """Olvida la lista de modelos (se llama tras un `probar`)."""
    with _lock:
        _cache["modelos"] = None
        _cache["at"] = 0.0