"""API del ciclo de desarrollo con OpenCode, para JARVIS Live.

Rutas bajo /api/dev, con el mismo token y la misma política de origen que el
resto del panel: quien no tenga la sesión no ve ni mueve nada.

    GET  /api/dev/status                       estado de OpenCode y tareas vivas
    GET  /api/dev/tasks                        historial de tareas
    POST /api/dev/tasks                        {idea, project_path?, analyze?}
    GET  /api/dev/tasks/{id}                   detalle completo (plan, eventos…)
    POST /api/dev/tasks/{id}/analyze           analizar proyecto y prever riesgos
    POST /api/dev/tasks/{id}/revise            {note} modificar el plan
    POST /api/dev/tasks/{id}/approve           enviar a OpenCode
    POST /api/dev/tasks/{id}/retry             reintentar (continúa la sesión)
    POST /api/dev/tasks/{id}/cancel            detener
    POST /api/dev/tasks/{id}/pause             interrumpir el turno
    POST /api/dev/tasks/{id}/permissions/{pid} {decision: once|always|reject}
    POST /api/dev/tasks/{id}/questions/{qid}   {answer: {...}} o {value: "texto"}
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import autenticado, origen_permitido

from .manager import get_manager

log = logging.getLogger("brain.opencode.routes")
router = APIRouter()

_ID_TAREA = re.compile(r"^dev-[0-9a-f]{6,16}$")
_ID_UPSTREAM = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_DECISIONES = ("once", "always", "reject")


def _authorized(request: Request) -> bool:
    """El mismo candado que el resto de JARVIS Live: cabecera, query o cookie."""
    return (origen_permitido(request.headers.get("origin"))
            and autenticado(request))


def _no_autorizado() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Sesión no autorizada. Recarga JARVIS Live."},
                        status_code=401, headers={"Cache-Control": "no-store"})


def _tarea_valida(task_id: str) -> bool:
    return bool(_ID_TAREA.match(str(task_id or "")))


def _id_upstream_valido(valor: str) -> bool:
    return bool(_ID_UPSTREAM.match(str(valor or "")))


class NuevaTarea(BaseModel):
    idea: str = Field(min_length=1, max_length=4000)
    project_path: str = Field(default="", max_length=400)
    model: str = Field(default="", max_length=120)
    agent: str = Field(default="", max_length=80)
    analizar: bool = True


class NotaPlan(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class Decision(BaseModel):
    decision: str = Field(min_length=1, max_length=20)


class Respuesta(BaseModel):
    answer: dict | None = None
    value: str = Field(default="", max_length=2000)


@router.get("/api/dev/status")
async def dev_status(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(get_manager().estado(), headers={"Cache-Control": "no-store"})


@router.get("/api/dev/tasks")
async def dev_tasks(request: Request, limit: int = 20):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse({"ok": True, "tasks": get_manager().listar(limit)},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/dev/tasks")
async def dev_crear(request: Request, cuerpo: NuevaTarea):
    if not _authorized(request):
        return _no_autorizado()
    gestor = get_manager()
    res = gestor.crear(cuerpo.idea, cuerpo.project_path, cuerpo.model, cuerpo.agent)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    if cuerpo.analizar:
        gestor.analizar(res["task"]["id"])
        res["task"] = gestor.obtener(res["task"]["id"])
    return JSONResponse(res, status_code=201)


@router.get("/api/dev/tasks/{task_id}")
async def dev_tarea(request: Request, task_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _tarea_valida(task_id):
        return JSONResponse({"ok": False, "error": "Identificador de tarea no válido."},
                            status_code=400)
    tarea = get_manager().obtener(task_id)
    if not tarea:
        return JSONResponse({"ok": False, "error": "Esa tarea no existe."}, status_code=404)
    return JSONResponse({"ok": True, "task": tarea}, headers={"Cache-Control": "no-store"})


def _accion(request: Request, task_id: str, funcion, **kwargs):
    if not _authorized(request):
        return _no_autorizado()
    if not _tarea_valida(task_id):
        return JSONResponse({"ok": False, "error": "Identificador de tarea no válido."},
                            status_code=400)
    try:
        res = funcion(task_id, **kwargs)
    except Exception as exc:  # noqa: BLE001 - la UI necesita una respuesta, no un 500
        log.exception("acción de tarea %s", task_id)
        return JSONResponse({"ok": False, "error": f"{exc}"[:200]}, status_code=500)
    if not res.get("ok"):
        return JSONResponse(res, status_code=409)
    tarea = get_manager().obtener(task_id)
    if tarea:
        res["task"] = tarea
    return JSONResponse(res)


@router.post("/api/dev/tasks/{task_id}/analyze")
async def dev_analizar(request: Request, task_id: str):
    return _accion(request, task_id, get_manager().analizar)


@router.post("/api/dev/tasks/{task_id}/revise")
async def dev_revisar(request: Request, task_id: str, cuerpo: NotaPlan):
    return _accion(request, task_id, get_manager().revisar, nota=cuerpo.note)


@router.post("/api/dev/tasks/{task_id}/approve")
async def dev_aprobar(request: Request, task_id: str):
    return _accion(request, task_id, get_manager().aprobar)


@router.post("/api/dev/tasks/{task_id}/retry")
async def dev_reintentar(request: Request, task_id: str):
    return _accion(request, task_id, get_manager().reintentar)


@router.post("/api/dev/tasks/{task_id}/cancel")
async def dev_cancelar(request: Request, task_id: str, cuerpo: dict | None = None):
    return _accion(request, task_id, get_manager().cancelar,
                   motivo=str((cuerpo or {}).get("motivo", ""))[:200])


@router.post("/api/dev/tasks/{task_id}/pause")
async def dev_pausar(request: Request, task_id: str):
    return _accion(request, task_id, get_manager().pausar)


@router.post("/api/dev/tasks/{task_id}/resume")
async def dev_reanudar(request: Request, task_id: str):
    """Reanuda una tarea pausada con su MISMA sesión de OpenCode."""
    return _accion(request, task_id, get_manager().reanudar)


@router.post("/api/dev/tasks/{task_id}/permissions/{permission_id}")
async def dev_permiso(request: Request, task_id: str, permission_id: str, cuerpo: Decision):
    if not _authorized(request):
        return _no_autorizado()
    if cuerpo.decision not in _DECISIONES:
        return JSONResponse({"ok": False,
                             "error": "Decisión no válida. Usa once, always o reject."},
                            status_code=400)
    if not _id_upstream_valido(permission_id):
        return JSONResponse({"ok": False, "error": "Identificador de permiso no válido."},
                            status_code=400)
    return _accion(request, task_id, get_manager().decidir_permiso,
                   permission_id=permission_id, decision=cuerpo.decision)


@router.post("/api/dev/tasks/{task_id}/questions/{question_id}")
async def dev_respuesta(request: Request, task_id: str, question_id: str, cuerpo: Respuesta):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_upstream_valido(question_id):
        return JSONResponse({"ok": False, "error": "Identificador de pregunta no válido."},
                            status_code=400)
    tarea = get_manager().obtener(task_id)
    if not tarea:
        return JSONResponse({"ok": False, "error": "Esa tarea no existe."}, status_code=404)
    pregunta = next((q for q in tarea.get("questions", [])
                     if q.get("id") == question_id and q.get("status") == "pending"), None)
    if not pregunta:
        return JSONResponse({"ok": False, "error": "Esa pregunta ya no está pendiente."},
                            status_code=404)
    if isinstance(cuerpo.answer, dict) and cuerpo.answer:
        respuesta = cuerpo.answer
    elif cuerpo.value:
        respuesta = {pregunta.get("key") or "respuesta": cuerpo.value}
    else:
        return JSONResponse({"ok": False, "error": "La respuesta está vacía."}, status_code=400)
    return _accion(request, task_id, get_manager().responder,
                   question_id=question_id, answer=respuesta)