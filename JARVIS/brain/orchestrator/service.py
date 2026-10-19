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
# Rutas de salud de OpenCode, en el orden en que se prueban.
#
# Antes solo se preguntaba a `/api/info`, y en OpenCode 1.19 ESA RUTA NO
# EXISTE: devuelve el HTML de la interfaz, no JSON. Con eso `health()` decía
# "OpenCode devolvió una respuesta que no es JSON", `ensure()` no encontraba
# el servidor NUNCA, y la tarea se quedaba eternamente en STARTING_OPENCODE.
# En el contenedor, sin OpenCode ya arrancado de antes, eso es siempre.
#
# Comprobado contra `opencode serve` 1.19.0:
#     /global/health -> 200 {"healthy":true,"version":"1.19.0"}
#     /api/health    -> 200 {"healthy":true}
#     /api/info      -> 200 <!doctype html>      (la interfaz, no la API)
RUTAS_SALUD = ("/global/health", "/api/health", "/api/info", "/health")


def health(url: str, username: str = "", password: str = "",
           timeout: float = 4.0) -> dict:
    """Pregunta al servidor si está vivo. Devuelve {'ok': bool, ...}.

    Se prueban varias rutas porque OpenCode las ha cambiado entre versiones:
    preguntando solo a una, con la versión equivocada el servidor aparece
    caído aunque esté perfectamente levantado.
    """
    if not url:
        return {"ok": False, "error": "Sin URL de servidor."}
    cliente = OpenCodeClient(url, username, password, timeout=timeout)
    ultimo = ""
    status = 0
    for ruta in RUTAS_SALUD:
        try:
            datos = cliente.get(ruta, timeout=timeout)
        except OpenCodeError as exc:
            ultimo = str(exc)
            status = exc.status
            continue
        except Exception as exc:  # noqa: BLE001
            ultimo = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(datos, dict):
            # Contestó, pero no es la API (es el HTML de la interfaz).
            ultimo = f"{ruta} devolvió algo que no es JSON"
            continue
        info = datos
        # El servidor dice «healthy» o «ok»; cualquiera de los dos vale, y si
        # no dice ninguno pero es JSON de la API, también está vivo.
        vivo = datos.get("healthy")
        if vivo is None:
            vivo = datos.get("ok")
        if vivo is None:
            vivo = True
        if not vivo:
            ultimo = f"{ruta} dijo que no está sano: {datos}"
            status = 200
            continue
        return {"ok": True, "url": url, "info": info, "ruta": ruta,
                "version": str(info.get("version") or ""),
                "pid": info.get("pid")}
    return {"ok": False, "error": ultimo or "el servidor no respondió",
            "status": status}


# --------------------------------------------------------------- #
# Los proveedores que trae OpenCode
# --------------------------------------------------------------- #
#
# OJO con la ruta. Existen dos y no vale la primera:
#
#   GET /api/provider  -> los proveedores ACTIVOS. Pide un parámetro
#                         `location` y, sin él, contesta lista VACÍA.
#   GET /provider      -> el catálogo entero: {"all": [...],
#                         "connected": [...], "default": {...}}
#
# Preguntando solo por la primera se llegaba a la conclusión de «OpenCode no
# tiene ningún proveedor». Es FALSA: medido en el contenedor con 1.19.0, hay
# 232 proveedores en el catálogo y tres YA CONECTADOS de serie
# (google, groq y opencode). El de OpenCode —«opencode», que es su Zen— trae
# diez modelos y todos son gratis.
#
# Con cero proveedores OpenCode acepta el encargo, no tiene con qué ejecutarlo
# y devuelve la sesión vacía sin decir por qué.

# Orden de preferencia al elegir modelo sin que nadie pida ninguno.
#
# Se empieza por el de OpenCode (el Zen, `opencode`) porque es el único que
# este mismo OpenCode está hecho para usar, y trae diez modelos sin coste
# (`space-bunny-free`, `glm-5.3-flash-free`, `deepseek-v4.1-flash-free`…).
_PREFERENCIA = ("opencode", "meta", "anthropic", "google", "openai")

# Proveedores que este OpenCode RECHAZA. Comprobado en su propio log con
# 1.19.0 y una clave de Groq puesta y correcta:
#
#   SessionRunnerModel.UnsupportedApiError: Unsupported API for
#   groq/llama-3.3-70b-versatile: aisdk:@ai-sdk/groq
#
# Es decir: el proveedor existe en el catálogo y aparece como «conectado»
# porque hay clave en el entorno, pero el paquete `@ai-sdk/groq` no viene en
# esta compilación. OpenCode acepta el encargo, se queda mudo y no escribe
# nada. Elegirlo a ciegas es la forma segura de dejar la tarea colgada, así
# que se descartan aunque tengan clave.
_SIN_SOPORTE = frozenset({"groq"})

# De qué clave habla cada proveedor, para poder decir algo útil cuando no hay
# ninguna puesta. Sacado de `GET /provider`.
_CLAVE_DE = {
    "opencode": "OPENCODE_API_KEY",
    "meta": "META_MODEL_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def proveedores(url: str, username: str = "", password: str = "",
                timeout: float = 20.0) -> dict:
    """Qué proveedores ve el servidor. {'ok': bool, 'todos', 'conectados', ...}

    Devuelve `conectados` como lista de identificadores, que es como lo da
    OpenCode.
    """
    if not url:
        return {"ok": False, "error": "Sin URL de servidor."}
    cliente = OpenCodeClient(url, username, password, timeout=timeout)
    try:
        datos = cliente.get("/provider", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not isinstance(datos, dict):
        return {"ok": False, "error": "El catálogo de proveedores vino raro."}

    # `all` llega como LISTA de proveedores, cada uno con su `id`. Antes se
    # trataba como diccionario y reventaba con AttributeError.
    por_id: dict = {}
    bruto = datos.get("all")
    if isinstance(bruto, list):
        for p in bruto:
            if isinstance(p, dict) and p.get("id"):
                por_id[str(p["id"])] = p
    elif isinstance(bruto, dict):
        por_id = {str(k): v for k, v in bruto.items() if isinstance(v, dict)}

    conectados = datos.get("connected")
    if isinstance(conectados, dict):
        lista_conectados = sorted(str(k) for k in conectados)
    elif isinstance(conectados, list):
        lista_conectados = [str(x) for x in conectados]
    else:
        lista_conectados = []

    return {"ok": True, "todos": por_id, "conectados": lista_conectados,
            "default": datos.get("default") or {},
            "total": len(por_id)}


def _modelo_gratis(proveedor: dict) -> str:
    """El primer modelo sin coste de un proveedor, si tiene alguno."""
    modelos = proveedor.get("models") or {}
    if not isinstance(modelos, dict):
        return ""
    for nombre in sorted(modelos.keys()):
        coste = ((modelos[nombre] or {}).get("cost") or {})
        entrada = coste.get("input")
        salida = coste.get("output")
        if entrada in (0, "0", None) and salida in (0, "0", None):
            return str(nombre)
    return ""


def elegir_modelo(url: str, username: str = "", password: str = "",
                  timeout: float = 20.0) -> dict:
    """Un modelo que OpenCode pueda usar DE VERDAD, sin pedir nada.

    OJO con un detalle que costó trabajo descubrir: que un proveedor esté en la
    lista de «conectados» NO quiere decir que tenga clave. Medido en el
    contenedor con 1.19.0:

        proveedor  claves que pide       ¿conectado?  ¿tiene clave?
        opencode   OPENCODE_API_KEY      sí           NO  -> HTTP 403
        meta       META_MODEL_API_KEY     no           NO
        groq       GROQ_API_KEY           sí           SÍ  (16 modelos)
        google     GEMINI_API_KEY         sí           NO

    Si se eligiera por la lista de conectados, casi siempre caería en
    `opencode`, que es justo el que devuelve 403. Así que se filtra por `key`:
    solo cuentan los que de verdad tienen credencial. De esos se prefiere el de
    OpenCode, y dentro de él el primer modelo gratis.

    Devuelve {"ok", "proveedor", "modelo", "gratis", "ref"} donde `ref` es
    listo para mandarse tal cual en el cuerpo de la sesión.
    """
    cat = proveedores(url, username, password, timeout=timeout)
    if not cat.get("ok"):
        return cat
    por_id = cat.get("todos") or {}
    conectados = set(cat.get("conectados") or [])

    # Descartados los que esta versión de OpenCode no soporta. Ver
    # `_SIN_SOPORTE`: con Groq puesto el encargo se quedaba colgado.
    utilizables = [pid for pid, p in por_id.items()
                   if isinstance(p, dict) and p.get("key")
                   and pid not in _SIN_SOPORTE]
    if not utilizables:
        return {"ok": False,
                "error": ("Ninguno de los proveedores con clave puesta serves: "
                          + ", ".join(sorted(conectados)) +
                          ". Pon OPENCODE_API_KEY para usar el Zen de OpenCode, "
                          "que trae diez modelos gratis."),
                "pide": ["OPENCODE_API_KEY"]}

    orden = [p for p in _PREFERENCIA if p in utilizables]
    orden += [p for p in utilizables if p not in orden]

    for pid in orden:
        proveedor = por_id[pid] or {}
        nombre = _modelo_gratis(proveedor)
        gratis = bool(nombre)
        if not nombre:
            modelos = proveedor.get("models") or {}
            if isinstance(modelos, dict) and modelos:
                nombre = sorted(modelos.keys())[0]
        if not nombre:
            continue
        return {"ok": True, "proveedor": pid, "modelo": nombre,
                "gratis": gratis,
                "conectado": pid in conectados,
                "ref": {"id": nombre, "providerID": pid}}

    return {"ok": False,
            "error": "OpenCode no tiene ningún proveedor conectado."}


def formatear_modelo(modelo: str) -> dict:
    """'opencode/space-bunny-free' -> {'providerID': ..., 'id': ...}.

    OpenCode 1.19 define `ModelRef` con required = ["id", "providerID"]. JARVIS
    guarda el modelo como una cadena suelta y lo mandaba tal cual: al no ser
    un objeto, el servidor contestaba `Missing key at ["model"]["id"]` y la
    tarea no arrancaba. Si ya viene en dict, se respeta como esté.
    """
    if isinstance(modelo, dict):
        ref = dict(modelo)
        if ref.get("id") and ref.get("providerID"):
            return ref
        modelo = str(ref.get("id") or ref.get("modelID") or "")
    texto = (modelo or "").strip()
    if not texto:
        return {}
    if "/" in texto:
        proveedor, _, nombre = texto.partition("/")
        if nombre:
            return {"id": nombre, "providerID": proveedor}
    # Sin proveedor explícito: solo el id; OpenCode lo resuelve con el de por
    # defecto del servidor.
    return {"id": texto}


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


def _arrancar_servicio_compartido(presupuesto: float = 20.0) -> Optional[dict]:
    """`opencode service start` y espera a que se registre.

    El presupuesto se lo pasa `ensure()`: antes este reloj era fijo (45 s) y se
    tragaba medio plazo entero antes de que el siguiente intento tuviera sitio.
    """
    cli = cli_path()
    if not cli:
        return None
    fin = time.monotonic() + max(4.0, float(presupuesto or 20.0))
    try:
        r = subprocess.run([cli, "service", "start"], capture_output=True,
                           text=True, timeout=max(4.0, fin - time.monotonic()),
                           env=_oc_env(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo arrancar el servicio de OpenCode: %s", exc)
        return None

    # Si el comando ha FALLADO no hay registro que llegue: se pierde el
    # tiempo esperándolo y se come el plazo del intento que sí funciona.
    #
    # Medido en el contenedor con opencode 1.19.0: `opencode service start`
    # devuelve código 1 en medio segundo (ese subcomando ya no existe), y antes
    # se seguían esperando 12 s a un fichero que noappears nunca. Con eso, de
    # los 25 s de `ensure()`, al servidor propio —el que sí funciona— le
    # llegaban menos de uno.
    if getattr(r, "returncode", 0) != 0:
        detalle = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1]
        log.info("`opencode service start` no funcionó (código %s): %s",
                 r.returncode, detalle[:120])
        return None

    restante = fin - time.monotonic()
    if restante <= 1.0:
        return None
    return _esperar_registro(min(20.0, restante))


def _arrancar_propio(deadline: Optional[float] = None) -> Optional[dict]:
    """Servidor privado con `opencode serve`, que luego apaga JARVIS.

    OJO con cómo se comprueba que arrancó. Antes se leía la línea que
    imprime el servidor («opencode server listening on http://…») con
    `proc.stdout.readline()`, esperando hasta 30 s. Eso funciona si OpenCode
    escribe cada línea al momento, pero **cuando stdout no es una terminal sino
    una tubería —que es justo lo que pasa en Docker— el proceso BUFFERIZA y
    no suelta nada hasta llenar el búfer**. La `readline()` se quedaba
    bloqueada esperando, y `ensure()` se comía más de minuto y medio sin
    devolver nada: en el contenedor la tarea se quedaba eternamente en
    STARTING_OPENCODE.

    Ahora se sondea el puerto que le hemos pedido, que es lo que de verdad
    dice si el servidor está escuchando, y la salida se lee en un hilo aparte
    solo para dejar rastro en el log. Y se respeta `deadline`.
    """
    global _propio
    cli = cli_path()
    if not cli:
        return None
    if deadline is None:
        deadline = time.monotonic() + 40.0
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

    def _escuchar() -> None:
        """Lee la salida del servidor para el log, sin bloquear nada."""
        try:
            for linea in iter(proc.stdout.readline, ""):
                if linea.strip():
                    log.debug("opencode serve: %s", linea.strip()[:160])
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_escuchar, daemon=True,
                     name="oc-serve-log").start()

    url = f"http://127.0.0.1:{puerto}"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if health(url, timeout=2.0).get("ok"):
            _propio = {"proc": proc, "url": url}
            return {"url": url, "_file": "", "pid": proc.pid, "propio": True}
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
    # El plazo se calcula ANTES de coger el candado: es lo que manda en los
    # dos arranques de abajo.
    limite = max(8.0, float(timeout or 25.0))
    deadline = time.monotonic() + limite
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

        # Un plazo único para los dos arranques. Antes cada uno tenía su
        # reloj (45 s y 30 s) y se encadenaban: `ensure(timeout=15)` tardaba
        # más de minuto y medio, y en el contenedor eso dejaba la tarea
        # eternamente en STARTING_OPENCODE.
        #
        # `opencode service` no existe en algunas versiones, y en un contenedor
        # sin gestor de servicios no puede funcionar.
        #
        # Se le da como mucho la mitad del plazo, y siempre dejando al menos
        # esa misma mitad para el servidor PROPIO: ese es el que funciona en
        # Docker, y si el primero se lleva el plazo entero no queda tiempo de
        # nada. Antes, entre esperar un registro que nunca llegaba y el reloj
        # fijo, al propio le tocaban menos de un segundo de 25.
        propio_minimo = max(8.0, limite * 0.5)
        info = None
        if time.monotonic() + propio_minimo < deadline:
            info = _arrancar_servicio_compartido(
                min(20.0, (deadline - time.monotonic()) - propio_minimo))
        if info:
            endpoint = endpoint_from(info)
            estado = health(endpoint["url"], endpoint["username"],
                            endpoint["password"], timeout=6.0)
            if estado.get("ok"):
                return {"ok": True, **endpoint, "source": "servicio compartido (nuevo)",
                        "pid": info.get("pid") or estado.get("pid"),
                        "version": estado.get("version", "")}

        info = _arrancar_propio(deadline)
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