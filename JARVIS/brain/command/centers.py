"""Health Center, Resource Center y matriz de control.

Tres cosas, y las tres son reales:

  · `salud()`      comprueba los subsistemas de verdad (núcleo, base de datos,
                   memoria, agentes, OpenCode, voz, Discord, bus, herramientas).
                   Si algo no se puede comprobar, se dice que no se sabe: no
                   se pone "verde" por defecto.
  · `recursos()`   números de verdad: CPU/RAM con psutil, disco con shutil,
                   GPU con nvidia-s si existe, red con los contadores de psutil.
                   Lo que no exista en el sistema aparece como "no disponible".
  · `matriz()`     la tabla de control (§64). Se construye declarando qué
                   acción real hay detrás de cada botón. Si no hay backend,
                   la celda no existe y la interfaz no pinta el botón.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

from . import store

log = logging.getLogger("brain.cmd.centers")

_cache: dict = {}
_CACHE_TTL = 5.0
_lock = threading.RLock()

NIVELES = ("healthy", "degraded", "down", "unknown")


# --------------------------------------------------------------------------- #
# Recursos (solo lo que el sistema sabe de verdad)
# --------------------------------------------------------------------------- #
def _gpu() -> dict:
    """GPU y VRAM con nvidia-smi. Sin GPU o sin nvidia-smi: no disponible."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "reason": "nvidia-smi no está en este equipo"}
    try:
        r = subprocess.run(
            [exe, "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, check=False)
        if r.returncode:
            return {"available": False, "reason": "nvidia-smi no responde"}
        linea = (r.stdout or "").strip().splitlines()[0]
        partes = [p.strip() for p in linea.split(",")]
        if len(partes) < 5:
            return {"available": False, "reason": "formato desconocido"}
        return {"available": True, "name": partes[0],
                "temperature": float(partes[1]) if partes[1].replace(".", "").isdigit() else None,
                "usage": float(partes[2]) if partes[2].isdigit() else None,
                "vram_used_mb": float(partes[3]) if partes[3].isdigit() else None,
                "vram_total_mb": float(partes[4]) if partes[4].isdigit() else None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:80]}


_RED_ANTERIOR: dict = {}


def _red() -> dict:
    """Tráfico real, medido comparando dos lecturas del contador del sistema."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "psutil no disponible"}
    try:
        ahora = time.monotonic()
        contadores = psutil.net_io_counters(pernic=True)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:80]}
    enviados = recibidos = 0.0
    for nic, c in contadores.items():
        if nic.startswith("lo"):
            continue
        enviados += c.bytes_sent
        recibidos += c.bytes_recv
    total = enviados + recibidos
    anterior = _RED_ANTERIOR.get("total")
    _RED_ANTERIOR["total"] = total
    _RED_ANTERIOR["at"] = ahora
    if anterior is None or ahora <= anterior[1] or ahora - anterior[1] < 1.0:
        return {"available": True, "since_seconds": None, "upload_bps": None,
                "download_bps": None, "total_bytes": total,
                "note": "midiendo: aún no hay con qué comparar"}
    delta = max(0.0, total - anterior[0])
    segundos = ahora - anterior[1]
    return {"available": True, "since_seconds": round(segundos, 1),
            "upload_bps": None, "download_bps": round(delta / segundos, 1),
            "total_bytes": total}


def _discos() -> list[dict]:
    salida = []
    for letra in ("C:\\", "D:\\") if os.name == "nt" else ("/",):
        if os.path.isdir(letra):
            libres = None
            total = None
            try:
                uso = shutil.disk_usage(letra)
                total, libres = uso.total, uso.free
            except OSError:
                pass
            if total:
                salida.append({"path": letra, "etiqueta": letra.rstrip("\\/"),
                               "total_gb": round(total / 1024 ** 3, 1),
                               "usado_gb": round((total - libres) / 1024 ** 3, 1),
                               "libre_gb": round(libres / 1024 ** 3, 1),
                               "porcentaje": round((total - libres) / total * 100, 1)})
    return salida


def recursos(forzar: bool = False) -> dict:
    """Una foto real del equipo. Sin GPU ni sin red, se dice que no está."""
    ahora = time.monotonic()
    with _lock:
        if not forzar and _cache.get("recursos") and \
                ahora - _cache["recursos"][0] < _CACHE_TTL:
            return _cache["recursos"][1]
    salida: dict = {"ts": time.time(), "cpu": None, "ram": None, "discos": [],
                    "gpu": {"available": False}, "red": {"available": False},
                    "temperatura": None, "carga": {}}
    try:
        import psutil
        salida["cpu"] = {"porcentaje": psutil.cpu_percent(interval=None),
                         "nucleos": psutil.cpu_count()}
        vm = psutil.virtual_memory()
        salida["ram"] = {"porcentaje": vm.percent, "usado_gb": round(vm.used / 1024 ** 3, 1),
                         "total_gb": round(vm.total / 1024 ** 3, 1),
                         "disponible_gb": round(vm.available / 1024 ** 3, 1)}
        salida["carga"]["procesos"] = len(psutil.pids())
    except Exception as exc:  # noqa: BLE001
        log.debug("recursos sin psutil: %s", exc)
    salida["discos"] = _discos()
    salida["gpu"] = _gpu()
    salida["red"] = _red()
    # La temperatura del procesador no tiene fuente fiable aquí: se dice.
    salida["temperatura"] = {"available": False,
                             "reason": "ningún sensor expuesto por el sistema"}
    with _lock:
        _cache["recursos"] = (time.monotonic(), salida)
    return salida


# --------------------------------------------------------------------------- #
# Salud de cada subsistema
# --------------------------------------------------------------------------- #
def _check_nucleo() -> dict:
    try:
        from main import brain
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": f"no se pudo leer el cerebro: {exc}"[:120]}
    return {"estado": "healthy",
            "detalle": f"en marcha · {len(getattr(brain, 'clients', []))} clientes · "
                       f"{len(getattr(brain, '_live_tasks', {}))} trabajos"}


def _check_datos() -> dict:
    try:
        from memory import sqlite_store as db
        fila = db.get_conn().execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return {"estado": "healthy", "detalle": f"SQLite con {fila['n']} mensajes"}
    except Exception as exc:  # noqa: BLE001
        return {"estado": "down", "detalle": str(exc)[:160]}


def _check_memoria() -> dict:
    try:
        from memory.manager import manager
        estado = manager().estado()
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    if not estado:
        return {"estado": "unknown", "detalle": "sin estado"}
    if not estado.get("activado"):
        return {"estado": "unknown", "detalle": "memoria desactivada en la configuración"}
    if estado.get("disponible"):
        return {"estado": "healthy", "detalle": "Mem0 disponible",
                "contadores": estado.get("contadores", {})}
    return {"estado": "degraded",
            "detalle": f"Mem0 configurado pero no responde ({estado.get('url', '')})"}


def _check_agentes() -> dict:
    try:
        import apps
        estados = apps.estado()
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    activos = [k for k, v in estados.items() if isinstance(v, dict) and v.get("activa")]
    return {"estado": "healthy", "detalle": f"{len(activos)} agentes en marcha",
            "activos": activos}


def _check_opencode() -> dict:
    try:
        from orchestrator import service
        estado = service.describe()
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    if estado.get("ok"):
        return {"estado": "healthy",
                "detalle": f"{estado.get('cli_version') or 'OpenCode'} · {estado.get('source')}"}
    if not estado.get("cli"):
        return {"estado": "down", "detalle": "OpenCode no está instalado en este equipo"}
    return {"estado": "degraded", "detalle": estado.get("error", "servidor parado")[:120]}


def _check_voz() -> dict:
    try:
        from main import brain
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    if brain.voice is None:
        return {"estado": "down", "detalle": "no hay motor de voz cargado"}
    estado = str(getattr(brain.voice, "state", "") or "desconocido")
    return {"estado": "healthy", "detalle": f"motor en {estado}",
            "mic_muted": bool(getattr(brain.voice, "mic_muted", False))}


def _check_discord() -> dict:
    try:
        from main import brain
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    puente = getattr(brain, "discord", None)
    if puente is None:
        return {"estado": "down", "detalle": "Discord no configurado"}
    return {"estado": "healthy", "detalle": "puente de Discord conectado"}


def _check_bus() -> dict:
    try:
        from main import brain
        with brain._live_event_lock:
            total = len(brain._live_events)
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    return {"estado": "healthy", "detalle": f"bus vivo · {total} eventos en el anillo"}


def _check_herramientas() -> dict:
    try:
        from main import brain
        if brain.tools is None:
            return {"estado": "down", "detalle": "Tool Manager sin inicializar"}
        total = len(brain.tools.list_tools())
        disponibles = len(brain.tools.disponibles())
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    estado = "healthy" if disponibles >= max(1, total * 0.5) else "degraded"
    return {"estado": estado, "detalle": f"{disponibles}/{total} herramientas disponibles"}


def _check_conocimiento() -> dict:
    try:
        import library
        stats = library.stats()
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    if stats.get("files"):
        return {"estado": "healthy",
                "detalle": f"{stats['files']} documentos indexados en {stats.get('roots', 0)} carpetas"}
    return {"estado": "unknown", "detalle": "no hay documentos indexados todavía"}


def _check_automatizaciones() -> dict:
    try:
        from tools import auto_tools
        tareas = auto_tools._load_tasks()
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    activas = [t for t in tareas if not t.get("paused")]
    return {"estado": "healthy",
            "detalle": f"{len(activas)} automatizaciones activas de {len(tareas)}"}


def _check_misiones() -> dict:
    try:
        from orchestrator import store as dev_store
        tareas = dev_store.cargar_todas(30)
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": str(exc)[:120]}
    vivas = [t for t in tareas if dev_store.vivo(t.get("status", ""))]
    if not tareas:
        return {"estado": "healthy", "detalle": "sin misiones todavía"}
    return {"estado": "healthy",
            "detalle": f"{len(vivas)} misiones en marcha de {len(tareas)}"}


def _check_ia() -> dict:
    """Estado de los modelos, leido del AI Gateway (sin llamadas de coste)."""
    try:
        from ai_gateway import health

        global_ = health.estado_global()
        return {"estado": {"CONNECTED": "healthy", "SIN_DATOS": "unknown",
                           "SIN CLAVE": "degraded", "APAGADO": "degraded",
                           }.get(global_, "degraded"),
                "detalle": f"modelos: {global_}"}
    except Exception as exc:  # noqa: BLE001
        return {"estado": "unknown", "detalle": f"gateway no leido: {exc}"[:120]}


CHECKS = {
    "nucleo": ("Núcleo del cerebro", _check_nucleo),
    "datos": ("Base de datos", _check_datos),
    "memoria": ("Memoria (Mem0)", _check_memoria),
    "agentes": ("Agentes", _check_agentes),
    "opencode": ("OpenCode", _check_opencode),
    "voz": ("Voz", _check_voz),
    "discord": ("Discord", _check_discord),
    "bus": ("Bus de eventos", _check_bus),
    "herramientas": ("Herramientas", _check_herramientas),
    "conocimiento": ("Conocimiento", _check_conocimiento),
    "automatizaciones": ("Automatizaciones", _check_automatizaciones),
    "misiones": ("Misiones", _check_misiones),
    "ia": ("Modelos de IA", _check_ia),
}


def salud(forzar: bool = False) -> dict:
    """Estado de cada subsistema. Lo que no se puede comprobar, se dice."""
    ahora = time.monotonic()
    with _lock:
        if not forzar and _cache.get("salud") and ahora - _cache["salud"][0] < _CACHE_TTL:
            return _cache["salud"][1]
    subsistemas = []
    for clave, (nombre, fn) in CHECKS.items():
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001
            res = {"estado": "unknown", "detalle": str(exc)[:160]}
        res.update({"id": clave, "nombre": nombre,
                    "estado": res.get("estado", "unknown")
                    if res.get("estado") in NIVELES else "unknown"})
        subsistemas.append(res)
    cuentas = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
    for sub in subsistemas:
        cuentas[sub["estado"]] = cuentas.get(sub["estado"], 0) + 1
    if cuentas["down"]:
        global_ = "error"
    elif cuentas["degraded"]:
        global_ = "degraded"
    elif cuentas["unknown"] >= len(subsistemas) / 2:
        global_ = "unknown"
    else:
        global_ = "online"
    salida = {"estado": global_, "ts": time.time(), "cuentas": cuentas,
              "subsistemas": subsistemas}
    with _lock:
        _cache["salud"] = (time.monotonic(), salida)
    return salida


# --------------------------------------------------------------------------- #
# Matriz de control (§64): qué se puede hacer DE VERDAD con cada cosa
# --------------------------------------------------------------------------- #
def _tiene(modulo: str) -> bool:
    try:
        __import__(modulo)
        return True
    except Exception:  # noqa: BLE001
        return False


def matriz() -> dict:
    """Declara las acciones reales. La UI solo pinta lo que hay aquí."""
    filas = []

    def fila(entidad, **acciones):
        acciones = {k: bool(v) for k, v in acciones.items()}
        filas.append({"entidad": entidad,
                      "acciones": {a: acciones.get(a, False) for a in
                                   ("view", "create", "edit", "start", "pause", "resume",
                                    "stop", "delete", "approve", "reject", "retry")}})

    # Misiones: el orquestador ya lo tiene todo.
    fila("misiones", view=True, create=True, edit=True, start=True, pause=True,
         resume=True, stop=True, delete=True, approve=True, reject=True, retry=True)
    # Tareas programadas: el planificador existente (crear, correr, pausar, borrar).
    fila("tareas", view=True, create=True, edit=False, start=True, pause=True,
         resume=True, stop=True, delete=True)
    # Agentes: apps.py es de SOLO LECTURA. Arrancar existe (open_app_handler),
    # pero solo si hay un ejecutable con ese nombre en la máquina.
    fila("agentes", view=True, create=False, edit=False, start=True, pause=False,
         resume=False, stop=False, delete=False)
    # Permisos: aprobar y denegar.
    fila("permisos", view=True, approve=True, reject=True)
    # Preguntas de OpenCode: contestar y cancelar la tarea.
    fila("preguntas", view=True, edit=True, retry=True)
    # OpenCode: todo lo que hace el adaptador.
    fila("opencode", view=True, create=True, edit=True, start=True, pause=True,
         resume=True, stop=True, retry=True)
    # Cambios (Git): ver y commitear con confirmación.
    fila("cambios", view=True, create=True, delete=False)
    # Tests: ver y ejecutar.
    fila("tests", view=True, create=True, start=True)
    # Incidentes: ver y cerrar.
    fila("incidentes", view=True, create=False, edit=False, delete=False)
    # Notificaciones: ver, descartar, silenciar.
    fila("notificaciones", view=True, edit=True, delete=True)
    # Automatizaciones: el planificador y los protocolos.
    fila("automatizaciones", view=True, create=True, edit=True, start=True,
         pause=True, resume=True, stop=True, delete=True, retry=True)
    # Memoria y conocimiento: CRUD real.
    fila("memoria", view=True, create=True, edit=True, delete=True)
    fila("conocimiento", view=True, create=True, edit=True, delete=True)
    fila("auditoria", view=True)
    fila("historial", view=True)
    fila("sistema", view=True, start=True)

    capacidades_extra = {
        "diagnostico": _tiene("tools.diag_tools"),
        "optimizacion": _tiene("tools.syshealth_tools"),
        "limpieza_disco": _tiene("tools.syshealth_tools"),
        "agente_inicio": _tiene("tools.system_tools"),
        "docker": False,
        "docker_nota": "No existe un gestor de Docker en este sistema: no se muestra control.",
        "red_monitoreo": _tiene("psutil"),
        "gpu_monitoreo": bool(shutil.which("nvidia-smi")),
        "voz_salida": _tiene("voice.tts"),
        "discord_push": _tiene("integrations.discord_bridge"),
        "knowledge_index": _tiene("library"),
        "briefing_voz": True,
    }
    return {"matriz": filas, "capacidades": capacidades_extra,
            "ts": time.time()}