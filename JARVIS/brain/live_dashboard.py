"""Rutas del panel JARVIS Live; reutiliza el cerebro y sus servicios actuales."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from auth import coincide, origen_permitido, token
from config import BRAIN_DIR
from memory import sqlite_store as db

log = logging.getLogger("brain.live")
router = APIRouter()
LIVE_DIR = Path(__file__).resolve().parent / "live"
_SCHEDULE_LOCK = Lock()


def register(app) -> None:
    """Monta las rutas de JARVIS Live en el FastAPI existente."""
    app.include_router(router)
    log.info("Centro de operaciones JARVIS Live montado en /live")


def _authorized(request: Request) -> bool:
    """El mismo token y la misma política de origen que protegen el WebSocket."""
    authorization = request.headers.get("authorization", "")
    candidate = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    candidate = candidate or request.headers.get("x-jarvis-token")
    return origen_permitido(request.headers.get("origin")) and coincide(candidate)


def _not_authorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Sesión no autorizada. Recarga JARVIS Live."},
                        status_code=401, headers={"Cache-Control": "no-store"})


def _agent_states() -> list[dict]:
    try:
        import apps
        states = apps.estado()
        names = {
            "jarvis": ("JARVIS", "Asistente"),
            "agent_opencode": ("OpenCode", "Agente de programación"),
            "integration_claude": ("Claude / editor", "Agente o editor"),
            "agent_codex": ("Codex", "Agente de programación"),
            "freebuff": ("Freebuff", "Agente de programación"),
            "agent_cursor": ("Cursor", "Editor"),
            "agent_amp": ("Amp", "Agente de programación"),
            "agent_hermes": ("Hermes", "Agente"),
            "agent_copilot": ("Copilot", "Agente de programación"),
            "agent_muse": ("Muse", "Agente"),
        }
        result = []
        for key, (name, description) in names.items():
            detected = states.get(key, {})
            active = bool(detected.get("activa")) if isinstance(detected, dict) else False
            result.append({"id": key, "name": name, "description": description,
                           "status": "activo" if active else "inactivo",
                           "processes": detected.get("procesos", []) if active else []})
        return result
    except Exception as exc:
        log.debug("No se pudo consultar la detección existente de agentes: %s", exc)
        return []


def _resource_state() -> dict:
    resources = {"cpu": None, "ram": None, "ram_used": None, "ram_total": None,
                 "gpu": None, "vram": None, "temperature": None, "source": "psutil"}
    try:
        import psutil
        resources["cpu"] = round(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        resources["ram"] = round(memory.percent)
        resources["ram_used"] = round(memory.used / (1024 ** 3), 1)
        resources["ram_total"] = round(memory.total / (1024 ** 3), 1)
    except Exception as exc:
        log.debug("Métricas de sistema no disponibles: %s", exc)
        resources["source"] = "no_disponible"
    return resources


def _git_changes() -> list[dict]:
    """Lee el estado Git real sin asumir un repositorio ni modificarlo."""
    try:
        root = str(Path(BRAIN_DIR).resolve().parent)
        result = subprocess.run(
            ["git", "-C", root, "status", "--short", "--untracked-files=normal", "--", "brain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2,
            check=False,
        )
        if result.returncode:
            return []
        changes = []
        for line in result.stdout.splitlines()[:20]:
            if len(line) < 4:
                continue
            code, path = line[:2].strip() or "?", line[3:].strip()
            changes.append({"path": path[:220], "action": code[:8]})
        return changes
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Estado Git no disponible: %s", exc)
        return []


def _approval_list(brain) -> list[dict]:
    pending: list[dict] = []
    try:
        engine = getattr(brain, "risk", None)
        if engine is not None:
            with getattr(engine, "_pending_lock", _SCHEDULE_LOCK):
                pending_risk = list(engine._pending.items())[-10:]
            for action_id, item in pending_risk:
                pending.append({
                    "id": action_id,
                    "kind": "confirmation",
                    "action": str(getattr(item, "action_type", "Acción protegida"))[:80],
                    "description": str(item.description)[:400],
                    "risk": str(getattr(item, "risk_level", "sensitive"))[:24],
                    "requester": "Tool Manager de JARVIS",
                    "task": "Acción protegida por el motor de permisos",
                    "remaining_seconds": max(0, int(item.timeout_seconds -
                        (time.monotonic() - getattr(item, "created_at", time.monotonic())))),
                })
    except Exception as exc:
        log.debug("No se pudieron leer confirmaciones pendientes: %s", exc)
    try:
        from memory import selfheal
        pending_ids = list(getattr(selfheal, "_approvals", {}))[-10:]
        errors = selfheal.list_errors(50, only_pending=True)
        error_items = {item.get("id"): item for item in errors}
        for item_id in pending_ids:
            item = error_items.get(item_id, {})
            pending.append({
                "id": str(item_id),
                "kind": "selfheal",
                "action": "Permitir una reparación",
                "description": str(item.get("error", "JARVIS solicita reparar un error crítico."))[:400],
                "risk": "sensitive",
                "requester": str(item.get("source", "Autocorrector de JARVIS"))[:100],
                "task": str(selfheal._plan_for(item))[:240] if item else "Reparación del sistema",
                "remaining_seconds": 90,
            })
    except Exception as exc:
        log.debug("No se pudieron consultar permisos de autocorrección: %s", exc)
    return pending


def _safe_float(value, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def _scheduled_tasks() -> list[dict]:
    """Muestra tareas persistentes del planificador existente; no inventa controles."""
    try:
        from tools.auto_tools import _load_tasks
        with _SCHEDULE_LOCK:
            source = _load_tasks()
    except Exception as exc:
        log.debug("No se pudo consultar el planificador actual: %s", exc)
        return []
    result = []
    safe_tasks = [task for task in source if isinstance(task, dict)]
    for task in sorted(safe_tasks, key=lambda item: _safe_float(item.get("run_at")))[:30]:
        name = str(task.get("name", "tarea"))[:80]
        run_at = _safe_float(task.get("run_at"))
        repeat = task.get("repeat")
        result.append({
            "id": hashlib.sha256(f"{name}:{run_at}".encode("utf-8")).hexdigest()[:12],
            "name": name,
            "description": str(task.get("message", ""))[:200],
            "state": "programada" if run_at > time.time() else "pendiente de ejecución",
            "run_at": run_at,
            "repeat": int(repeat) if isinstance(repeat, (int, float)) and repeat > 0 else None,
            "agent": "JARVIS",
        })
    return result


def _read_recent_events(brain) -> list[dict]:
    with brain._live_event_lock:
        return list(brain._live_events)[-40:]


def _audit_events() -> list[dict]:
    """Los eventos persistentes salen de la auditoría SQL que ya usa Tool Manager."""
    try:
        rows = db.list_audit(limit=60)
    except Exception as exc:
        log.debug("Auditoría reciente no disponible: %s", exc)
        return []
    result = []
    for row in rows:
        risk = str(row.get("risk_level", "low"))
        outcome = str(row.get("result", ""))
        result.append({
            "id": f"audit-{row.get('id', '')}",
            "at": str(row.get("created_at", "")),
            "category": "permisos" if risk == "sensitive" or row.get("confirmed") else
                        "errores" if outcome == "failed" else "tareas",
            "title": str(row.get("action_type", "Acción JARVIS"))[:100],
            "detail": {"success": "Acción completada", "failed": "La acción falló",
                       "cancelled": "Acción cancelada", "timeout": "Tiempo de confirmación agotado"}
                      .get(outcome, "Acción registrada en la auditoría de JARVIS"),
            "risk": risk,
        })
    return result


@router.get("/live")
@router.get("/live/")
async def live_page(request: Request):
    if not origen_permitido(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Origen no autorizado")
    index = LIVE_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="JARVIS Live no está instalado")
    html = index.read_text(encoding="utf-8")
    bootstrap = "<script>window.JARVIS_TOKEN=" + json.dumps(token()) + ";</script>"
    html = html.replace("</head>", bootstrap + "</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/live/{asset:path}")
async def live_asset(asset: str, request: Request):
    if not origen_permitido(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Origen no autorizado")
    base = LIVE_DIR.resolve()
    target = (base / asset).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


@router.get("/api/live/overview")
async def live_overview(request: Request):
    if not _authorized(request):
        return _not_authorized()
    from main import brain
    with brain._live_task_lock:
        live_tasks = [dict(task) for task in brain._live_tasks.values()]
    health = {
        "online": True,
        "active_clients": len(brain.clients),
        "uptime_seconds": max(0, int(time.time() - brain._live_started_at)),
        "voice": str(getattr(brain.voice, "state", "desconectada") or "desconectada"),
        "voice_available": brain.voice is not None,
        "discord": bool(getattr(brain, "discord", None)),
        "active_tasks": live_tasks,
        "scheduled_tasks": _scheduled_tasks(),
        "approvals": _approval_list(brain),
        "agents": _agent_states(),
        "resources": _resource_state(),
        "changes": await asyncio.to_thread(_git_changes),
        "events": await asyncio.to_thread(lambda: _read_recent_events(brain)),
        "audit_events": _audit_events(),
    }
    return JSONResponse(health, headers={"Cache-Control": "no-store"})


@router.get("/api/live/events")
async def live_events(request: Request, after: int = 0):
    """Recupera el anillo acotado de la sesión; el WebSocket entrega lo nuevo."""
    if not _authorized(request):
        return _not_authorized()
    from main import brain
    with brain._live_event_lock:
        events = [event for event in brain._live_events if int(event["id"]) > after]
    return JSONResponse({"events": events[-100:],
                         "oldest_id": events[0]["id"] if events else None},
                        headers={"Cache-Control": "no-store"})


class LiveAction(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    target_id: str = Field(default="", max_length=120)
    approved: bool | None = None
    name: str = Field(default="", max_length=80)


@router.post("/api/live/actions")
async def live_actions(request: Request, action: LiveAction):
    """Únicos controles ofrecidos: aprobar las puertas existentes y cancelar tareas programadas.

    Las tareas de conversación no son cancelables: el protocolo actual de ejecución no
    proporciona interrupción. No se declara una capacidad que el backend no permite.
    """
    if not _authorized(request):
        return _not_authorized()
    from main import brain
    if action.kind == "approve_confirmation":
        if action.approved is None or brain.risk is None:
            return JSONResponse({"ok": False, "error": "Decisión de aprobación obligatoria."}, status_code=400)
        accepted = brain.risk.resolve(action.target_id, action.approved)
        if accepted:
            brain.emit({"type": "confirmation_resolved", "action_id": action.target_id,
                        "approved": action.approved, "ok": True})
        return JSONResponse({"ok": accepted, "error": None if accepted else "La aprobación ya no está pendiente."},
                            status_code=200 if accepted else 404)
    if action.kind == "approve_selfheal":
        if action.approved is None:
            return JSONResponse({"ok": False, "error": "Decisión de aprobación obligatoria."}, status_code=400)
        try:
            from memory import selfheal
            accepted = selfheal.resolve_approval(action.target_id, action.approved)
        except Exception:
            log.exception("No se pudo resolver permiso de autocorrección")
            return JSONResponse({"ok": False, "error": "El Permission Manager no pudo completar la decisión."}, status_code=503)
        if accepted:
            brain.emit({"type": "confirmation_resolved", "action_id": action.target_id,
                        "approved": action.approved, "ok": True})
        return JSONResponse({"ok": accepted, "error": None if accepted else "La aprobación ya no está pendiente."},
                            status_code=200 if accepted else 404)
    if action.kind == "cancel_scheduled_task":
        task_id = action.target_id
        if len(task_id) != 12 or any(char not in "0123456789abcdef" for char in task_id):
            return JSONResponse({"ok": False, "error": "Identificador de tarea no válido."}, status_code=400)
        try:
            from tools.auto_tools import _load_tasks, _save_tasks
            with _SCHEDULE_LOCK:
                current = _load_tasks()
                selected = next((task for task in current if isinstance(task, dict) and hashlib.sha256(
                    f"{str(task.get('name', 'tarea'))[:80]}:{_safe_float(task.get('run_at'))}".encode("utf-8")
                ).hexdigest()[:12] == task_id), None)
                if not selected:
                    return JSONResponse({"ok": False, "error": "La tarea ya no está pendiente en el planificador."}, status_code=404)
                left = [task for task in current if task is not selected]
                _save_tasks(left)
                if len(left) == len(current):
                    return JSONResponse({"ok": False, "error": "La tarea ya se ejecutó o fue cancelada."}, status_code=409)
        except Exception:
            log.exception("No se pudo cancelar la tarea programada")
            return JSONResponse({"ok": False, "error": "No se pudo guardar la cancelación de la tarea."}, status_code=503)
        brain.emit({"type": "action_status", "label": "Tarea programada cancelada", "state": "cancelled"})
        return JSONResponse({"ok": True, "message": "Tarea programada cancelada."})
    return JSONResponse({"ok": False, "error": "Acción no admitida por las APIs actuales de JARVIS."}, status_code=422)
