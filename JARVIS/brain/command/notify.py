"""Notificaciones e incidentes a partir del bus de eventos REAL de JARVIS.

No hay un sistema de avisos paralelo: este módulo se engancha al bus que ya
existe (Brain.emit) y traduce hechos a dos cosas concretas:

  · una NOTIFICACIÓN, para que el señor sepa que hay algo que le toca
  · un INCIDENTE, cuando lo que ha pasado es un fallo de verdad

Regla: si el evento no ocurrió, aquí no hay nada. Cero mensajes de relleno.
"""
from __future__ import annotations

import logging
import time

from . import store

log = logging.getLogger("brain.cmd.notify")

# tipo de evento -> (clase de notificación, categoría, título legible)
_MAPA = {
    "opencode.permission_requested": ("permission", "permisos", "OpenCode pide permiso"),
    "opencode.question_requested": ("question", "preguntas", "OpenCode necesita una decisión"),
    "confirmation_request": ("permission", "permisos", "Acción protegida esperando permiso"),
    "heal_approval_request": ("permission", "permisos", "Autocorrección esperando permiso"),
    "opencode.completed": ("success", "misiones", "Misión completada"),
    "opencode.test_finished": ("info", "tests", "Tests terminados"),
    "opencode.started": ("info", "misiones", "OpenCode en marcha"),
    "opencode.file_changed": ("info", "cambios", "Ficheros modificados"),
    "live_task_start": ("info", "tareas", "Trabajo iniciado"),
    "live_task_end": ("info", "tareas", "Trabajo terminado"),
    "agent_state": ("info", "agentes", "Cambio de agente"),
    "agent_update": ("info", "agentes", "Actualización de agente"),
    "proactive_notice": ("warning", "sistema", "Aviso proactivo"),
    "speech_break": ("info", "voz", "Interrupción por voz"),
}

_ERRORES = ("opencode.error", "error_report", "selfheal_update")

_seen: dict = {}


def _titulo(msg: dict) -> str:
    return (str(msg.get("detail") or msg.get("label") or msg.get("title")
                or msg.get("error") or msg.get("state") or msg.get("type") or "")
            .strip()[:180])


def al_evento(msg: dict) -> None:
    """Entrada única: se llama por cada evento del bus. Nunca lanza."""
    if not isinstance(msg, dict):
        return
    tipo = str(msg.get("type") or "")
    if not tipo or tipo in ("opencode.state", "action_status", "task_step",
                            "user_heard", "assistant_reply", "pong"):
        return
    clave = (tipo, str(msg.get("task_id") or ""), _titulo(msg)[:60])
    ahora = time.time()
    if ahora - _seen.get(clave, 0) < 3.0:      # anti-duplicado inmediato
        return
    _seen[clave] = ahora
    if len(_seen) > 2000:
        _seen.clear()

    task_id = str(msg.get("task_id") or "")

    if tipo in _ERRORES and not msg.get("cancelled"):
        texto = _titulo(msg)
        if not texto:
            return
        store.abrir_incidente(
            severity="alto" if tipo in _ERRORES and "fatal" in texto.lower() else "medio",
            source=f"bus:{tipo}", title=texto,
            description=str(msg.get("detail") or "")[:600],
            impact="Ha bloqueado el trabajo en curso.", task_id=task_id,
            diagnosis="Pendiente de investigar.")
        store.crear_notificacion("error", f"Fallo: {texto}", task_id=task_id,
                                 category="incidentes", dedupe_key=texto)
        return

    clase, categoria, base = _MAPA.get(tipo, ("info", "sistema", tipo))
    titulo = _titulo(msg)
    if not titulo:
        titulo = base
    if clase == "permission":
        titulo = f"{base}: {titulo}"[:180]
    store.crear_notificacion(clase, titulo, body=str(msg.get("detail") or "")[:600],
                             category=categoria, task_id=task_id,
                             dedupe_key=titulo)


def incidentes_del_autocorrector(limite: int = 20) -> int:
    """Trae al centro de mando los errores que ya guarda el autocorrector."""
    try:
        from memory import selfheal
        errores = selfheal.list_errors(limite, only_pending=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudieron leer los errores del autocorrector: %s", exc)
        return 0
    nuevos = 0
    for item in errores:
        if not isinstance(item, dict):
            continue
        titulo = str(item.get("error") or "")[:180]
        if not titulo:
            continue
        antes = store.contar_incidentes()["abiertos"]
        store.abrir_incidente(
            severity="critical" if item.get("severidad") == "critico" else "medio",
            source=f"autocorrector:{item.get('source', '?')}", title=titulo,
            description=str(item.get("context") or "")[:600],
            impact="Fallo pendiente de reparación automática.",
            diagnosis=str(item.get("status") or ""),
            dedupe_key=titulo, ventana=86400)
        if store.contar_incidentes()["abiertos"] > antes:
            nuevos += 1
    return nuevos


def instalar(brain) -> None:
    """Se engancha al bus del cerebro. Un suscriptor más, no otro bus."""
    try:
        suscriptores = getattr(brain, "_suscriptores_eventos", None)
        if suscriptores is None:
            suscriptores = []
            brain._suscriptores_eventos = suscriptores
        if al_evento not in suscriptores:
            suscriptores.append(al_evento)
        log.info("centro de mando suscrito al bus de eventos")
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo suscribir el centro de mando: %s", exc)