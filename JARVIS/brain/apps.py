"""Que aplicaciones estan abiertas ahora, para las pastillas de Coucou.

Coucou se entera de los agentes por los hooks de Claude Code. JARVIS no tiene
hooks: lo que hay que mirar es si el proceso de cada editor o agente esta vivo.
Un endpoint pequeno y sin dependencias raras, para que la isla pueda pintar las
mascotas con su estado real en vez de todas en reposo.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List

log = logging.getLogger("jarvis.apps")

# Pastilla de Coucou -> procesos de Windows que la ponen a trabajar. El primero
# que aparezca gana; se comparan en minusculas.
DETECTORES: Dict[str, List[str]] = {
    "jarvis": ["jarvis.exe"],
    "agent_opencode": ["opencode.exe", "opencode"],
    "integration_claude": ["code.exe", "devenv.exe"],
    "agent_codex": ["codex.exe", "codex"],
    "freebuff": ["freebuff.exe", "freebuff"],
    "agent_cursor": ["cursor.exe"],
    "agent_amp": ["amp.exe"],
    "agent_hermes": ["hermes.exe"],
    "agent_copilot": ["copilot.exe"],
    "agent_muse": ["muse.exe"],
}

# Cache breve: la isla pregunta cada pocos segundos y recorrer la tabla de
# procesos en cada pregunta es de sobra.
_CACHE: Dict[str, object] = {"dentro": 0.0, "datos": {}}
_TTL = 3.0


def _procesos_vivos() -> set:
    nombres = set()
    try:
        import psutil

        for p in psutil.process_iter(["name"]):
            nombre = p.info.get("name")
            if nombre:
                nombres.add(nombre.lower())
    except Exception as exc:  # noqa: BLE001 - psutil puede fallar por permisos
        log.debug("psutil no disponible: %s", exc)
        try:
            import csv
            import io
            import subprocess

            salida = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace",
            ).stdout
            # stdout puede llegar a None si el hilo lector de subprocess fallo;
            # sin esto el io.StringIO de abajo reventaba con TypeError.
            for fila in csv.reader(io.StringIO(salida or "")):
                if fila:
                    nombres.add(fila[0].lower())
        except Exception as exc2:  # noqa: BLE001
            log.warning("no pude listar procesos: %s", exc2)
            return nombres
    return nombres


def estado() -> Dict[str, object]:
    """Pastilla -> { activa, procesos, detectado } para todas las declaradas."""
    ahora = time.monotonic()
    if ahora - float(_CACHE["dentro"]) < _TTL and _CACHE["datos"]:
        return _CACHE["datos"]  # type: ignore[return-value]

    vivos = _procesos_vivos()
    datos: Dict[str, object] = {}
    for pastilla, procesos in DETECTORES.items():
        hallados = [p for p in procesos if p in vivos]
        datos[pastilla] = {
            "activa": bool(hallados),
            "procesos": hallados,
            "detectado": bool(hallados),
        }
    _CACHE["dentro"] = ahora
    _CACHE["datos"] = datos
    return datos


def registrar(app) -> None:
    """Monta el endpoint /api/apps en el FastAPI del cerebro."""
    try:
        @app.get("/api/apps")
        def _apps() -> Dict[str, object]:  # pragma: no cover - servidor
            return {"ok": True, "apps": estado()}

        log.info("endpoint /api/apps montado")
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude montar /api/apps: %s", exc)