"""Guardado de las tareas del ciclo de desarrollo.

Una tarea sobrevive a que JARVIS Live se cierre y a que el cerebro se
reinicie: idea, plan, riesgos, prompt enviado, sesión de OpenCode, eventos,
permisos, preguntas, cambios, tests, tiempos y resultado quedan en la tabla
`dev_tasks` de la base de datos de siempre.

El detalle es un JSON por tarea: se lee entero y se escribe entero, que es
como se usa. Lo que se filtra (estado, sesión, fechas) tiene columna propia.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Optional

from memory import sqlite_store as db

log = logging.getLogger("brain.opencode.store")

_lock = threading.RLock()

ESTADOS = ("DRAFT", "ANALYZING", "PLANNING", "WAITING_PLAN_APPROVAL",
           "STARTING_OPENCODE", "RUNNING", "WAITING_PERMISSION", "WAITING_USER",
           "TESTING", "PAUSED", "ERROR",
           "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED", "COMPLETED")
# PAUSED entra en los vivos a proposito: una tarea pausada NO esta terminada, y
# si no se contara como viva, `recuperar()` daria por buena una sesion que el
# senor detuvo. ERROR tambien: es un fallo en curso, no un desenlace.
ESTADOS_VIVOS = ("ANALYZING", "PLANNING", "STARTING_OPENCODE", "RUNNING",
                 "WAITING_PERMISSION", "WAITING_USER", "TESTING", "PAUSED",
                 "ERROR")
# Estados en los que la tarea ya no avanza sola: no se ofrece "reanudar".
ESTADOS_TERMINALES = ("FAILED", "CANCELLED", "COMPLETED",
                      "COMPLETED_WITH_WARNINGS")


def nuevo_id() -> str:
    return "dev-" + uuid.uuid4().hex[:10]


def nueva(idea: str, project_path: str = "", model: str = "", agent: str = "") -> dict:
    """Tarea en DRAFT. Todavía no se ha analizado nada ni se ha tocado Git."""
    ahora = time.time()
    return {
        "id": nuevo_id(),
        "idea": (idea or "").strip()[:4000],
        "status": "DRAFT",
        "project_path": project_path or "",
        "opencode_session_id": "",
        "opencode_model": model or "",
        "opencode_agent": agent or "",
        "created_at": ahora,
        "started_at": None,
        "finished_at": None,
        "updated_at": ahora,
        "payload": {
            "analysis": {},
            "plan": {},
            "risks": [],
            "prompt": "",
            "events": [],
            "permissions": [],
            "questions": [],
            "answers": [],
            "changes": [],
            "tests": {"ran": False, "passed": None, "failed": None, "runs": []},
            "result": {},
            "summary": {},
            "timings": {"analysis": 0.0, "planning": 0.0, "approval": 0.0,
                        "execution": 0.0, "permission": 0.0, "question": 0.0,
                        "tests": 0.0},
            "phase": "",
            "phase_started": ahora,
            "current_step": None,
            "current_step_title": "",
            "current_action": "",
            "current_agent": "",
            "estimate": {},
            "error": "",
            "notes": [],
            "revisions": [],
            "opencode": {},
            "tools_used": [],
            "waiting_since": None,
            "recoverable": False,
            "attempts": 0,
        },
    }


def guardar(tarea: dict) -> None:
    tarea["updated_at"] = time.time()
    payload = tarea.get("payload") or {}
    # Los eventos son lo que más crece: se acotan para no inflar la base.
    eventos = payload.get("events") or []
    if len(eventos) > 400:
        payload["events"] = eventos[-400:]
    try:
        with _lock:
            fila = db.get_dev_task(tarea["id"])
            if fila is None:
                db.add_dev_task(tarea)
            else:
                db.update_dev_task(tarea["id"], tarea)
    except Exception as exc:  # noqa: BLE001 - perder el guardado no debe parar la tarea
        log.warning("no se pudo guardar la tarea %s: %s", tarea.get("id"), exc)


def cargar(task_id: str) -> Optional[dict]:
    try:
        return db.get_dev_task(task_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer la tarea %s: %s", task_id, exc)
        return None


def cargar_todas(limit: int = 40) -> list[dict]:
    try:
        return db.list_dev_tasks(limit)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer el historial de tareas: %s", exc)
        return []


def vivo(estado: str) -> bool:
    return estado in ESTADOS_VIVOS