"""Lo que la pantalla principal necesita en una sola respuesta.

Tres preguntas que el señor se hace al entrar:

    ¿Está funcionando JARVIS?      -> salud (lo da /api/cmd/estado)
    ¿Qué está haciendo?            -> actividad()
    ¿Hay algo que necesite de mí?  -> permisos()

Nada de esto inventa: sale de las misiones reales, de los permisos reales del
Risk Engine y de la sesión real de OpenCode.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from . import briefing, store

log = logging.getLogger("brain.cmd.activity")

VIVOS = ("STARTING_OPENCODE", "RUNNING", "TESTING", "WAITING_PERMISSION",
         "WAITING_USER", "ANALYZING", "PLANNING", "WAITING_PLAN_APPROVAL")


def _mision_activa() -> Optional[dict]:
    try:
        from orchestrator.manager import get_manager
        gestor = get_manager()
        for tarea in gestor.listar(12):
            if tarea.get("status") in VIVOS:
                return gestor.obtener(tarea["id"])
        tareas = gestor.listar(1)
        return gestor.obtener(tareas[0]["id"]) if tareas else None
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer la misión activa: %s", exc)
        return None


def actividad() -> dict:
    """La tarea protagonista de la pantalla, con fases y progreso con base real."""
    tarea = _mision_activa()
    if not tarea:
        return {
            "ok": True, "hay_tarea": False,
            "titulo": "JARVIS está disponible.",
            "detalle": "No hay ninguna tarea en marcha. Crea una y JARVIS la analiza "
                       "antes de tocar nada.",
            "estado": "DISPONIBLE",
            "progreso": None, "pasos": [], "cambios": [], "tests": {}, "eventos": [],
        }
    # `manager.obtener()` devuelve la misión ya plana (plan, riesgos, cambios,
    # tests y eventos arriba del todo). Si algún día se pasa la tarea cruda del
    # almacén, `_plano` la deshace igual.
    payload = briefing._plano(tarea)
    pasos = briefing.pasos_con_evidencia(tarea)
    hechos = [p for p in pasos if p["estado"] == "COMPLETED"]
    pendiente = [p for p in pasos if p["estado"] != "COMPLETED"]
    corriendo = tarea.get("status") in ("RUNNING", "TESTING")
    # El agente no dice "voy por el paso 3". Como mucho sabemos cuál es el
    # siguiente pendiente del plan, y se marca como tal.
    if corriendo and pendiente:
        pendiente[0]["estado"] = "EN_CURSO"
        pendiente[0]["evidencia"] = "Siguiente paso del plan; el agente no informa de cuál."
    porcentaje = None
    if pasos:
        porcentaje = int(round(len(hechos) / len(pasos) * 100))
    elif tarea.get("status") == "COMPLETED":
        porcentaje = 100
    pruebas = payload.get("tests") or {}
    resumen = payload.get("summary") or {}
    plan = payload.get("plan") or {}
    return {
        "ok": True,
        "hay_tarea": True,
        "id": tarea.get("id"),
        "titulo": plan.get("objetivo") or tarea.get("idea", ""),
        "subtitulo": tarea.get("idea", ""),
        "estado": tarea.get("status"),
        "estado_texto": _texto_estado(tarea.get("status", "")),
        "agente": payload.get("current_agent") or tarea.get("agent") or "OpenCode",
        "accion": payload.get("current_action") or tarea.get("current_action", ""),
        "tiempo": _duracion(tarea),
        "inicio": tarea.get("started_at"),
        "created_at": tarea.get("created_at"),
        "progreso": porcentaje,
        "progreso_base": (f"{len(hechos)} de {len(pasos)} pasos con evidencia real"
                          if pasos else "Sin plan con pasos: no hay porcentaje honesto."),
        "pasos": pasos,
        "plan": plan,
        "riesgos": payload.get("risks") or [],
        "prompt_chars": tarea.get("prompt_chars") or len(payload.get("prompt", "")),
        "estimate": payload.get("estimate") or tarea.get("estimate") or {},
        "estimate_note": tarea.get("estimate_note", ""),
        "sesion": tarea.get("session_id") or tarea.get("opencode_session_id", ""),
        "cambios": (payload.get("changes") or [])[:12],
        "tests": {"ran": bool(pruebas.get("ran")), "resumen": pruebas.get("summary", ""),
                  "passed": pruebas.get("passed"), "failed": pruebas.get("failed")},
        "commit": _commit_sugerido(tarea),
        "eventos": (payload.get("events") or [])[-14:][::-1],
        "archivo_actual": _archivo_actual(payload),
        "resultado": payload.get("result") or {},
        "resumen_final": resumen.get("resumen", ""),
        "tiempos": resumen.get("tiempos") or {},
    }


def _texto_estado(estado: str) -> str:
    return {
        "DRAFT": "BORRADOR", "ANALYZING": "ANALIZANDO", "PLANNING": "DISEÑANDO",
        "WAITING_PLAN_APPROVAL": "ESPERANDO TU APROBACIÓN",
        "STARTING_OPENCODE": "ABRIENDO SESIÓN", "RUNNING": "EJECUTANDO",
        "WAITING_PERMISSION": "ESPERANDO PERMISO", "WAITING_USER": "ESPERANDO DECISIÓN",
        "TESTING": "EJECUTANDO TESTS", "FAILED": "CON PROBLEMAS",
        "CANCELLED": "DETENIDA", "COMPLETED": "COMPLETADA",
    }.get(estado, estado or "—")


def _duracion(tarea: dict) -> int:
    inicio = tarea.get("created_at") or time.time()
    fin = tarea.get("finished_at") or time.time()
    return max(0, int(fin - inicio))


def _archivo_actual(payload: dict) -> str:
    """Último fichero que OpenCode tocó, según sus propias llamadas."""
    herramientas = payload.get("tools_used") or payload.get("tools") or []
    for herramienta in reversed(herramientas):
        detalle = str(herramienta.get("detail") or "")
        if herramienta.get("name") in ("edit", "write", "read") and detalle:
            return os.path.basename(detalle)
    return ""


def _commit_sugerido(tarea: dict) -> dict:
    """Mensaje de commit propuesto a partir de lo que hizo la misión de verdad."""
    cambios = briefing._plano(tarea).get("changes") or []
    if not cambios:
        return {"texto": "", "ficheros": 0}
    titulo = (tarea.get("idea") or "").strip().split("\n")[0][:60]
    verbo = "add" if all(c.get("action") == "added" for c in cambios) else "update"
    return {"texto": f"{verbo}: {titulo}" if titulo else "",
            "ficheros": len(cambios)}


# --------------------------------------------------------------------------- #
# Permisos: lo que espera, lo concedido y lo denegado
# --------------------------------------------------------------------------- #
def permisos() -> dict:
    """Un solo sitio con los tres estratos: pendiente, concedido y denegado.

    Los pendientes son los de verdad: los del Risk Engine de JARVIS y los que
    pide OpenCode. El histórico sale de la auditoría, que es donde JARVIS ya
    deja constancia de lo que se autorizó.
    """
    pendientes: list = []
    concedidos: list = []
    try:
        from main import brain
        engine = getattr(brain, "risk", None)
        if engine is not None:
            with engine._pending_lock:
                for action_id, item in list(engine._pending.items())[-20:]:
                    pendientes.append({
                        "id": action_id, "origen": "JARVIS", "accion": "Acción protegida",
                        "detalle": str(getattr(item, "description", ""))[:300],
                        "riesgo": str(getattr(item, "risk_level", "sensitive"))[:24],
                        "vence_en": max(0, int(getattr(item, "timeout_seconds", 0)
                                               - (time.monotonic() - getattr(
                                                   item, "created_at", time.monotonic())))),
                    })
    except Exception as exc:  # noqa: BLE001
        log.debug("permisos del Risk Engine: %s", exc)
    try:
        from orchestrator.manager import get_manager
        gestor = get_manager()
        for tarea in gestor.listar(10):
            for permiso in (gestor.obtener(tarea["id"]) or {}).get("permissions", []):
                base = {"id": permiso.get("id"), "origen": "OpenCode",
                        "accion": permiso.get("action", ""),
                        "detalle": permiso.get("detail") or permiso.get("message", ""),
                        "riesgo": "sensitive", "mision": tarea["id"],
                        "estado": permiso.get("status"), "decision": permiso.get("decision"),
                        "espera": permiso.get("wait_seconds"),
                        "fecha": permiso.get("requested_at")}
                if permiso.get("status") == "pending":
                    pendientes.append(base)
                else:
                    concedidos.append(base)
    except Exception as exc:  # noqa: BLE001
        log.debug("permisos de OpenCode: %s", exc)

    historial = []
    try:
        from memory import sqlite_store as db
        for fila in db.list_audit(120):
            if fila.get("confirmed") is None:
                continue
            historial.append({
                "accion": fila.get("action_type"), "resultado": fila.get("result"),
                "riesgo": fila.get("risk_level"), "at": fila.get("created_at"),
                "detalle": str(fila.get("details") or "")[:160],
                "concedido": fila.get("result") == "success"})
    except Exception as exc:  # noqa: BLE001
        log.debug("historial de permisos: %s", exc)

    # Ámbitos: lo que JARVIS ha pedido de verdad y con qué decisión.
    ambitos: dict = {}
    for permiso in concedidos + pendientes:
        clave = (permiso.get("accion") or "acción").strip().lower() or "acción"
        entrada = ambitos.setdefault(clave, {"ambito": clave, "estado": "perguntar",
                                             "usos": 0, "ultimo": None})
        entrada["usos"] += 1
        entrada["ultimo"] = permiso.get("fecha") or entrada["ultimo"]
        if permiso.get("decision") == "always":
            entrada["estado"] = "permitido"
        elif permiso.get("decision") == "once":
            entrada["estado"] = "permitido"
        elif permiso.get("decision") == "reject":
            entrada["estado"] = "denegado"
    try:
        from memory import sqlite_store as db
        for clave in db.get_setting("perm_ambitos", "") or "":  # setting opcional
            ambitos[clave] = {"ambito": clave, "estado": "permitido", "usos": 0, "ultimo": None}
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "pendientes": pendientes,
        "total_pendientes": len(pendientes),
        "concedidos": concedidos[-12:],
        "historial": historial[:20],
        "ambitos": sorted(ambitos.values(), key=lambda a: -a["usos"])[:10],
        "denegados": [p for p in concedidos if p.get("decision") == "reject"][-8:],
    }