# -*- coding: utf-8 -*-
"""Lo que la pizarra necesita para funcionar en el navegador.

La pizarra de escritorio (Win+J) habla con Electron por IPC. Cuando la misma
pizarra se sirve por HTTP —modo LITE, servidor o Docker— no hay IPC, así que
estas funciones devuelven EXACTAMENTE la misma forma de datos que
`app/overlay-backend.js` devuelve por `ipcMain`. Misma forma a propósito: así
el mismo app.js sirve para las dos versiones sin duplicar la interfaz.

Solo Windows tiene accesos directos reales; en Linux el listado sale del PATH y
de las carpetas del usuario, que es lo mejor que se puede hacer sin un gestor
de aplicaciones instalado.
"""
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from path_security import resolve_user_path

log = logging.getLogger("webboard")

_SKIP_EXT = {".lnk", ".dll", ".sys", ".tmp", ".ini", ".reg"}
_MAX_ENTRIES = 1500
_MAX_APPS = 60
_MAX_SIZE = 100 * 1024 * 1024
_IMG_RE = re.compile(r"\.(png|jpe?g|gif|webp|bmp|ico|tiff?)$", re.I)

_apps_cache: list = []
_apps_at = 0.0
_INDICE: dict = {}          # copia estable del índice de apps, para el listado completo


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def home_dirs() -> list[str]:
    h = _home()
    out = []
    for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
        p = h / sub
        if p.is_dir():
            out.append(str(p))
    return out


def fixed_drives() -> list[str]:
    """Unidades fijas. En otros sistemas operativo se listan los puntos de montaje."""
    if os.name == "nt":
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            out = []
            for i in range(26):
                if mask >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(
                        chr(65 + i) + ":\\") == 3:
                    out.append(chr(65 + i) + ":\\")
            return out
        except Exception:
            return ["C:\\"]
    return ["/"]


def list_apps() -> list[dict]:
    """Programas instalados. Reutiliza el índice de winget-free que ya hay
    para "abre X", así que coincide con lo que el asistente puede abrir."""
    global _apps_cache, _apps_at
    if _apps_cache and time.time() - _apps_at < 300:
        return _apps_cache
    apps: list[dict] = []
    vistos: set[str] = set()
    firmas: set[tuple] = set()
    try:
        from tools import system_tools as st
        st._ensure_index()
        th = st._INDEX_THREAD
        if th is not None and th.is_alive():
            th.join(timeout=4.0)
        # Se eligen los atajos y ejecutables "bonitos", no 1000 DLLs del PATH.
        for key, ruta in st._APP_INDEX.items():
            if key in vistos or not key or len(key) < 3:
                continue
            p = Path(ruta)
            if p.suffix.lower() not in (".lnk", ".exe"):
                continue
            # El mismo programa aparece con varias claves en el índice; sin
            # esto la rejilla salía con "File Explorer" repetido tres veces.
            firma = (p.stem.lower(), str(p).lower())
            if firma in firmas:
                continue
            vistos.add(key)
            firmas.add(firma)
            apps.append({"name": p.stem, "path": str(p), "type": "app"})
            if len(apps) >= _MAX_APPS:
                break
    except Exception as e:
        log.debug("list_apps: %s", e)
    # Atajos del menú Inicio, que son los que el usuario reconoce.
    try:
        for base in (Path(os.environ.get("APPDATA", "")) /
                     "Microsoft/Windows/Start Menu/Programs",
                     Path(os.environ.get("PROGRAMDATA", "")) /
                     "Microsoft/Windows/Start Menu/Programs"):
            if not base.is_dir():
                continue
            for lnk in base.rglob("*.lnk"):
                if len(apps) >= _MAX_APPS:
                    break
                k = re.sub(r"[^a-z0-9]", "", lnk.stem.lower())
                if k in vistos or len(lnk.stem) < 3:
                    continue
                firma = (lnk.stem.lower(), str(lnk).lower())
                if firma in firmas:
                    continue
                vistos.add(k)
                firmas.add(firma)
                apps.append({"name": lnk.stem, "path": str(lnk), "type": "app"})
    except Exception as e:
        log.debug("list_apps menu: %s", e)
    for sub in ("Desktop", "Downloads", "Pictures"):
        p = _home() / sub
        if p.is_dir():
            apps.append({"name": sub, "path": str(p), "type": "folder",
                         "kind": "quick"})
    _apps_cache = apps[:_MAX_APPS]
    _apps_at = time.time()
    # Se guarda una copia para el listado completo, que no puede leer un
    # diccionario que se está rellenando en otro hilo.
    _INDICE.clear()
    try:
        _INDICE.update(st._APP_INDEX)
    except Exception:
        pass
    return _apps_cache


def recent_files() -> list[dict]:
    out = []
    for sub in ("Documents", "Pictures", "Desktop", "Downloads"):
        d = _home() / sub
        if not d.is_dir():
            continue
        try:
            nombres = list(d.iterdir())
        except Exception:
            continue
        for f in nombres:
            try:
                # Los ficheros de bloqueo (.~lock.X#) de LibreOffice u Office
                # no son documentos del usuario: solo ruido.
                if f.name.startswith(".") or f.name.endswith("#"):
                    continue
                if f.suffix.lower() in _SKIP_EXT or not f.is_file():
                    continue
                if f.stat().st_size > _MAX_SIZE:
                    continue
            except Exception:
                continue
            out.append({"name": f.name, "path": str(f), "ext": f.suffix.lower(),
                        "type": "image" if _IMG_RE.search(f.name) else "file",
                        "_mtime": f.stat().st_mtime * 1000})
    out.sort(key=lambda e: e.get("_mtime", 0), reverse=True)
    return out


def list_dir(directorio: str | None) -> dict:
    """Listado de una carpeta, con la misma forma que usa la pizarra."""
    if not directorio:
        return {"path": None, "name": "Recientes", "parent": None,
                "homes": home_dirs(), "drives": fixed_drives(),
                "folders": [], "entries": recent_files()}
    try:
        safe_path = resolve_user_path(directorio, must_exist=True)
        if not safe_path:
            return list_dir(None)
        base = Path(safe_path)
    except Exception:
        return list_dir(None)
    if not base.is_dir():
        return list_dir(None)
    carpetas, ficheros = [], []
    try:
        hijos = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except Exception:
        return list_dir(None)
    for d in hijos:
        if d.name.startswith("."):
            continue
        if len(carpetas) + len(ficheros) >= _MAX_ENTRIES:
            break
        try:
            if d.is_dir():
                carpetas.append({"name": d.name, "path": str(d), "type": "folder"})
                continue
            if d.suffix.lower() in _SKIP_EXT:
                continue
            tam = d.stat().st_size
        except Exception:
            continue
        if tam > _MAX_SIZE:
            continue
        ficheros.append({"name": d.name, "path": str(d), "ext": d.suffix.lower(),
                         "type": "image" if _IMG_RE.search(d.name) else "file",
                         "size": tam, "big": tam > _MAX_SIZE})
    carpetas.sort(key=lambda e: e["name"].lower())
    ficheros.sort(key=lambda e: e["name"].lower())
    padre = str(base.parent) if base.parent != base else None
    return {"path": str(base), "name": base.name or str(base), "parent": padre,
            "homes": home_dirs(), "folders": carpetas, "entries": ficheros}


def list_apps_all(consulta: str = "", tope: int = 2000) -> list[dict]:
    """TODAS las aplicaciones instaladas, no solo las 60 de la rejilla.

    La rejilla de la pizarra es una muestra bonita; esto es el listado completo
    para buscar cualquier cosa que haya en el equipo y abrirla. Búsqueda por
    texto, para no tener que recorrer cientos de nombres a ojo.
    """
    try:
        from tools import system_tools as st
        st._ensure_index()
        th = st._INDEX_THREAD
        if th is not None and th.is_alive():
            th.join(timeout=4.0)
    except Exception as e:
        log.debug("list_apps_all: %s", e)
    # Se sincroniza aquí y no en list_apps(): este endpoint se puede llamar
    # solo, y sin esto devolvía 95 en vez de 900.
    if not _INDICE:
        try:
            from tools import system_tools as st
            _INDICE.update(st._APP_INDEX)
        except Exception as e:
            log.debug("list_apps_all indice: %s", e)
    q = (consulta or "").strip().lower()
    vistos: dict[str, dict] = {}
    for _clave, ruta in list(_INDICE.items()):
        p = Path(ruta)
        if p.suffix.lower() not in (".lnk", ".exe"):
            continue
        nombre = p.stem.strip()
        if len(nombre) < 2:
            continue
        clave = nombre.lower()
        if clave in vistos:
            continue
        if q and q not in clave:
            continue
        vistos[clave] = {"name": nombre, "path": str(p), "type": "app",
                         "menu": False}
    # Los atajos del menú Inicio van primero al ordenar: son los programas que
    # el usuario tiene instalados de verdad, mientras que el resto son
    # ejecutables sueltos del PATH. Con el tope no deben caer los primeros.
    try:
        for base in (Path(os.environ.get("APPDATA", "")) /
                     "Microsoft/Windows/Start Menu/Programs",
                     Path(os.environ.get("PROGRAMDATA", "")) /
                     "Microsoft/Windows/Start Menu/Programs"):
            if not base.is_dir():
                continue
            for lnk in base.rglob("*.lnk"):
                nombre = lnk.stem.strip()
                if len(nombre) < 3:
                    continue
                clave = nombre.lower()
                if clave in vistos:
                    continue
                if q and q not in clave:
                    continue
                vistos[clave] = {"name": nombre, "path": str(lnk), "type": "app",
                                 "menu": True}
    except Exception as e:
        log.debug("list_apps_all menu: %s", e)
    out = sorted(vistos.values(), key=lambda e: (not e.get("menu"), e["name"].lower()))
    for e in out:
        e.pop("menu", None)
    out = out[:tope]
    # Las herramientas del sistema se añaden SIEMPRE al principio. El índice
    # depende de los accesos del menú Inicio, y hay Utilities de Windows que
    # ahí no aparecen (la terminal, el explorador, calculators). El señor las
    # pidió expressly, así que no pueden depender de que el índice las pille.
    sistema = _utilidades_sistema()
    faltan = {s["name"].lower() for s in sistema}
    return [s for s in sistema if not q or q in s["name"].lower()] + \
           [e for e in out if e["name"].lower() not in faltan]


def _utilidades_sistema() -> list[dict]:
    """Lanzadores directos a lo que siempre hay en Windows. shutil.which no
    basta: el Bloc de notas y el Explorador se abren por su executable."""
    out = []

    def anade(nombre, candidatos):
        for c in candidatos:
            r = shutil.which(c) or (c if os.path.exists(c) else None)
            if r:
                out.append({"name": nombre, "path": r, "type": "app"})
                return

    win = os.environ.get("SystemRoot", r"C:\Windows")
    anade("Símbolo de sistema", ["cmd.exe", os.path.join(win, "System32", "cmd.exe")])
    anade("PowerShell", ["powershell.exe", "pwsh.exe",
                         os.path.join(win, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")])
    anade("Windows Terminal", ["wt.exe", "WindowsTerminal"])
    anade("Explorador de archivos", ["explorer.exe",
                                     os.path.join(win, "explorer.exe")])
    anade("Bloc de notas", ["notepad.exe", os.path.join(win, "System32", "notepad.exe")])
    anade("Calculadora", ["calc.exe", os.path.join(win, "System32", "calc.exe")])
    anade("Configuración", ["ms-settings:", "SystemSettings"])
    anade("Programas y caracteristicas", ["appwiz.cpl"])
    return out


def abrir_terminal(shell: str = "") -> dict:
    """Abre una terminal en el equipo donde corre el brain. En LITE el usuario
    no ve ninguna ventana de JARVIS, así que esta es la forma de tener una
    consola de verdad sin salir del navegador."""
    windows = {
        "wt": ["wt.exe", "WindowsTerminal"],
        "powershell": ["powershell.exe", "pwsh.exe", "PowerShell"],
        "cmd": ["cmd.exe", "Símbolo de sistema"],
    }
    orden = [windows.get((shell or "").lower())] if shell in windows else []
    orden += [windows["wt"], windows["powershell"], windows["cmd"]]
    for item in orden:
        if not item:
            continue
        exe, etiq = item
        ruta = shutil.which(exe)
        if not ruta:
            continue
        try:
            subprocess.Popen([ruta], close_fds=True)
            return {"ok": True, "opened": etiq}
        except Exception as e:
            log.debug("terminal %s: %s", exe, e)
    # Fuera de Windows: intentamos los emuladores habituales.
    if os.name != "nt":
        for exe in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            ruta = shutil.which(exe)
            if ruta:
                try:
                    subprocess.Popen([ruta], close_fds=True)
                    return {"ok": True, "opened": exe}
                except Exception:
                    continue
    return {"ok": False, "error": "No hay ninguna terminal instalada"}


def abrir(item: dict) -> dict:
    """Abre una app o un fichero, igual que lo haría el IPC de Electron."""
    ruta = (item or {}).get("path") or (item or {}).get("target") or ""
    nombre = (item or {}).get("name") or ""
    if not ruta and nombre:
        ruta = nombre
    if not ruta:
        return {"ok": False, "error": "no hay nada que abrir"}
    try:
        if os.name == "nt":
            from tools.system_tools import (
                _launch_native, _resolve_open_target, _safe_open_path,
            )
            safe_path = _safe_open_path(ruta)
            if safe_path:
                ruta = safe_path
                os.startfile(ruta)
            else:
                target = _resolve_open_target(ruta)
                if not target or target == ruta:
                    return {"ok": False, "error": "ruta fuera de ubicaciones permitidas"}
                _launch_native(target)
        else:
            ruta = resolve_user_path(ruta, must_exist=True)
            if not ruta:
                return {"ok": False, "error": "ruta fuera de la carpeta de usuario"}
            subprocess.Popen(["xdg-open", ruta],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "opened": ruta}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
