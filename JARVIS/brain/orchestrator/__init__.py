"""Orquestador de JARVIS: el ciclo completo de desarrollo con OpenCode.

JARVIS entiende, diseña, planifica, prevé, supervisa, pregunta, pide permisos,
delega, valida e informa. OpenCode analiza, lee, programa, ejecuta, testea,
corrige e implementa. Cada uno en su sitio:

    JARVIS  ->  planner  ->  risk analyzer  ->  task manager  ->  OpenCodeAdapter
    OpenCode  ->  proyecto

Nada aquí simula a OpenCode: si el servidor no está, se dice que no está.
"""

from . import adapter, client, inbox, manager, planner, service, store  # noqa: F401
from .manager import TaskManager, get_manager  # noqa: F401

__all__ = ["adapter", "client", "inbox", "manager", "planner", "service",
           "store", "TaskManager", "get_manager"]