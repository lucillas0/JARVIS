# -*- coding: utf-8 -*-
"""Automatización: flujos (descargar/mover/listar), GPU, Steam y tareas
programadas (sección 14). Las tareas viven en DATA_DIR/tasks.json y las
ejecuta un hilo del brain con el mensaje guardado."""
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("tools.auto")

_TASKS_FILE = None
_sched_started = False


def _tasks_path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "tasks.json")


def _load_tasks() -> list:
    try:
        with open(_tasks_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_tasks(tasks: list) -> None:
    try:
        tmp = _tasks_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False)
        os.replace(tmp, _tasks_path())
    except Exception as e:
        log.debug("tasks save: %s", e)


def _parse_when(spec: str):
    """'HH:MM' (hoy/mañana) | 'en Nh' | 'en Nm' | 'cada Nh'/'cada Nm' -> dict."""
    s = (spec or "").strip().lower()
    now = time.time()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        import datetime as _dt
        t = _dt.datetime.now().replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                       second=0, microsecond=0)
        if t.timestamp() <= now:
            t += _dt.timedelta(days=1)
        return {"run_at": t.timestamp(), "repeat": None}
    m = re.fullmatch(r"en\s+(\d+)\s*(h|hora|horas|m|min|minutos?)", s)
    if m:
        secs = int(m.group(1)) * (3600 if m.group(2).startswith("h") else 60)
        return {"run_at": now + secs, "repeat": None}
    m = re.fullmatch(r"cada\s+(\d+)\s*(h|hora|horas|m|min|minutos?)", s)
    if m:
        secs = int(m.group(1)) * (3600 if m.group(2).startswith("h") else 60)
        return {"run_at": now + secs, "repeat": secs}
    return None


def download_file_handler(params: dict) -> dict:
    import urllib.request
    url = (params.get("url", "") or "").strip()
    if not url.startswith("http"):
        raise ValueError("URL inválida.")
    name = (params.get("name", "") or "").strip().strip('"') or \
        url.split("?")[0].rstrip("/").split("/")[-1] or "descarga"
    name = re.sub(r"[^a-zA-Z0-9._\- áéíóúñü]", "_", os.path.basename(name))[:120]
    from config import DATA_DIR
    d = os.path.join(str(DATA_DIR), "downloads")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS/1.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        while True:
            ch = r.read(1024 * 1024)
            if not ch:
                break
            f.write(ch)
            total += len(ch)
            if total > 500 * 1024 * 1024:
                raise ValueError("Más de 500MB: abortado.")
    return {"path": path, "bytes": total}


def move_file_handler(params: dict) -> dict:
    import shutil
    src = os.path.abspath(os.path.expandvars((params.get("src", "") or "").strip().strip('"')))
    dst = (params.get("dst", "") or "").strip().strip('"')
    if not src or not os.path.exists(src):
        raise ValueError(f"No existe: {src}")
    dst = os.path.abspath(os.path.expandvars(dst))
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return {"from": src, "to": dst}


def list_dir_handler(params: dict) -> dict:
    path = os.path.abspath(os.path.expandvars((params.get("path", "") or "").strip().strip('"') or "."))
    if not os.path.isdir(path):
        raise ValueError(f"No es carpeta: {path}")
    out = []
    for e in sorted(os.listdir(path))[:60]:
        fp = os.path.join(path, e)
        out.append(("d " if os.path.isdir(fp) else "f ") + e)
    return {"path": path, "entries": out}


def gpu_status_handler(params: dict) -> dict:
    import shutil
    import subprocess
    if not shutil.which("nvidia-smi"):
        return {"gpu": "sin nvidia-smi"}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"gpu": (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 else "?"}
    except Exception as e:
        return {"gpu": f"error: {e}"[:150]}


_GAMES = {"cs2": "730", "csgo": "730", "counter": "730", "counter strike": "730",
          "dota": "570", "apex": "1172470", "pubg": "578080", "rust": "252490",
          "gta v": "271590", "gta": "271590", "elden ring": "1245620",
          "minecraft": "mc", "fortnite": "fn"}


def launch_game_handler(params: dict) -> dict:
    import webbrowser
    name = (params.get("game", "") or "").strip().lower()
    appid = None
    for k, v in _GAMES.items():
        if k in name:
            appid = v
            break
    if appid in ("mc", "fn"):
        raise RuntimeError(f"{name} no va por Steam: ábrelo a mano, señor.")
    if not appid:
        raise ValueError(f"No conozco ese juego. Sé: {', '.join(sorted(set(_GAMES)))}.")
    try:
        webbrowser.open(f"steam://run/{appid}")
    except Exception as e:
        raise RuntimeError(f"No pude lanzar Steam: {e}")
    return {"game": name, "appid": appid, "launched": True}


def schedule_task_handler(params: dict) -> dict:
    name = (params.get("name", "") or "").strip()[:60] or "tarea"
    message = (params.get("message", "") or "").strip()
    when = _parse_when(params.get("when", "") or "")
    if not message:
        raise ValueError("Falta el mensaje (qué debe hacer).")
    if not when:
        raise ValueError("Cuándo: 'HH:MM', 'en 2h', 'en 30m', 'cada 1h'.")
    tasks = [t for t in _load_tasks() if t.get("name") != name]
    tasks.append({"name": name, "message": message,
                  "run_at": when["run_at"], "repeat": when["repeat"]})
    _save_tasks(tasks)
    import datetime as _dt
    eta = _dt.datetime.fromtimestamp(when["run_at"]).strftime("%H:%M")
    rep = f", se repite cada {when['repeat'] // 60} min" if when["repeat"] else ""
    return {"name": name, "at": eta, "repeat": rep}


def list_tasks_handler(params: dict) -> dict:
    tasks = _load_tasks()
    import datetime as _dt
    out = []
    for t in tasks:
        eta = _dt.datetime.fromtimestamp(t.get("run_at", 0)).strftime("%d %H:%M")
        out.append(f"{t.get('name')}: «{t.get('message', '')[:60]}» -> {eta}")
    return {"tasks": out}


def cancel_task_handler(params: dict) -> dict:
    name = (params.get("name", "") or "").strip()
    tasks = _load_tasks()
    left = [t for t in tasks if t.get("name") != name] if name else []
    _save_tasks(left)
    return {"cancelled": name or "todas", "remaining": len(left)}


def notify_phone_handler(params: dict) -> dict:
    """Aviso push al móvil (ntfy.sh, prioridad urgente + sonido de teléfono).
    El móvil necesita la app ntfy suscrita al tema. Sin claves ni cuentas."""
    import urllib.request
    from config import Config
    message = (params.get("message", "") or "").strip()[:500]
    if not message:
        raise ValueError("Falta el mensaje.")
    topic = (getattr(Config, "NTIFY_TOPIC", "") or "").strip()
    if not topic:
        owner = (getattr(Config, "DISCORD_OWNER_ID", "") or "").strip()
        if not owner:
            raise RuntimeError("Sin tema de avisos configurado.")
        topic = f"jarvis-{owner}"
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic, data=message.encode("utf-8"),
        headers={"Title": "JARVIS", "Priority": "urgent", "Tags": "telephone"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError(f"ntfy devolvió {r.status}")
    except Exception as e:
        raise RuntimeError(f"No pude avisar al móvil: {e}")
    return {"topic": topic, "sent": True}


def start_scheduler(handle_fn, emit_fn=None) -> bool:
    """Hilo daemon: ejecuta tareas vencidas con handle_fn(mensaje)."""
    global _sched_started
    if _sched_started:
        return True
    _sched_started = True

    def _loop():
        while True:
            try:
                now = time.time()
                tasks = _load_tasks()
                changed = False
                for t in list(tasks):
                    if t.get("run_at", 0) <= now:
                        try:
                            handle_fn(t.get("message", ""))
                        except Exception as e:
                            log.debug("task run: %s", e)
                        if t.get("repeat"):
                            t["run_at"] = now + t["repeat"]
                            changed = True
                        else:
                            tasks.remove(t)
                            changed = True
                            if emit_fn:
                                try:
                                    emit_fn({"type": "assistant_reply",
                                             "text": f"Tarea «{t.get('name')}» ejecutada, señor."})
                                except Exception:
                                    pass
                if changed:
                    _save_tasks(tasks)
            except Exception as e:
                log.debug("scheduler: %s", e)
            time.sleep(20)

    threading.Thread(target=_loop, daemon=True, name="task-scheduler").start()
    return True


AUTO_TOOLS = [
    {"name": "download_file",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"url": "str", "name": "str opcional"},
     "description": "Descarga un fichero a la carpeta de descargas de JARVIS.",
     "label": "Descargando",
     "handler": download_file_handler},
    {"name": "move_file",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"src": "str", "dst": "str (carpeta o ruta)"},
     "description": "Mueve/renombra un fichero o carpeta.",
     "label": "Moviendo",
     "handler": move_file_handler},
    {"name": "list_dir",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str"},
     "description": "Lista archivos y carpetas de una ruta.",
     "label": "Explorando",
     "handler": list_dir_handler},
    {"name": "gpu_status",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Estado de la GPU NVIDIA (temp, uso, memoria).",
     "label": "Mirando la GPU",
     "handler": gpu_status_handler},
    {"name": "launch_game",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"game": "str (cs2, dota, apex, gta v, elden ring…)"},
     "description": "Lanza un juego de Steam.",
     "label": "Lanzando juego",
     "handler": launch_game_handler},
    {"name": "schedule_task",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"name": "str", "message": "str (qué hacer)", "when": "str (HH:MM|en 2h|en 30m|cada 1h)"},
     "description": "Programa una tarea: JARVIS ejecutará el mensaje a la hora.",
     "label": "Programando tarea",
     "handler": schedule_task_handler},
    {"name": "list_tasks",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Tareas programadas pendientes.",
     "label": "Listando tareas",
     "handler": list_tasks_handler},
    {"name": "cancel_task",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"name": "str (vacío=todas)"},
     "description": "Cancela tareas programadas.",
     "label": "Cancelando tarea",
     "handler": cancel_task_handler},
    {"name": "notify_phone",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"message": "str"},
     "description": "Aviso push urgente al móvil (suena como teléfono). Requiere app ntfy suscrita.",
     "label": "Avisando al móvil",
     "handler": notify_phone_handler},
]
