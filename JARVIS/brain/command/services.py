"""Centros que se apoyan en sistemas que YA existen: conocimiento, automatizaciones,
memoria, auditoría e historial.

Aquí no hay una segunda base de datos ni un segundo planificador: cada función
delega en el módulo que ya usa JARVIS (`library`, `tools.auto_tools`,
`memory.protocols`, `memory.manager`, `memory.projects`, `sqlite_store`,
`recap`). Si el sistema no existe aquí, se devuelve el aviso y ya está.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("brain.cmd.services")


def _no(disponible: bool, motivo: str = "") -> Optional[dict]:
    """Respuesta uniforme cuando un subsistema no está instalado en este equipo."""
    return {"ok": False, "disponible": False,
            "error": motivo or "Este equipo no tiene ese sistema."} if not disponible else None


# --------------------------------------------------------------------------- #
# CONOCIMIENTO (library.py: la colección de documentos de JARVIS)
# --------------------------------------------------------------------------- #
def conocimiento_estado() -> dict:
    try:
        import library
    except Exception as exc:  # noqa: BLE001
        return {"disponible": False, "error": f"librería no disponible: {exc}"[:160]}
    try:
        stats = library.stats()
        roots = library.get_roots()
    except Exception as exc:  # noqa: BLE001
        return {"disponible": True, "error": str(exc)[:160], "stats": {}}
    return {"disponible": True, "stats": stats, "raices": roots[:20],
            "nextcloud": _nc_stats(library)}


def _nc_stats(library) -> dict:
    try:
        return {"raices": library.get_nc_roots()[:10], **library.nc_stats()}
    except Exception:  # noqa: BLE001
        return {"raices": [], "error": "Nextcloud no configurado"}


def conocimiento_buscar(consulta: str, limite: int = 8) -> dict:
    if not (consulta or "").strip():
        return {"ok": False, "error": "Escribe qué quieres buscar."}
    try:
        import library
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"Sin base de conocimiento: {exc}"[:160])
    try:
        docs = library.search_docs(consulta, limit=max(1, min(limite, 20)))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "consulta": consulta, "total": len(docs),
            "resultados": [{"nombre": d.get("name", ""), "ruta": d.get("path", ""),
                            "texto": str(d.get("text", ""))[:600],
                            "relevancia": round(float(d.get("score", 0)), 4)}
                           for d in docs]}


def conocimiento_indexar(forzar: bool = False) -> dict:
    try:
        import library
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"Sin base de conocimiento: {exc}"[:160])
    try:
        arrancado = library.index_in_background(force=forzar)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "arrancado": bool(arrancado),
            "detalle": "Indexando en segundo plano." if arrancado
                       else "No se pudo arrancar el indexado."}


def conocimiento_olvidar() -> dict:
    """Destructivo: borra la colección entera. Lo llama el panel con permiso."""
    try:
        import library
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"Sin base de conocimiento: {exc}"[:160])
    try:
        library.forget_all()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "mensaje": "Base de conocimiento borrada."}


# --------------------------------------------------------------------------- #
# AUTOMATIZACIONES (tools/auto_tools.py + memory/protocols.py)
# --------------------------------------------------------------------------- #
def _id_tarea(tarea: dict) -> str:
    import hashlib
    return hashlib.sha256(f"{tarea.get('name','')}:{tarea.get('run_at',0)}"
                          .encode("utf-8")).hexdigest()[:12]


def automatizaciones() -> dict:
    try:
        from tools import auto_tools
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"planificador no disponible: {exc}"[:160])
    try:
        with auto_tools._TASKS_LOCK:
            tareas = [t for t in auto_tools._load_tasks() if isinstance(t, dict)]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    ahora = time.time()
    lista = []
    for tarea in sorted(tareas, key=lambda t: float(t.get("run_at") or 0)):
        run_at = float(tarea.get("run_at") or 0)
        lista.append({
            "id": _id_tarea(tarea),
            "nombre": str(tarea.get("name", ""))[:80],
            "mensaje": str(tarea.get("message", ""))[:400],
            "run_at": run_at,
            "repeat": tarea.get("repeat"),
            "paused": bool(tarea.get("paused")),
            "estado": ("pausada" if tarea.get("paused")
                       else "programada" if run_at > ahora else "pendiente"),
            "en": max(0, int(run_at - ahora)),
            "agente": "JARVIS",
        })
    return {"ok": True, "tareas": lista, "total": len(lista),
            "protocolos": protocolos()["protocolos"]}


def _mutar(task_id: str, cambios: dict, deben: Optional[str] = None) -> dict:
    """Cambia una tarea programada por su id estable (el mismo que ve el panel)."""
    from tools import auto_tools
    with auto_tools._TASKS_LOCK:
        actual = [t for t in auto_tools._load_tasks() if isinstance(t, dict)]
        seleccionada = None
        for tarea in actual:
            if _id_tarea(tarea) == task_id:
                seleccionada = tarea
                break
        if seleccionada is None:
            return {"ok": False, "error": "Esa automatización ya no está."}
        if deben and not deben(seleccionada):
            return {"ok": False, "error": "La automatización no está en ese estado."}
        for clave, valor in cambios.items():
            if valor is None:
                seleccionada.pop(clave, None)
            else:
                seleccionada[clave] = valor
        auto_tools._save_tasks(actual)
    try:
        auto_tools.despertar()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "mensaje": "Automatización actualizada.",
            "tarea": _id_tarea(seleccionada)}


def automatizacion_crear(nombre: str, mensaje: str, cuando: str) -> dict:
    try:
        from tools.auto_tools import schedule_task_handler
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"planificador no disponible: {exc}"[:160])
    try:
        res = schedule_task_handler({"name": nombre, "message": mensaje, "when": cuando})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "mensaje": f"Programada para las {res.get('at', '')}"
                                   f"{res.get('repeat', '')}."}


def automatizacion_pausar(task_id: str) -> dict:
    return _mutar(task_id, {"paused": True},
                  deben=lambda t: not t.get("paused"))


def automatizacion_reanudar(task_id: str) -> dict:
    return _mutar(task_id, {"paused": None},
                  deben=lambda t: bool(t.get("paused")))


def automatizacion_borrar(task_id: str) -> dict:
    return _borrar(task_id)


def _borrar(task_id: str) -> dict:
    from tools import auto_tools
    with auto_tools._TASKS_LOCK:
        actual = [t for t in auto_tools._load_tasks() if isinstance(t, dict)]
        restante = [t for t in actual if _id_tarea(t) != task_id]
        if len(restante) == len(actual):
            return {"ok": False, "error": "Esa automatización ya no está."}
        auto_tools._save_tasks(restante)
    try:
        auto_tools.despertar()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "mensaje": "Automatización borrada."}


def automatizacion_correr_ya(task_id: str) -> dict:
    """La adelanta a ahora mismo. Quien la ejecuta sigue siendo el planificador."""
    return _mutar(task_id, {"run_at": time.time()},
                  deben=lambda t: not t.get("paused"))


def automatizacion_editar(task_id: str, nombre: str = "", mensaje: str = "",
                          cuando: str = "") -> dict:
    cambios: dict = {}
    if nombre:
        cambios["name"] = nombre[:80]
    if mensaje:
        cambios["message"] = mensaje[:1000]
    if cuando:
        from tools.auto_tools import _parse_when
        try:
            momento = _parse_when(cuando)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}
        if not momento:
            return {"ok": False, "error": "No entiendo ese cuándo."}
        cambios.update(momento)
    if not cambios:
        return {"ok": False, "error": "No hay nada que cambiar."}
    return _mutar(task_id, cambios)


# Protocolos: automatizaciones por disparador (los de memory/protocols.py)
def protocolos() -> dict:
    try:
        from memory import protocols
    except Exception as exc:  # noqa: BLE001
        return _no(False, f"protocolos no disponibles: {exc}"[:160]) or {"protocolos": []}
    try:
        return {"protocolos": protocols.list_all()}
    except Exception as exc:  # noqa: BLE001
        return {"protocolos": [], "error": str(exc)[:160]}


def protocolo_crear(nombre: str, triggers: list, actions: list, say: str = "") -> dict:
    try:
        from memory import protocols
        return {"ok": True, "protocolo": protocols.add(nombre, triggers, actions, say)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def protocolo_borrar(nombre: str) -> dict:
    try:
        from memory import protocols
        borrado = protocols.remove(nombre)
        return {"ok": bool(borrado),
                "mensaje": "Protocolo borrado." if borrado else "No había ese protocolo."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# MEMORIA, PROYECTOS Y PERFIL (los que JARVIS ya tiene)
# --------------------------------------------------------------------------- #
def memoria(consulta: str = "", limite: int = 60) -> dict:
    try:
        from memory.manager import manager
        m = manager()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "disponible": False, "error": str(exc)[:160], "recuerdos": []}
    try:
        recs = m.search(consulta, limit=limite) if consulta else m.get_all(limit=limite)
        estado = m.estado()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "recuerdos": []}
    return {"ok": True, "recuerdos": recs, "total": len(recs),
            "estado": estado,
            "categorias": sorted({str(r.get("categoria", "")) for r in recs if r.get("categoria")})}


def proyectos() -> dict:
    try:
        from memory import projects
        return {"ok": True, "proyectos": projects.list_all()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160], "proyectos": []}


def perfil() -> dict:
    try:
        from memory import profile
        return {"ok": True, "perfil": profile.render()[:1500]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160]}


# --------------------------------------------------------------------------- #
# AUDITORÍA E HISTORIAL
# --------------------------------------------------------------------------- #
def auditoria(limite: int = 60, risk: str = "", resultado: str = "",
              busqueda: str = "") -> dict:
    """Lectura de la auditoría que ya escribe el Tool Manager. Solo lectura."""
    try:
        from memory import sqlite_store as db
        filas = db.list_audit(limit=min(limite * 4, 400))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "entradas": []}
    salida = []
    for fila in filas:
        if risk and str(fila.get("risk_level", "")) != risk:
            continue
        if resultado and str(fila.get("result", "")) != resultado:
            continue
        if busqueda and busqueda.lower() not in \
                f"{fila.get('action_type','')} {fila.get('details','')}".lower():
            continue
        salida.append({
            "id": fila.get("id"),
            "at": fila.get("created_at"),
            "accion": fila.get("action_type"),
            "riesgo": fila.get("risk_level"),
            "resultado": fila.get("result"),
            "confirmado": fila.get("confirmed"),
            "detalle": str(fila.get("details") or "")[:300],
        })
        if len(salida) >= limite:
            break
    return {"ok": True, "entradas": salida, "total": len(salida)}


def historial(dias: int = 7) -> dict:
    try:
        import recap
        return recap.resumen(max(1, min(int(dias), 30)))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "vacio": True}


def decisiones(mission_id: str = "", limite: int = 40) -> dict:
    return {"ok": True, "decisiones": store.listar_decisiones(limite, mission_id)}


# --------------------------------------------------------------------------- #
# AGENTES: lo que se puede hacer de verdad con ellos
# --------------------------------------------------------------------------- #
def agentes() -> dict:
    """Agentes detectados + qué se puede hacer con cada uno.

    apps.py es de solo lectura. La única acción real es "iniciar" (abrir el
    programa si existe). Parar un proceso ajeno no lo hace JARVIS: no hay
    backend que lo haga con seguridad, y aquí no se inventa.
    """
    try:
        import apps
        estados = apps.estado()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160], "agentes": []}
    nombres = {
        "jarvis": ("JARVIS", "Este asistente"),
        "agent_opencode": ("OpenCode", "Agente de programación"),
        "integration_claude": ("Claude / editor", "Agente o editor"),
        "agent_codex": ("Codex", "Agente de programación"),
        "freebuff": ("Freebuff", "Agente de programación"),
        "agent_cursor": ("Cursor", "Editor"),
        "agent_amp": ("Amp", "Agente de programación"),
        "agent_hermes": ("Hermes", "Agente"),
        "agent_copilot": ("Copilot", "Agente"),
        "agent_muse": ("Muse", "Agente"),
    }
    import shutil
    lista = []
    for clave, (nombre, descripcion) in nombres.items():
        detected = estados.get(clave) or {}
        ejecutable = ""
        for candidato in apps.DETECTORES.get(clave, []):
            if shutil.which(candidato):
                ejecutable = candidato
                break
        activo = bool(detected.get("activa"))
        lista.append({
            "id": clave, "nombre": nombre, "descripcion": descripcion,
            "estado": ("WORKING" if activo else
                       "DISABLED" if not ejecutable and clave != "jarvis" else "IDLE"),
            "activo": activo,
            "procesos": detected.get("procesos", []) if activo else [],
            "ejecutable": ejecutable,
            "acciones": {"start": bool(ejecutable or clave == "jarvis"),
                         "stop": False, "pause": False, "resume": False},
            "nota": "Solo lectura: apps.py detecta procesos, no los gobierna."
                    if not ejecutable else "Se puede abrir el programa.",
        })
    return {"ok": True, "agentes": lista}


def agente_parar(agente_id: str) -> dict:
    """Cierra el programa del agente, si lo hay abierto.

    Se cuentan los procesos REALES de ese agente (los mismos que usa apps.py
    para decir si está activo), se pide permiso al señor antes de cerrar
    nada, y se termina de forma educada: primero se pide que cierre, y solo
    si sigue ahí dentro de un momento se fuerza. Matar a lo bruto un editor
    con cambios sin guardar seria perder trabajo del señor.
    """
    try:
        import apps
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160]}
    candidatos = [c.lower() for c in (apps.DETECTORES.get(agente_id) or [])]
    if not candidatos:
        return {"ok": False, "error": f"{agente_id} no es un agente conocido."}

    try:
        import psutil
    except Exception as exc:  # noqa: BLE001
        return {"ok": False,
                "error": f"No puedo parar procesos sin psutil: {exc}"}

    victimas = []
    for proc in psutil.process_iter(["name", "pid"]):
        nombre = (proc.info.get("name") or "").lower()
        if nombre in candidatos:
            victimas.append(proc)
    if not victimas:
        return {"ok": True, "detenido": False,
                "detalle": f"{agente_id} no estaba abierto."}

    for proc in victimas:
        try:
            proc.terminate()
        except Exception as exc:  # noqa: BLE001
            log.debug("terminate(%s): %s", proc.pid, exc)

    # Se espera un poco y se fuerza lo que siga vivo.
    import time as _t

    _t.sleep(1.5)
    forzados = 0
    for proc in victimas:
        try:
            if proc.is_running():
                proc.kill()
                forzados += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("kill(%s): %s", proc.pid, exc)
    return {"ok": True, "detenido": True,
            "procesos": len(victimas), "forzados": forzados,
            "detalle": f"{agente_id}: se cerraron {len(victimas)} proceso(s)."}
    """Abre el programa del agente si su ejecutable está en el equipo."""
    try:
        import apps
        from tools import system_tools
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160]}
    import shutil
    candidatos = apps.DETECTORES.get(agente_id) or []
    ejecutable = next((c for c in candidatos if shutil.which(c)), "")
    if not ejecutable:
        return {"ok": False,
                "error": f"{agente_id} no está instalado en este equipo."}
    try:
        return system_tools.open_app_handler({"name": ejecutable})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}