"""Rutas del panel JARVIS Live; reutiliza el cerebro y sus servicios actuales."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
import time
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from auth import coincide, origen_permitido
from config import BRAIN_DIR
from memory import sqlite_store as db

log = logging.getLogger("brain.live")
router = APIRouter()
LIVE_DIR = Path(__file__).resolve().parent / "live"
_SCHEDULE_LOCK = Lock()


def register(app) -> None:
    """Monta las rutas de JARVIS Live en el FastAPI existente."""
    app.include_router(router)
    # El ciclo de desarrollo con OpenCode cuelga de aquí, con su propio router
    # y su propio candado: si falla, JARVIS Live sigue funcionando igual.
    try:
        from orchestrator import routes as dev_routes
        app.include_router(dev_routes.router)
        log.info("Ciclo de desarrollo con OpenCode montado en /api/dev")
    except Exception:
        log.exception("no pude montar el ciclo de desarrollo con OpenCode")
    try:
        from command import routes as cmd_routes
        app.include_router(cmd_routes.router)
        log.info("Centro de mando montado en /api/cmd")
    except Exception:
        log.exception("no pude montar el centro de mando")
    try:
        from ai_gateway import routes as ai_routes
        app.include_router(ai_routes.router)
        log.info("AI Gateway montado en /api/ai")
    except Exception:
        log.exception("no pude montar el AI Gateway")
    log.info("Centro de operaciones JARVIS Live montado en /live")
    _arrancar_gestores()


def _arrancar_gestores() -> None:
    """Engancha Agent Manager y Resource Manager entre sí y con el bus.

    Se hace aquí porque este es el sitio donde ya se montan el resto de
    sistemas del centro de mando. El enganche es corto y, si algo falla, JARVIS
    sigue arrancando: un gestor que no está es un gestor que no decide, no un
    cerebro que no arranca.
    """
    try:
        import agent_manager as _agentes
        import resource_manager as _recursos

        try:
            from main import brain

            _agentes.conectar_bus(brain.emit)
            _recursos.conectar(brain.emit)
        except Exception as exc:  # noqa: BLE001
            log.debug("gestores sin bus por ahora: %s", exc)
            _agentes.conectar_bus(lambda _e: None)
            _recursos.conectar(lambda _e: None)

        # El Resource Manager puede BAJAR el tope de trabajos a la vez, pero
        # nunca subirlo por encima del que puso el señor.
        _agentes.conectar_limite(_recursos.limite_concurrencia)

        # Que se sepa qué se quedó a medias al reiniciar.
        recup = _agentes.recuperar_al_arrancar()
        if recup.get("marcados_atascados"):
            log.info("Trabajos de agentes marcados como atascados al arrancar: %s",
                     recup["marcados_atascados"])
        _recursos.revisar(forzar=True)
        log.info("Agent Manager y Resource Manager en marcha (nivel %s)",
                 _recursos.nivel())
    except Exception:  # noqa: BLE001
        log.exception("no pude arrancar los gestores; JARVIS sigue sin ellos")


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
            "state": "pausada" if task.get("paused") else
                     ("programada" if run_at > time.time() else "pendiente de ejecución"),
            "paused": bool(task.get("paused")),
            "run_at": run_at,
            "repeat": int(repeat) if isinstance(repeat, (int, float)) and repeat > 0 else None,
            "agent": "JARVIS",
        })
    return result


def _auditar(accion: str, detalle: str) -> None:
    """Deja rastro de las ordenes del panel: quien programa que queda escrito.

    Las acciones en si son de bajo riesgo (crear una fila, despertad al
    planificador); el permiso de verdad se decide al ejecutarse, con el
    RiskEngine de siempre.
    """
    try:
        db.add_audit("live:" + accion, {"origen": "panel"},
                     "low", True, "success", detalle[:300])
    except Exception as e:
        log.debug("auditoria live: %s", e)


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
    # live.js ya manda la cabecera en sus fetch, pero se inyecta tambien el
    # envoltorio por si algun script nuevo de la pagina llama a la API sin
    # usarla: es el mismo criterio que el resto de paginas del brain.
    from main import _inyecta_token

    return HTMLResponse(_inyecta_token(html), headers={"Cache-Control": "no-store"})
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
        for t in live_tasks:
            t.pop("message", None)
        recent = [dict(task) for task in brain._live_done]
    health = {
        "online": True,
        "active_clients": len(brain.clients),
        "uptime_seconds": max(0, int(time.time() - brain._live_started_at)),
        "voice": str(getattr(brain.voice, "state", "desconectada") or "desconectada"),
        "voice_available": brain.voice is not None,
        "discord": bool(getattr(brain, "discord", None)),
        "active_tasks": live_tasks,
        "recent_tasks": recent,
        "scheduled_tasks": _scheduled_tasks(),
        "approvals": _approval_list(brain),
        "agents": _agent_states(),
        "resources": _resource_state(),
        "changes": await asyncio.to_thread(_git_changes),
        "events": await asyncio.to_thread(lambda: _read_recent_events(brain)),
        "audit_events": _audit_events(),
    }
    return JSONResponse(health, headers={"Cache-Control": "no-store"})


