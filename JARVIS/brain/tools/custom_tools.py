# -*- coding: utf-8 -*-
"""Herramientas creadas por la autocorrección de JARVIS.

Este fichero lo escribe OpenCode (vía selfheal) cuando detecta que falta
una herramienta: añade un handler + entrada en CUSTOM_TOOLS siguiendo el
formato de ToolManager. Se carga automáticamente al arrancar (y en caliente
si el reparador lo pide). NUNCA poner secretos aquí.
"""
import logging

log = logging.getLogger("tools.custom")


def _ejemplo_handler(params: dict) -> dict:
    """Plantilla: no se registra (nombre empieza por _)."""
    return {"ok": True}


CUSTOM_TOOLS: list = []
