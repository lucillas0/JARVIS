# -*- coding: utf-8 -*-
"""Bus de progreso: las herramientas largas avisan de lo que hacen y las
ventanas (orbe y Win+J) lo muestran en el árbol de actividad."""
import logging

log = logging.getLogger("status")

_EMIT = None
_TASK = ""


def set_emit(fn) -> None:
    global _EMIT
    _EMIT = fn


def start_task(title: str) -> None:
    global _TASK
    _TASK = title
    step(title, first=True)


def step(label: str, first: bool = False) -> None:
    if _EMIT is None:
        return
    try:
        _EMIT({"type": "task_step", "task": _TASK, "label": label,
               "first": bool(first)})
    except Exception as e:
        log.debug("status.step: %s", e)


def end_task(summary: str = "") -> None:
    global _TASK
    if _EMIT is not None and _TASK:
        try:
            _EMIT({"type": "task_step", "task": _TASK,
                   "label": summary or "Tarea terminada", "done": True})
        except Exception:
            pass
    _TASK = ""
