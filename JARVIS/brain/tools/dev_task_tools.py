# -*- coding: utf-8 -*-
"""Herramientas del ciclo de desarrollo con OpenCode, para que el señor lo
pida con su propia voz o desde Discord.

La diferencia con `opencode_tools` (que lanza un `opencode run` suelto y se
olvida) es que aquí manda el orquestador:

    idea -> analisis del proyecto -> plan + riesgos -> APROBACION DEL SEÑOR ->
    OpenCode -> permisos/preguntas -> tests -> resultado

Ninguna de estas herramientas ejecuta nada sin que el señor haya aprobado el
plan: `opencode_task_create` solo analiza.
"""
import logging

log = logging.getLogger("tools.dev_tasks")

_FRASES = {
    "DRAFT": "creada",
    "ANALYZING": "analizando el proyecto",
    "PLANNING": "diseñando la solución",
    "WAITING_PLAN_APPROVAL": "esperando tu aprobación del plan",
    "STARTING_OPENCODE": "abriendo sesión en OpenCode",
    "RUNNING": "OpenCode está trabajando",
    "WAITING_PERMISSION": "esperando que autorices una operación",
    "WAITING_USER": "esperando que decidas algo que OpenCode pregunta",
    "TESTING": "ejecutando tests",
    "COMPLETED": "completada",
    "FAILED": "terminó con problemas",
    "CANCELLED": "detenida",
}


def _dev():
    """El TaskManager del orquestador.

    Import perezoso a propósito: el Tool Manager se carga al arrancar el
    cerebro y no tiene que arrastrar el planificador entero con él.
    """
    from orchestrator.manager import get_manager
    return get_manager()


def _frase(estado: str) -> str:
    return _FRASES.get(estado, estado or "sin estado")


def _minutos(segundos) -> str:
    try:
        total = int(float(segundos or 0))
    except (TypeError, ValueError):
        return "0 s"
    if total < 60:
        return f"{total} s"
    return f"{total // 60} min {total % 60} s"


# --------------------------------------------------------------------------- #
def crear_tarea(idea: str, project_path: str = "") -> dict:
    """Crea la tarea y manda a JARVIS analizarla. NO ejecuta nada."""
    gestor = _dev()
    res = gestor.crear(idea, project_path)
    if not res.get("ok"):
        return res
    task_id = res["task"]["id"]
    gestor.analizar(task_id)
    tarea = gestor.obtener(task_id) or {}
    plan = tarea.get("plan") or {}
    return {
        "ok": True,
        "task_id": task_id,
        "estado": tarea.get("status", "ANALYZING"),
        "idea": idea,
        "objetivo_del_plan": plan.get("objetivo", ""),
        "pasos": len(plan.get("pasos") or []),
        "riesgos": len(tarea.get("risks") or []),
        "estimacion": (tarea.get("estimate") or {}).get("label", ""),
        "mensaje": ("He creado la tarea y estoy analizando el proyecto de verdad. "
                    "En cuanto tenga el plan con sus riesgos te lo enseño en "
                    "JARVIS Live; hasta entonces no se toca nada."),
    }


def revisar_plan(task_id: str, nota: str) -> dict:
    """Aplica una indicación del señor al plan. Sigue sin ejecutar nada."""
    res = _dev().revisar(task_id, nota)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo cambiar el plan."))
    return {"ok": True, "task_id": task_id, "cambios": res.get("changes", []),
            "mensaje": "He cambiado el plan. Revísalo en JARVIS Live antes de aprobar."}


def estado_tarea(task_id: str = "") -> dict:
    """Estado real: fase, permisos, preguntas, tests y tiempos de espera."""
    gestor = _dev()
    if not task_id:
        return {"ok": True, "estado_global": gestor.estado()}
    tarea = gestor.obtener(task_id)
    if not tarea:
        return {"ok": False, "error": "Esa tarea no existe."}
    pendientes = [q for q in tarea.get("questions", []) if q.get("status") == "pending"]
    resumen = {
        "ok": True,
        "task_id": task_id,
        "estado": tarea.get("status"),
        "frase": _frase(tarea.get("status", "")),
        "accion_actual": tarea.get("current_action", ""),
        "sesion_opencode": tarea.get("session_id", ""),
        "permisos_pendientes": tarea.get("permissions_pending", 0),
        "preguntas_pendientes": tarea.get("questions_pending", 0),
        "ficheros_tocados": tarea.get("changes", 0),
        "tests": (tarea.get("tests") or {}).get("summary", ""),
        "tiempo_total": _minutos(tarea.get("elapsed", 0)),
        "error": tarea.get("error", ""),
    }
    if pendientes:
        resumen["preguntas"] = [
            {"id": q["id"], "texto": q.get("question", ""),
             "opciones": [o["label"] for o in q.get("options", [])]}
            for q in pendientes[:3]]
    if tarea.get("status") == "COMPLETED":
        resumen["resultado"] = (tarea.get("result") or {}).get("status", "")
        resumen["resumen"] = (tarea.get("summary") or {}).get("resumen", "")[:600]
    return resumen


def listar_tareas() -> dict:
    """Las tareas de desarrollo, la más reciente primero."""
    return {"ok": True,
            "tasks": [{"id": t["id"], "idea": t["idea"][:120], "estado": t["status"],
                       "frase": _frase(t["status"])}
                      for t in _dev().listar(12)]}


def aprobar_tarea(task_id: str) -> dict:
    """El señor da el visto bueno: ahora sí se envía a OpenCode."""
    res = _dev().aprobar(task_id)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo lanzar."))
    return {"ok": True, "task_id": task_id,
            "mensaje": "Enviando a OpenCode. Puedes seguirlo en vivo en JARVIS Live."}


def cancelar_tarea(task_id: str) -> dict:
    """Detiene la tarea. Lo ya escrito se queda como está."""
    res = _dev().cancelar(task_id)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo detener."))
    return {"ok": True, "task_id": task_id,
            "mensaje": res.get("mensaje", "Tarea detenida.")}


def responder_pregunta(task_id: str, question_id: str, respuesta: str) -> dict:
    """Entrega la respuesta del señor a la pregunta que hizo OpenCode."""
    res = _dev().responder(task_id, question_id, {"respuesta": respuesta})
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo enviar la respuesta."))
    return {"ok": True, "task_id": task_id,
            "mensaje": "Respuesta enviada a OpenCode."}


# --------------------------------------------------------------------------- #
# Handlers del Tool Manager
# --------------------------------------------------------------------------- #
def crear_tarea_handler(params: dict) -> dict:
    idea = (params.get("idea") or params.get("task") or "").strip()
    if not idea:
        raise ValueError("Falta la idea.")
    return crear_tarea(idea, params.get("project_path") or "")


def revisar_plan_handler(params: dict) -> dict:
    return revisar_plan((params.get("task_id") or "").strip(),
                        (params.get("note") or params.get("nota") or "").strip())


def estado_tarea_handler(params: dict) -> dict:
    return estado_tarea((params.get("task_id") or "").strip())


def listar_tareas_handler(params: dict) -> dict:
    return listar_tareas()


def aprobar_tarea_handler(params: dict) -> dict:
    return aprobar_tarea((params.get("task_id") or "").strip())


def cancelar_tarea_handler(params: dict) -> dict:
    return cancelar_tarea((params.get("task_id") or "").strip())


def responder_pregunta_handler(params: dict) -> dict:
    return responder_pregunta((params.get("task_id") or "").strip(),
                              (params.get("question_id") or "").strip(),
                              (params.get("answer") or params.get("respuesta") or "").strip())


DEV_TOOLS = [
    {"name": "opencode_task_create",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"idea": "str, lo que el señor quiere que haga, tal cual",
                    "project_path": "str opcional, carpeta donde trabajar"},
     "description": ("Crea una tarea de desarrollo y manda a JARVIS analizar el proyecto real "
                     "para proponer un plan con sus riesgos. NO ejecuta nada todavía: el "
                     "señor tiene que aprobar el plan en JARVIS Live. Úsala cuando pida "
                     "cambiar o añadir algo a un proyecto que ya existe."),
     "label": "Analizando la petición con JARVIS",
     "handler": crear_tarea_handler},
    {"name": "opencode_task_status",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"task_id": "str opcional, id de la tarea (dev-...)"},
     "description": ("Estado real de una tarea de desarrollo: fase, sesión de OpenCode, "
                     "permisos y preguntas pendientes, ficheros tocados, tests y tiempos."),
     "label": "Viendo el estado del desarrollo",
     "handler": estado_tarea_handler},
    {"name": "opencode_task_list",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Lista las tareas de desarrollo con OpenCode.",
     "label": "Viendo las tareas de desarrollo",
     "handler": listar_tareas_handler},
    {"name": "opencode_task_revise",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"task_id": "str, la tarea", "note": "str, lo que quiere cambiar"},
     "description": ("Cambia el plan de una tarea con una indicación del señor "
                     "(«usa SQLite», «no uses Docker», «haz copia antes»). Sigue sin ejecutar."),
     "label": "Cambiando el plan",
     "handler": revisar_plan_handler},
    {"name": "opencode_task_approve",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Apruebo el plan y lo envío a OpenCode?",
     "parameters": {"task_id": "str, la tarea"},
     "description": ("El señor aprueba el plan: la tarea se envía a OpenCode y empieza a "
                     "ejecutarse de verdad."),
     "label": "Enviando el plan a OpenCode",
     "handler": aprobar_tarea_handler},
    {"name": "opencode_task_cancel",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Detengo esta tarea? Lo ya escrito se queda.",
     "parameters": {"task_id": "str, la tarea"},
     "description": "Detiene una tarea de desarrollo. Lo ya escrito no se borra.",
     "label": "Deteniendo el desarrollo",
     "handler": cancelar_tarea_handler},
    {"name": "opencode_task_answer",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"task_id": "str, la tarea", "question_id": "str, la pregunta",
                    "answer": "str, lo que contesta el señor"},
     "description": ("Responde a una pregunta que OpenCode ha hecho durante la tarea "
                     "(base de datos, puerto, formato…). No se responde solo: eso lo decide "
                     "el señor."),
     "label": "Enviando la respuesta a OpenCode",
     "handler": responder_pregunta_handler},
]