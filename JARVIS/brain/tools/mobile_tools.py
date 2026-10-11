# -*- coding: utf-8 -*-
"""Control de móviles reales con mobile-mcp (servidor MCP en Node).

El servidor no se instala dentro de la app: vive en %APPDATA%\\JARVIS\\mobile-mcp
y se instala con npm la primera vez que hace falta. Se le habla por MCP
(Streamable HTTP) en 127.0.0.1, con token propio para que ningún otro proceso
local pueda manejar el teléfono.

Android necesita adb y "depuración USB" activada en el móvil. iOS necesita macOS
con Xcode, así que en Windows solo hay Android.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger("tools.mobile")

NPM = r"C:\Program Files\nodejs\npm.cmd"
PAQUETE = "@mobilenext/mobile-mcp@latest"
PUERTO = int(os.environ.get("JARVIS_MOBILE_PORT", "8765") or 8765)
TIMEOUT = 40
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_proceso = None
_sesion = None


def _appdata() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "JARVIS")


def _dir_instalacion() -> str:
    return os.path.join(_appdata(), "mobile-mcp")


def _dir_datos() -> str:
    return os.path.join(_appdata(), "brain-data")


def _bin() -> str:
    return os.path.join(_dir_instalacion(), "node_modules", ".bin",
                        "mcp-server-mobile.cmd")


def _token() -> str:
    """Token propio del servidor: evita que otro programa local lo use."""
    ruta = os.path.join(_dir_datos(), "mobile_mcp_token.txt")
    try:
        os.makedirs(_dir_datos(), exist_ok=True)
        if os.path.isfile(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
    except Exception as e:
        log.warning("no pude leer el token de mobile-mcp: %s", e)
    t = os.urandom(24).hex()
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(t)
    except Exception as e:
        log.warning("no pude guardar el token de mobile-mcp: %s", e)
    return t


def ensure_instalado() -> str:
    """Deja el servidor instalado. Devuelve la ruta del ejecutable."""
    if os.path.isfile(_bin()):
        return _bin()
    d = _dir_instalacion()
    os.makedirs(d, exist_ok=True)
    exe = NPM if os.path.isfile(NPM) else (shutil.which("npm.cmd")
                                           or shutil.which("npm"))
    if not exe:
        raise RuntimeError("No encuentro Node/npm. Instala Node.js 20 o superior.")
    log.info("Instalando mobile-mcp con npm (la primera vez tarda un poco)...")
    subprocess.run([exe, "install", PAQUETE, "--no-fund", "--no-audit"],
                   cwd=d, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=900, creationflags=_NO_WINDOW)
    if not os.path.isfile(_bin()):
        raise RuntimeError("La instalación de mobile-mcp no ha salido bien.")
    return _bin()


def buscar_adb() -> str | None:
    """Localiza adb.exe: en el PATH, por winget o en el SDK de Android."""
    for c in ("adb.exe", "adb"):
        p = shutil.which(c)
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    base = os.environ.get("LOCALAPPDATA", "")
    rutas = []
    if base:
        paquetes = os.path.join(base, "Microsoft", "WinGet", "Packages")
        if os.path.isdir(paquetes):
            for d in os.listdir(paquetes):
                if d.lower().startswith("google.platformtools"):
                    rutas.append(os.path.join(paquetes, d, "platform-tools",
                                              "adb.exe"))
        rutas.append(os.path.join(base, "Android", "Sdk", "platform-tools",
                                  "adb.exe"))
    for r in rutas:
        if os.path.isfile(r):
            return r
    return None


def _adb_dispositivos() -> dict:
    """Diagnóstico con adb: útil cuando mobile-mcp no ve ningún móvil."""
    adb = buscar_adb()
    if not adb:
        return {"adb": False,
                "aviso": "No está instalado 'adb' (Platform Tools de Android)."}
    try:
        r = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           creationflags=_NO_WINDOW)
        salida = (r.stdout or "").strip()
        lineas = [l for l in salida.splitlines()[1:] if l.strip()]
        return {"adb": True, "ruta": adb, "salida": salida[:600],
                "conectados": len(lineas)}
    except Exception as e:
        return {"adb": True, "ruta": adb, "error": str(e)[:200]}


def _vivo(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def _node() -> str:
    if os.path.isfile(r"C:\Program Files\nodejs\node.exe"):
        return r"C:\Program Files\nodejs\node.exe"
    for c in ("node.exe", "node"):
        p = shutil.which(c)
        if p and os.path.isfile(p):
            return p
    raise RuntimeError("No encuentro Node.js. Instala Node.js 20 o superior.")


def _entrada() -> str:
    return os.path.join(_dir_instalacion(), "node_modules", "@mobilenext",
                        "mobile-mcp", "lib", "index.js")


def _arrancar() -> None:
    global _proceso, _sesion
    _sesion = None
    ensure_instalado()
    env = dict(os.environ)
    env["MOBILEMCP_DISABLE_TELEMETRY"] = "1"
    env["MOBILEMCP_AUTH"] = _token()
    adb = buscar_adb()
    if adb:
        env["PATH"] = os.path.dirname(adb) + os.pathsep + env.get("PATH", "")
    log_dir = _dir_datos()
    os.makedirs(log_dir, exist_ok=True)
    salida = open(os.path.join(log_dir, "mobile_mcp.log"), "ab")
    # Se arranca node directamente (no a través del .cmd) para que el proceso
    # que llevamos sea el bueno y se pueda parar al salir. Se le deja la
    # entrada estándar abierta porque si se la cierra, se marcha solo.
    _proceso = subprocess.Popen([_node(), _entrada(), "--listen", str(PUERTO)],
                                stdin=subprocess.PIPE, stdout=salida,
                                stderr=salida, cwd=_dir_instalacion(), env=env,
                                creationflags=_NO_WINDOW)
    for _ in range(TIMEOUT):
        if _vivo(PUERTO):
            log.info("mobile-mcp escuchando en el puerto %s", PUERTO)
            return
        if _proceso.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError("El servidor mobile-mcp no arrancó. Mira mobile_mcp.log "
                       "en brain-data.")


def _parar() -> None:
    global _proceso
    p = _proceso
    _proceso = None
    if p is None or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


atexit.register(_parar)


def asegurar_servidor() -> None:
    if not _vivo(PUERTO):
        _arrancar()


def _post(metodo: str, params=None, notificar: bool = False) -> dict:
    global _sesion
    url = f"http://127.0.0.1:{PUERTO}/mcp"
    cuerpo: dict = {"jsonrpc": "2.0", "method": metodo}
    if not notificar:
        cuerpo["id"] = int(time.time() * 1000) % 10_000_000
    if params is not None:
        cuerpo["params"] = params
    cab = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream",
           "Authorization": "Bearer " + _token()}
    if _sesion:
        cab["Mcp-Session-Id"] = _sesion
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(),
                                 headers=cab, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        nuevo = r.headers.get("Mcp-Session-Id")
        if nuevo:
            _sesion = nuevo
        bruto = r.read().decode("utf-8", "replace")
        ct = r.headers.get("Content-Type", "")
    if not bruto.strip():
        return {}
    if "text/event-stream" in ct:
        for linea in bruto.splitlines():
            if linea.startswith("data:"):
                return json.loads(linea[5:].strip())
        return {}
    return json.loads(bruto)


def _texto(resultado: dict) -> str:
    partes = []
    for c in (resultado.get("content") or []):
        if c.get("type") == "text":
            partes.append(c.get("text", ""))
    return "\n".join(partes).strip()


def _json_de(texto: str):
    try:
        return json.loads(texto)
    except Exception:
        return {"texto": texto}


def _llamar(nombre_mcp: str, argumentos: dict) -> dict:
    """Llama a una herramienta del servidor MCP y devuelve lo que responde."""
    global _sesion
    asegurar_servidor()
    if not _sesion:
        _post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "jarvis", "version": "1"}})
        _post("notifications/initialized", notificar=True)
    try:
        r = _post("tools/call", {"name": nombre_mcp, "arguments": argumentos})
    except urllib.error.HTTPError:
        _sesion = None
        r = _post("tools/call", {"name": nombre_mcp, "arguments": argumentos})
    if r.get("error"):
        raise RuntimeError(f"mobile-mcp: {r['error'].get('message', r['error'])}")
    res = r.get("result") or {}
    if res.get("isError"):
        raise RuntimeError(_texto(res) or "mobile-mcp falló")
    return _json_de(_texto(res))


def _dispositivos() -> list:
    d = _llamar("mobile_list_available_devices", {})
    if isinstance(d, dict):
        lista = d.get("devices") or []
    elif isinstance(d, list):
        lista = d
    else:
        lista = []
    return lista if isinstance(lista, list) else []


def _id_de(entrada) -> str:
    if isinstance(entrada, str):
        return entrada
    if isinstance(entrada, dict):
        for clave in ("id", "deviceId", "device_id", "udid", "serial", "name"):
            v = entrada.get(clave)
            if v:
                return str(v)
    return str(entrada)


def _movil(params: dict) -> str:
    """Elige el móvil: el que le digan o el primero que haya."""
    pedido = (params.get("device") or "").strip()
    if pedido:
        return pedido
    lista = _dispositivos()
    if not lista:
        raise ValueError(
            "No veo ningún móvil. Conéctalo por USB y en el móvil activa "
            "Ajustes > Acerca del teléfono > pulsar 7 veces en 'Número de "
            "compilación' y luego Opciones de desarrollador > Depuración USB. "
            "En iOS hace falta un Mac con Xcode.")
    return _id_de(lista[0])


def _p(params: dict, *nombres, default=None):
    for n in nombres:
        v = params.get(n)
        if v not in (None, ""):
            return v
    return default


# --------------------------------------------------------------------------- #
# Herramientas
# --------------------------------------------------------------------------- #

def _h_dispositivos(params: dict) -> dict:
    lista = _dispositivos()
    salida = {"dispositivos": [{"id": _id_de(d),
                                "detalle": d if isinstance(d, dict) else str(d)}
                               for d in lista],
              "adb": _adb_dispositivos()}
    if not lista:
        salida["aviso"] = ("Ningún móvil conectado. Mira el apartado 'adb' para "
                           "ver si el teléfono aparece.")
    return salida


def _h_apps(params: dict) -> dict:
    d = _movil(params)
    r = _llamar("mobile_list_apps", {"device": d})
    filtro = (params.get("filtro") or "").strip().lower()
    if filtro and isinstance(r, dict) and isinstance(r.get("apps"), list):
        r["apps"] = [a for a in r["apps"]
                     if filtro in json.dumps(a, ensure_ascii=False).lower()]
    return {"device": d, "apps": r}


def _h_pantalla(params: dict) -> dict:
    d = _movil(params)
    args = {"device": d}
    fmt = (params.get("formato") or "").strip()
    if fmt:
        args["format"] = fmt
    return {"device": d,
            "pantalla": _llamar("mobile_list_elements_on_screen", args)}


def _h_captura(params: dict) -> dict:
    d = _movil(params)
    carpeta = os.path.join(_dir_datos(), "mobile")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"captura-{int(time.time())}.png")
    _llamar("mobile_save_screenshot", {"device": d, "saveTo": ruta})
    return {"device": d, "captura": ruta,
            "nota": "Imagen de la pantalla del móvil, guardada en ese fichero."}


def _h_tapar(params: dict) -> dict:
    d = _movil(params)
    args = {"device": d}
    ref = _p(params, "ref")
    if ref:
        args["ref"] = str(ref)
    else:
        args["x"] = int(_p(params, "x", default=0) or 0)
        args["y"] = int(_p(params, "y", default=0) or 0)
    return {"device": d,
            "hecho": _llamar("mobile_click_on_screen_at_coordinates", args)}


def _h_doble(params: dict) -> dict:
    d = _movil(params)
    return {"device": d, "hecho": _llamar("mobile_double_tap_on_screen", {
        "device": d, "x": int(_p(params, "x", default=0) or 0),
        "y": int(_p(params, "y", default=0) or 0)})}


def _h_pulsar_largo(params: dict) -> dict:
    d = _movil(params)
    args = {"device": d, "x": int(_p(params, "x", default=0) or 0),
            "y": int(_p(params, "y", default=0) or 0)}
    dur = _p(params, "duracion", "duration")
    if dur:
        args["duration"] = int(dur)
    return {"device": d,
            "hecho": _llamar("mobile_long_press_on_screen_at_coordinates", args)}


def _h_deslizar(params: dict) -> dict:
    d = _movil(params)
    args = {"device": d,
            "direction": str(_p(params, "direccion", "direction", default="up"))}
    for k_es, k_mcp in (("x", "x"), ("y", "y"), ("distancia", "distance")):
        v = _p(params, k_es, k_mcp)
        if v not in (None, ""):
            args[k_mcp] = int(v)
    return {"device": d, "hecho": _llamar("mobile_swipe_on_screen", args)}


def _h_escribir(params: dict) -> dict:
    d = _movil(params)
    texto = _p(params, "texto", "text")
    if texto in (None, ""):
        raise ValueError("Falta el texto a escribir.")
    args = {"device": d, "text": str(texto)}
    if _p(params, "enviar", "submit") not in (None, "", False, "false"):
        args["submit"] = True
    return {"device": d, "hecho": _llamar("mobile_type_keys", args)}


def _h_boton(params: dict) -> dict:
    d = _movil(params)
    boton = str(_p(params, "boton", "button", default="HOME")).upper()
    return {"device": d,
            "hecho": _llamar("mobile_press_button",
                             {"device": d, "button": boton})}


def _h_app(params: dict) -> dict:
    d = _movil(params)
    accion = str(_p(params, "accion", "action", default="abrir")).lower()
    paquete = _p(params, "paquete", "package", "packageName")
    if not paquete:
        raise ValueError("Falta el nombre del paquete de la app.")
    if accion in ("cerrar", "close", "terminate"):
        return {"device": d,
                "hecho": _llamar("mobile_terminate_app",
                                 {"device": d, "packageName": str(paquete)})}
    args = {"device": d, "packageName": str(paquete)}
    loc = _p(params, "idioma", "locale")
    if loc:
        args["locale"] = str(loc)
    return {"device": d, "hecho": _llamar("mobile_launch_app", args)}


def _h_url(params: dict) -> dict:
    d = _movil(params)
    url = _p(params, "url")
    if not url:
        raise ValueError("Falta la URL.")
    return {"device": d, "hecho": _llamar("mobile_open_url",
                                           {"device": d, "url": str(url)})}


def _h_primer_plano(params: dict) -> dict:
    d = _movil(params)
    return {"device": d,
            "app": _llamar("mobile_get_foreground_app", {"device": d})}


def _h_pegador(params: dict) -> dict:
    d = _movil(params)
    texto = _p(params, "texto", "text")
    args = {"device": d}
    if texto not in (None, ""):
        args["text"] = str(texto)
    return {"device": d, "pegador": _llamar("mobile_clipboard", args)}


def _h_ubicacion(params: dict) -> dict:
    d = _movil(params)
    lat = _p(params, "latitud", "latitude")
    lon = _p(params, "longitud", "longitude")
    args = {"device": d}
    if lat not in (None, "") and lon not in (None, ""):
        args["latitude"] = float(lat)
        args["longitude"] = float(lon)
    return {"device": d,
            "ubicacion": _llamar("mobile_set_location", args)}


def _h_logs(params: dict) -> dict:
    d = _movil(params)
    args = {"device": d}
    lim = _p(params, "limite", "limit")
    if lim not in (None, ""):
        args["limit"] = int(lim)
    filt = _p(params, "filtro", "filter")
    if filt:
        args["filter"] = str(filt)
    return {"device": d, "logs": _llamar("mobile_get_device_logs", args)}


def _h_pantalla_info(params: dict) -> dict:
    d = _movil(params)
    return {"device": d,
            "tamano": _llamar("mobile_get_screen_size", {"device": d}),
            "orientacion": _llamar("mobile_get_orientation", {"device": d})}


def _h_girar(params: dict) -> dict:
    d = _movil(params)
    ori = str(_p(params, "orientacion", "orientation",
                 default="portrait")).lower()
    return {"device": d, "hecho": _llamar("mobile_set_orientation",
                                           {"device": d, "orientation": ori})}


def _h_instalar(params: dict) -> dict:
    d = _movil(params)
    ruta = _p(params, "fichero", "path", "file")
    if not ruta:
        raise ValueError("Falta la ruta del fichero .apk.")
    return {"device": d, "hecho": _llamar("mobile_install_app",
                                           {"device": d, "path": str(ruta)})}


# Las llamadas van por red y arrancan un proceso: se lanzan en un hilo para no
# congelar el cerebro mientras habla con el teléfono.
def _async(fn):
    async def envoltura(params: dict) -> dict:
        return await asyncio.to_thread(fn, params)
    envoltura.__name__ = fn.__name__
    return envoltura


MOBILE_TOOLS = [
    {"name": "mobile_dispositivos", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Móviles y tablets que puedo controlar ahora mismo, y si adb los ve.",
     "label": "Mirando el móvil", "handler": _async(_h_dispositivos)},

    {"name": "mobile_pantalla", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional", "formato": "str opcional"},
     "description": "Qué hay ahora en la pantalla del móvil: elementos con sus "
                    "coordenadas y referencias. Es lo primero que hay que mirar "
                    "antes de tocar nada.",
     "label": "Leyendo la pantalla", "handler": _async(_h_pantalla)},

    {"name": "mobile_pantalla_info", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional"},
     "description": "Tamaño en píxeles y orientación del móvil.",
     "label": "Midiendo la pantalla", "handler": _async(_h_pantalla_info)},

    {"name": "mobile_captura", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional"},
     "description": "Guardo una foto de la pantalla del móvil y te digo dónde está el fichero.",
     "label": "Capturando la pantalla", "handler": _async(_h_captura)},

    {"name": "mobile_apps", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional", "filtro": "str opcional"},
     "description": "Apps instaladas en el móvil, con filtro opcional por nombre.",
     "label": "Listando apps", "handler": _async(_h_apps)},

    {"name": "mobile_tapar", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional", "x": "int", "y": "int",
                    "ref": "str opcional"},
     "description": "Toco la pantalla del móvil. Mejor con 'ref' (el identificador "
                    "que devuelve mobile_pantalla) que con coordenadas.",
     "label": "Tocando la pantalla", "handler": _async(_h_tapar)},

    {"name": "mobile_doble_toque", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional", "x": "int", "y": "int"},
     "description": "Doble toque en unas coordenadas del móvil.",
     "label": "Dando doble toque", "handler": _async(_h_doble)},

    {"name": "mobile_pulsar_largo", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional", "x": "int", "y": "int",
                    "duracion": "int opcional"},
     "description": "Mantengo el dedo en unas coordenadas del móvil.",
     "label": "Mantengo pulsado", "handler": _async(_h_pulsar_largo)},

    {"name": "mobile_deslizar", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional",
                    "direccion": "up/down/left/right", "distancia": "int opcional"},
     "description": "Deslizo la pantalla del móvil hacia un lado.",
     "label": "Deslizando", "handler": _async(_h_deslizar)},

    {"name": "mobile_escribir", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"texto": "str", "device": "str opcional",
                    "enviar": "bool opcional"},
     "description": "Escribo texto en el móvil donde esté el cursor.",
     "label": "Escribiendo", "handler": _async(_h_escribir)},

    {"name": "mobile_boton", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"boton": "HOME/BACK/VOLUME_UP/VOLUME_DOWN/ENTER...",
                    "device": "str opcional"},
     "description": "Pulso un botón físico del móvil (inicio, atrás, volumen...).",
     "label": "Pulsando un botón", "handler": _async(_h_boton)},

    {"name": "mobile_app", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"paquete": "str (nombre del paquete)",
                    "accion": "abrir/cerrar", "device": "str opcional"},
     "description": "Abro o cierro una app del móvil por su nombre de paquete.",
     "label": "Abriendo la app", "handler": _async(_h_app)},

    {"name": "mobile_url", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"url": "str", "device": "str opcional"},
     "description": "Abro una web en el navegador del móvil.",
     "label": "Abriendo la web", "handler": _async(_h_url)},

    {"name": "mobile_primer_plano", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"device": "str opcional"},
     "description": "Qué app está abierta ahora mismo en el móvil.",
     "label": "Mirando qué está abierto", "handler": _async(_h_primer_plano)},

    {"name": "mobile_pegador", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"texto": "str opcional", "device": "str opcional"},
     "description": "Leo el portapapeles del móvil, o copio un texto en él. Solo "
                    "si me lo pides: suele llevar contraseñas.",
     "label": "Mirando el portapapeles", "handler": _async(_h_pegador)},

    {"name": "mobile_ubicacion", "risk_level": "high",
     "requires_confirmation": True,
     "confirm_text": "¿Fingo la posición GPS del móvil?",
     "parameters": {"latitud": "float", "longitud": "float",
                    "device": "str opcional"},
     "description": "Cambio la posición que dice el móvil. Sin coordenadas lo limpio.",
     "label": "Cambiando la ubicación", "handler": _async(_h_ubicacion)},

    {"name": "mobile_logs", "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"limite": "int opcional", "filtro": "str opcional",
                    "device": "str opcional"},
     "description": "Registro del sistema del móvil (logcat en Android).",
     "label": "Leyendo el registro", "handler": _async(_h_logs)},

    {"name": "mobile_girar", "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"orientacion": "portrait/landscape", "device": "str opcional"},
     "description": "Giro la pantalla del móvil.",
     "label": "Girando el móvil", "handler": _async(_h_girar)},

    {"name": "mobile_instalar", "risk_level": "high",
     "requires_confirmation": True,
     "confirm_text": "¿Instalo ese fichero en el móvil?",
     "parameters": {"fichero": "str (ruta del .apk)", "device": "str opcional"},
     "description": "Instalo un .apk desde el ordenador en el móvil.",
     "label": "Instalando en el móvil", "handler": _async(_h_instalar)},
]
