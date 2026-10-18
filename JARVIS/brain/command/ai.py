"""Centro de IA: lo que JARVIS Live enseña y toca de los modelos.

No guarda estado propio: lee el gateway (`brain/ai_gateway`), que a su vez lee
la configuracion y las peticiones reales. Aqui solo se cambia de donde se mira
y quien avisa antes de tocar algo.
"""
from __future__ import annotations

import logging
import time

from ai_gateway import gateway, health, registry, stats

log = logging.getLogger("brain.cmd.ai")


def estado() -> dict:
    """Todo lo que se ve en la vista IA de un vistazo."""
    gw = gateway.instancia()
    datos = gw.estado()
    datos["estado_global"] = health.estado_global()
    datos["salud"] = health.proveedores()
    datos["ts"] = time.time()
    return datos


def modelos(consultar: bool = False) -> dict:
    if consultar:
        registry.todos(True)
    return {"ok": True, "modelos": registry.todos(),
            "asignados": registry.configurados(),
            "seleccion": registry.seleccion(),
            "nota": "Las capacidades que no se conocen quedan en null: "
                    "no se ofrecen herramientas a un modelo que no se sabe "
                    "que las soporte."}


def configurar(datos: dict) -> dict:
    """Cambia modelos por tarea y enciende/apaga proveedores.

    Cambiar de modelo no es una acción peligrosa: no toca el sistema ni manda
    nada a ningun sitio. Por eso no pasa por el Permission Manager; lo que sí
    es delicado es poner una clave, y eso va por el camino de /config.
    """
    guardado = registry.guardar_seleccion(datos)
    return {"ok": True, "seleccion": guardado,
            "asignados": registry.configurados(),
            "detalle": "Cambio aplicado. Se nota en la siguiente petición."}


def probar(proveedor: str = "groq") -> dict:
    """Prueba de conexión real. Es la única llamada de coste, y solo se pide."""
    return health.probar(proveedor)


def uso(minutos: int = 1440) -> dict:
    return {"ok": True, "resumen": stats.resumen(max(1, int(minutos)))}


def peticiones(minutos: int = 1440, limite: int = 40) -> dict:
    return {"ok": True, "peticiones": stats.listar(max(1, int(minutos)), limite)}


def para_el_centro_de_mando() -> dict:
    """Resumen corto para el dashboard (cabecera de Live)."""
    salud = health.proveedores()
    groq = next((p for p in salud if p["id"] == "groq"), {})
    uso_24 = stats.resumen(60 * 24)
    return {
        "estado": groq.get("estado", "SIN DATOS"),
        "modelo": (registry.configurados()[1]["model"] if len(registry.configurados()) > 1
                   else ""),
        "peticiones": uso_24.get("peticiones", 0),
        "fallos": uso_24.get("fallos", 0),
        "tokens": uso_24.get("total_tokens"),
        "media_ms": uso_24.get("media_ms"),
        "en_vuelo": gateway.en_vuelo(),
    }