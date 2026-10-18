"""Dónde está el servidor de OpenCode de verdad, y cómo arrancarlo.

OpenCode no es un ejecutable que se llame y devuelva texto: es un servicio.
El cliente (el TUI, `opencode run`) localiza un servidor ya arrancado o lo
levanta él. JARVIS hace lo mismo, en este orden:

  1. OPENCODE_SERVER_URL, si el señor la fija a mano.
  2. El fichero de registro que escribe el servicio compartido
     (XDG_STATE_HOME/opencode/service.json, o ~/.local/state/opencode/...).
     Es la vía normal: si el TUI está abierto, el servicio ya existe.
  3. `opencode service start`, y se espera a que se registre.
  4. Un servidor propio con `opencode serve`, que JARVIS arranca y apaga.

Si nada de eso funciona se dice con todas las letras: no hay servidor de
OpenCode, y por tanto no hay nada que monitorizar. Nunca se simula.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .client import OpenCodeClient, OpenCodeError

log = logging.getLogger("brain.opencode.service")

_DEFAULT_PORT = 4096
_lock = threading.RLock()
_propio: Optional[dict] = None   # servidor arrancado por JARVIS


# --------------------------------------------------------------------------- #
# El ejecutable de OpenCode
# --------------------------------------------------------------------------- #
def cli_path() -> str:
    """Ruta del CLI de OpenCode, o "" si no está.

    Primero se reutiliza el detector que ya usa el Tool Manager
    (`tools.opencode_tools`), para no tener dos verdades distintas; y si ese
    módulo no está disponible, se busca en el PATH.
    """
    try:
        from tools.opencode_tools import _cli_disponible  # type: ignore

        ruta = _cli_disponible()
        if ruta:
            return ruta
    except Exception as exc:  # noqa: BLE001 - el modulo de tools puede faltar
        log.debug("no se pudo usar el detector de OpenCode existente: %s", exc)
    try:
        import shutil

        for cand in ("opencode.cmd", "opencode.exe", "opencode"):
            ruta = shutil.which(cand)
            if ruta:
                return ruta
    except Exception:  # noqa: BLE001
        pass
    return ""


_VERSION_CACHE: tuple = ("", 0.0)


def version(cli: str = "", max_age: float = 900.0) -> str:
    """Versión real del CLI instalado (o "" si no se puede leer).

    Con memoria: el panel pregunta cada pocos segundos, y lanzar el proceso
    `opencode --version` en cada consulta sería absurdo.
    """
    global _VERSION_CACHE
    ruta = cli or cli_path()
    if not ruta:
        return ""
    cached, cuando = _VERSION_CACHE
    if cached and cuando and time.monotonic() - cuando < max_age:
        return cached
    try:
        r = subprocess.run([ruta, "--version"], capture_output=True, text=True,
                           timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        texto = (r.stdout or r.stderr or "").strip().splitlines()[0][:40]
        _VERSION_CACHE = (texto, time.monotonic())
        return texto
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo leer la versión de OpenCode: %s", exc)
        return ""


# --------------------------------------------------------------------------- #
# El fichero de registro del servicio compartido
# --------------------------------------------------------------------------- #
def registration_files() -> list[Path]:
    """Rutas donde OpenCode deja apuntar su servicio (en ese orden)."""
    rutas: list[Path] = []
    estado = os.environ.get("XDG_STATE_HOME", "").strip()
    if estado:
        rutas.append(Path(estado) / "opencode" / "service.json")
    try:
        rutas.append(Path.home() / ".local" / "state" / "opencode" / "service.json")
    except Exception:  # noqa: BLE001 - home puede fallar en contenedores raros
        pass
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        rutas.append(Path(local) / "opencode" / "service.json")
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        rutas.append(Path(appdata) / "opencode" / "service.json")
    vistas, únicas = set(), []
    for ruta in rutas:
        clave = str(ruta).lower()
        if clave not in vistas:
            vistas.add(clave)
            únicas.append(ruta)
    return únicas


def read_registration() -> Optional[dict]:
    """Contenido del registro del servicio compartido, si lo hay."""
    for ruta in registration_files():
        try:
            if not ruta.is_file():
                continue
            datos = json.loads(ruta.read_text(encoding="utf-8") or "{}")
            if isinstance(datos, dict) and datos.get("url"):
                datos["_file"] = str(ruta)
                return datos
        except Exception as exc:  # noqa: BLE001 - un registro ilegible no es fatal
            log.debug("registro de OpenCode ilegible en %s: %s", ruta, exc)
    return None


def endpoint_from(info: dict) -> dict:
    """Traduce un registro de OpenCode a {url, username, password}."""
    url = str(info.get("url") or "").rstrip("/")
    password = str(info.get("password") or "")
    return {"url": url, "username": "opencode" if password else "",
            "password": password}


# --------------------------------------------------------------------------- #
# Salud del servidor
# --------------------------------------------------------------------------- #
def health(url: str, username: str = "", password: str = "",
           timeout: float = 4.0) -> dict:
    """GET /api/info. Devuelve {'ok': bool, ...} con lo que/conteste de verdad."""
    if not url:
        return {"ok": False, "error": "Sin URL de servidor."}
    cliente = OpenCodeClient(url, username, password, timeout=timeout)
    try:
        datos = cliente.get("/api/info", timeout=timeout)
    except OpenCodeError as exc:
        return {"ok": False, "error": str(exc), "status": exc.status}
    info = datos if isinstance(datos, dict) else {}
    return {"ok": True, "url": url, "info": info,
            "version": str(info.get("version") or ""),
            "pid": info.get("pid")}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _oc_env() -> dict:
    """El entorno de OpenCode con las claves de JARVIS (mismo truco que las tools)."""
    env = dict(os.environ)
    try:
        base = env.get("APPDATA", "")
        if base:
            p = Path(base) / "JARVIS" / "keys.env"
            if p.is_file():
                for linea in p.read_text(encoding="utf-8-sig").splitlines():
                    linea = linea.strip()
                    if linea and "=" in linea and not linea.startswith("#"):
                        k, _, v = linea.partition("=")
                        if k.strip() and v.strip():
                            env[k.strip()] = v.strip()
    except Exception as exc:  # noqa: BLE001
        # El mensaje decía "claves para OpenCode" pero lo que se registra es el
        # error de lectura, no las claves: asi que no parece que se estén
        # volcando secretos, que es justo lo que deja sospechoso un log.
        log.debug("no pude leer las claves para OpenCode: %s", exc)
    return env


# --------------------------------------------------------------------------- #
# Arranque del servicio
# --------------------------------------------------------------------------- #
def _esperar_registro(segundos: float = 20.0) -> Optional[dict]:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        datos = read_registration()
        if datos:
            return datos
        time.sleep(0.4)
    return None


def _arrancar_servicio_compartido() -> Optional[dict]:
    """`opencode service start` y espera a que se registre."""
    cli = cli_path()
    if not cli:
        return None
    try:
        subprocess.run([cli, "service", "start"], capture_output=True, text=True,
                       timeout=45, env=_oc_env(),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo arrancar el servicio de OpenCode: %s", exc)
        return None
    return _esperar_registro(20.0)


def _arrancar_propio() -> Optional[dict]:
    """Servidor privado con `opencode serve`, que luego apaga JARVIS."""
    global _propio
    cli = cli_path()
    if not cli:
        return None
    puerto = _free_port()
    cmd = [cli, "serve", "--port", str(puerto), "--hostname", "127.0.0.1"]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=_oc_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo lanzar `opencode serve`: %s", exc)
        return None

    url = ""
    linea_re = "http://"
    for _ in range(60):          # 30 s a 500 ms leyendo lo que imprime
        if proc.poll() is not None:
            break
        linea = ""
        try:
            linea = proc.stdout.readline() if proc.stdout else ""
        except Exception:  # noqa: BLE001
            linea = ""
        if linea:
            log.debug("opencode serve: %s", linea.strip()[:160])
            if linea_re in linea:
                url = linea[linea_re.index(linea_re):].strip().strip('"').rstrip("/")
                if url.startswith("http://"):
                    break
        time.sleep(0.5)
    if not url:
        # El servicio puede no imprimir nada (por ejemplo en modo registro):
        # se deduce del puerto que le pedimos y se comprueba la salud.
        url = f"http://127.0.0.1:{puerto}"
    for _ in range(20):
        if health(url, timeout=2.0).get("ok"):
            _propio = {"proc": proc, "url": url}
            return {"url": url, "_file": "", "pid": proc.pid, "propio": True}
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    _matar(proc)
    return None


def _matar(proc) -> None:
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def ensure(timeout: float = 25.0) -> dict:
    """Devuelve el servidor de OpenCode disponible, o un error concreto.

    {"ok": True, "url", "username", "password", "source", "pid", "version"}
    {"ok": False, "error": "..."}
    """
    global _propio
    with _lock:
        manual = os.environ.get("OPENCODE_SERVER_URL", "").strip()
        if manual:
            url = manual.rstrip("/")
            estado = health(url, timeout=4.0)
            if estado.get("ok"):
                return {"ok": True, "url": url, "username": "", "password": "",
                        "source": "OPENCODE_SERVER_URL", "pid": estado.get("pid"),
                        "version": estado.get("version", "")}
            return {"ok": False,
                    "error": "OPENCODE_SERVER_URL no responde: " +
                             str(estado.get("error") or "sin detalle")}

        info = read_registration()
        if info:
            endpoint = endpoint_from(info)
            estado = health(endpoint["url"], endpoint["username"],
                            endpoint["password"], timeout=4.0)
            if estado.get("ok"):
                return {"ok": True, **endpoint, "source": "servicio compartido",
                        "pid": info.get("pid") or estado.get("pid"),
                        "version": estado.get("version", "")}

        info = _arrancar_servicio_compartido()
        if info:
            endpoint = endpoint_from(info)
            estado = health(endpoint["url"], endpoint["username"],
                            endpoint["password"], timeout=6.0)
            if estado.get("ok"):
                return {"ok": True, **endpoint, "source": "servicio compartido (nuevo)",
                        "pid": info.get("pid") or estado.get("pid"),
                        "version": estado.get("version", "")}

        info = _arrancar_propio()
        if info:
            return {"ok": True, "url": info["url"], "username": "", "password": "",
                    "source": "servidor propio de JARVIS", "pid": info.get("pid"),
                    "version": ""}

        return {"ok": False,
                "error": "No hay servidor de OpenCode disponible "
                         "(no está instalado, no arranca, o "
                         + (f"no se encontró {registration_files()[0]}" if registration_files() else "no se encontró su registro")
                         + ")."}


def stop_owned() -> None:
    """Apaga solo el servidor que arrancó JARVIS. El compartido no se toca."""
    global _propio
    with _lock:
        if _propio and _propio.get("proc"):
            _matar(_propio["proc"])
        _propio = None


def describe(arrancar: bool = False) -> dict:
    """Estado del servidor para el panel. Sin inventar nada: si no está, se dice.

    Por defecto NO arranca nada: el panel consulta cada pocos segundos y no
    puede ponerse a levantar servidores de OpenCode por el camino. Solo
    `ensure()` (que usa el adaptador al abrir una tarea) arranca uno.
    """
    cli = cli_path()
    info = read_registration()
    estado = {"cli": cli, "cli_version": version(cli) if cli else "",
              "registration": info.get("_file", "") if info else "",
              "registered_url": info.get("url", "") if info else ""}
    if not cli and not info:
        estado.update({"ok": False, "error": "OpenCode no está instalado en este equipo."})
        return estado
    endpoint = endpoint_from(info) if info else {}
    salud = health(endpoint.get("url", ""), endpoint.get("username", ""),
                   endpoint.get("password", "")) if endpoint else {"ok": False}
    if salud.get("ok"):
        estado.update({"ok": True, "url": endpoint.get("url", ""),
                       "source": "servicio compartido",
                       "version": salud.get("version") or estado["cli_version"],
                       "pid": info.get("pid") or salud.get("pid")})
        return estado
    if not arrancar:
        estado.update({"ok": False,
                       "error": "OpenCode está instalado pero su servidor no está en marcha."})
        return estado
    resultado = ensure(timeout=15.0)
    estado.update({"ok": bool(resultado.get("ok")),
                   "url": resultado.get("url", ""),
                   "source": resultado.get("source", ""),
                   "version": resultado.get("version", "") or estado["cli_version"],
                   "pid": resultado.get("pid")})
    if not resultado.get("ok"):
        estado["error"] = resultado.get("error", "")
    return estado