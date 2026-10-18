"""Modo ausente y briefing: qué pasó mientras el señor no estaba delante.

Todo lo que sale aquí sale de datos que ya existen: notificaciones reales,
incidentes reales, misiones reales y la auditoría real. El texto es un
resumen redactado a partir de esos hechos, no una narración inventada; y si no
hay nada, se dice "nada nuevo" en vez de rellenar.
"""
from __future__ import annotations

import logging
import time

from . import services, store

log = logging.getLogger("brain.cmd.briefing")

_CLAVE_VISITA = "cmd_ultima_visita"
_CLAVE_MODO = "cmd_modo_presentacion"


# --------------------------------------------------------------------------- #
def ultima_visita() -> float:
    from memory import sqlite_store as db
    try:
        return float(db.get_setting(_CLAVE_VISITA, "") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def marcar_visita() -> None:
    from memory import sqlite_store as db
    try:
        db.set_setting(_CLAVE_VISITA, str(time.time()))
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo marcar la visita: %s", exc)


def ausente(minutos: int = 0) -> dict:
    """Resumen de lo ocurrido desde la última visita (o desde hace X minutos)."""
    ahora = time.time()
    desde = ultima_visita() if minutos <= 0 else ahora - minutos * 60
    if not desde or desde > ahora:
        desde = ahora - 3600
    ventana = {"desde": desde, "hasta": ahora,
               "minutos": max(1, int((ahora - desde) / 60))}

    notificaciones = [n for n in store.listar_notificaciones(80)
                      if desde <= float(n.get("created_at") or 0) <= ahora]
    incidentes = [i for i in store.listar_incidentes(50)
                  if desde <= float(i.get("created_at") or 0) <= ahora]
    misiones, completadas, fallidas = _misiones(desde, ahora)
    pendientes = {
        "permisos": _pendientes_permisos(),
        "preguntas": _pendientes_preguntas(),
    }
    return {
        "ok": True,
        "ventana": ventana,
        "notificaciones": notificaciones[:25],
        "incidentes": incidentes[:10],
        "misiones": misiones[:15],
        "completadas": len(completadas),
        "fallidas": len(fallidas),
        "pendientes": pendientes,
        "texto": redactar(ventana, notificaciones, incidentes, completadas,
                          fallidas, pendientes),
    }


def _misiones(desde: float, hasta: float) -> tuple[list, list, list]:
    try:
        from orchestrator import store as dev_store
        tareas = dev_store.cargar_todas(40)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudieron leer las misiones: %s", exc)
        return [], [], []
    nuevas, completadas, fallidas = [], [], []
    for tarea in tareas:
        actualizada = float(tarea.get("updated_at") or 0)
        if not (desde <= actualizada <= hasta):
            continue
        nuevas.append({"id": tarea.get("id"), "idea": tarea.get("idea", "")[:120],
                       "status": tarea.get("status"),
                       "session_id": tarea.get("opencode_session_id", "")})
        if tarea.get("status") == "COMPLETED":
            completadas.append(tarea)
        if tarea.get("status") in ("FAILED", "CANCELLED"):
            fallidas.append(tarea)
    return nuevas, completadas, fallidas


def _pendientes_permisos() -> int:
    total = 0
    try:
        from main import brain
        engine = getattr(brain, "risk", None)
        if engine is not None:
            with engine._pending_lock:
                total += len(engine._pending)
    except Exception:  # noqa: BLE001
        pass
    try:
        from orchestrator.manager import get_manager
        gestor = get_manager()
        total += len(gestor.permisos_pendientes())
    except Exception:  # noqa: BLE001
        pass
    return total


def _pendientes_preguntas() -> int:
    try:
        from orchestrator.manager import get_manager
        return len(get_manager().preguntas_pendientes())
    except Exception:  # noqa: BLE001
        return 0


def redactar(ventana: dict, notificaciones: list, incidentes: list,
             completadas: list, fallidas: list, pendientes: dict) -> str:
    """Texto para leer en voz alta. Cada frase sale de un dato."""
    partes = []
    minutos = ventana.get("minutos", 0)
    if not notificaciones and not incidentes and not completadas and not fallidas:
        return f"Durante los últimos {minutos} minutos no ha pasado nada que contarte."
    if completadas:
        partes.append(f"terminé {len(completadas)} "
                     f"{'misión' if len(completadas) == 1 else 'misiones'}")
    if fallidas:
        partes.append(f"{len(fallidas)} quedaron con problemas")
    cambios = sum(len(s.get("changes") or []) for s in completadas
                  for s in [s.get("payload") or {}])
    if cambios:
        partes.append(f"OpenCode dejó {cambios} ficheros tocados")
    tests = [s.get("payload", {}).get("tests") or {} for s in completadas]
    pasados = sum(t.get("passed") or 0 for t in tests)
    if pasados:
        partes.append(f"y {pasados} tests pasaron")
    preguntas = [n for n in notificaciones if n.get("kind") == "question"]
    if preguntas:
        partes.append(f"quedó {len(preguntas)} "
                     f"{'pregunta' if len(preguntas) == 1 else 'preguntas'} sin responder")
    permisos = [n for n in notificaciones if n.get("kind") == "permission"]
    if permisos:
        partes.append(f"{len(permisos)} por Autorizar")
    if incidentes:
        graves = [i for i in incidentes if i.get("severity") in ("critical", "alto")]
        partes.append(f"hubo {len(incidentes)} incidentes"
                     + (f", {len(graves)} graves" if graves else ""))
    texto = ("Durante tu ausencia, " + ", ".join(partes) + "."
             if partes else f"Durante los últimos {minutos} minutos no ha pasado nada raro.")
    if pendientes.get("permisos") or pendientes.get("preguntas"):
        texto += (" Ahora mismo necesito " +
                  ", ".join(filter(None, [
                      f"{pendientes['permisos']} permisos" if pendientes.get("permisos") else "",
                      f"{pendientes['preguntas']} decisiones" if pendientes.get("preguntas") else ""])
                  ) + ".")
    return texto


# --------------------------------------------------------------------------- #
# Modo presentación (§52): los mismos datos, contados de otra forma
# --------------------------------------------------------------------------- #
def presentacion(mission_id: str = "") -> dict:
    """Recorrido de la misión con fases REALES (las que existen en el historial)."""
    try:
        from orchestrator import store as dev_store
        tarea = dev_store.cargar(mission_id) if mission_id else None
        if not tarea:
            tareas = dev_store.cargar_todas(1)
            tarea = tareas[0] if tareas else None
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer la misión: %s", exc)
        tarea = None
    if not tarea:
        return {"ok": False, "error": "No hay ninguna misión que presentar."}
    fases = [
        ("IDEA", bool(tarea.get("idea"))),
        ("ANALYSIS", bool((tarea.get("payload") or {}).get("analysis"))),
        ("PLAN", bool((tarea.get("payload") or {}).get("plan"))),
        ("RISKS", bool((tarea.get("payload") or {}).get("risks"))),
        ("APPROVAL", bool(tarea.get("started_at"))),
        ("OPENCODE", bool(tarea.get("opencode_session_id"))),
        ("EXECUTION", bool((tarea.get("payload") or {}).get("tools_used"))),
        ("PERMISSION", bool((tarea.get("payload") or {}).get("permissions"))),
        ("QUESTION", bool((tarea.get("payload") or {}).get("questions"))),
        ("TEST", bool(((tarea.get("payload") or {}).get("tests") or {}).get("ran"))),
        ("VALIDATION", bool((tarea.get("payload") or {}).get("result"))),
        ("COMPLETED", tarea.get("status") == "COMPLETED"),
    ]
    return {"ok": True, "mission_id": tarea.get("id"), "idea": tarea.get("idea"),
            "status": tarea.get("status"),
            "fases": [{"nombre": n, "alcanzada": c} for n, c in fases],
            "resumen": ((tarea.get("payload") or {}).get("summary") or {}).get("resumen", "")}


def modo_presentacion(activo: bool) -> bool:
    from memory import sqlite_store as db
    valor = "1" if activo else "0"
    try:
        db.set_setting(_CLAVE_MODO, valor)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo guardar el modo: %s", exc)
    return activo


def hay_presentacion() -> bool:
    from memory import sqlite_store as db
    try:
        return db.get_setting(_CLAVE_MODO, "0") == "1"
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Estado de los pasos de una misión, con evidencia real (§11)
# --------------------------------------------------------------------------- #
def _plano(tarea: dict) -> dict:
    """El gestor devuelve la tarea plana y el almacén la guarda con `payload`.

    Aquí se leen los mismos campos en los dos casos: quien llame a esta función
    no tiene que acordarse de cómo se guardó.
    """
    if isinstance(tarea.get("payload"), dict):
        return tarea["payload"]
    return tarea or {}


def pasos_con_evidencia(tarea: dict) -> list[dict]:
    """Estado de cada paso del plan.

    OpenCode no informa de "he hecho el paso 3". Así que un paso solo se marca
    como hecho cuando hay una prueba: los ficheros que ese paso nombraba
    aparecen tocados, o se ejecutó un comando de tests. El resto sigue
    PENDIENTE, porque decir otra cosa sería inventar.
    """
    payload = _plano(tarea)
    plan = payload.get("plan") or {}
    cambios = {str(c.get("path", "")).replace("\\", "/").lower()
               for c in (payload.get("changes") or [])}
    tests = payload.get("tests") or {}
    hay_tests = bool(tests.get("ran"))
    pasos = []
    for paso in (plan.get("pasos") or []):
        ficheros = [str(f).replace("\\", "/").lower() for f in (paso.get("archivos") or [])]
        tocados = [f for f in ficheros
                   if f in cambios or any(f.split("/")[-1] == c.split("/")[-1] for c in cambios)]
        estado = "PENDING"
        if tocados:
            estado = "COMPLETED"
        elif ficheros and any(c in cambios for c in ficheros):
            estado = "COMPLETED"
        if not tocados and ficheros and cambios and not any(
                f.split("/")[-1] in cambios for f in ficheros):
            # el paso nombraba ficheros y ninguno aparece: sigue pendiente.
            estado = "PENDING"
        pasos.append({
            "n": paso.get("n"), "titulo": paso.get("titulo", ""),
            "detalle": paso.get("detalle", ""),
            "archivos": paso.get("archivos", []),
            "estado": estado,
            "evidencia": (f"Ficheros tocados: {', '.join(tocados[:3])}" if tocados else
                          ("Tests ejecutados en la misión." if hay_tests and not ficheros
                           else "Sin evidencia directa todavía.")),
        })
    return pasos