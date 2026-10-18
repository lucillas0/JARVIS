"""AI Gateway de JARVIS.

Un solo sitio por el que pasa cualquier llamada a un modelo:

    main.py  →  gateway.chat()  →  registro.elegir()  →  proveedor (Groq, …)
                                              ↓
                                        bus de eventos (llm.*)
                                              ↓
                                        JARVIS Live / Discord / voz

No hay bus paralelo, ni memoria paralela, ni permisos paralelos: este módulo
solo decide **qué modelo** y **dónde**; el Permission Manager sigue siendo el
de `brain.risk_engine` y el bus sigue siendo `Brain.emit`.

Submódulos
    errors.py    clasificación de fallos y saneado de mensajes
    registry.py  qué modelos hay y cuál toca para cada tipo de tarea
    providers/   un fichero por proveedor (groq.py habla su API oficial)
    stats.py     uso real: tokens, latencia, reintentos, errores
    health.py    CONNECTED / DISCONNECTED / AUTH_ERROR / RATE_LIMITED / ERROR
    routes.py    /api/ai/*
"""
from __future__ import annotations

import logging

log = logging.getLogger("brain.ai")

__all__ = ["gateway", "errors", "registry", "stats", "health"]


def obtener():
    """Devuelve la instancia única del gateway (la crea la primera vez)."""
    from . import gateway as _gw

    return _gw.instancia()