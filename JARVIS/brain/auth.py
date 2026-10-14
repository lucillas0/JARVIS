"""brain/auth.py — un token compartido para el WebSocket y las rutas que tocan
secretos.

Por que hace falta, con lo que se comprobo:

    El WebSocket aceptaba la conexion sin mirar nada, y los WebSocket NO están
    sujetos a la Same-Origin Policy: cualquier pagina que visitara el usuario
    podia hacer new WebSocket('ws://127.0.0.1:8000/ws') y mandar preguntas,
    pedir ficheros o responder a una peticion de confirmacion del Risk Engine.

    Aparte, /api/config/exportar-claves devolvia las claves de API en claro a
    cualquiera que llegara al puerto, y /api/camara/* iba con
    Access-Control-Allow-Origin: *, con lo que una web cualquiera ademas podia
    pedir una foto de la camara.

Que protege y que no:

    El token protege frente a OTRAS PAGINAS del navegador. No es un secreto de
    por si: se guarda en el volumen, en el directorio de datos, y se inyecta en
    el HTML que sirve el propio JARVIS. Quien ya puede abrir http://localhost:8000
    puede leerlo, y a ese usuario ya le damos el cerebro entero en su pantalla.

    Lo que hace es que un sitio ajeno no pueda hablar con tu cerebro solo porque
    tengas una pestana abierta.

    Origenes permitidos aparte: las paginas del JARVIS (file:// y http en
    localhost y en la IP del propio equipo). Eso tapa el ataque aunque alguien
    se saltase el token.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Iterable

_TOKEN_CACHE: dict[str, str] = {}


def token_path() -> Path:
    """Donde vive el token. En DATA_DIR, que es lo unico persistente."""
    try:
        from config import DATA_DIR
        return Path(DATA_DIR) / "jarvis.token"
    except Exception:
        return Path(os.path.expanduser("~")) / ".jarvis" / "jarvis.token"


def token() -> str:
    """El token, creandolo la primera vez (y con permisos de solo propietario)."""
    ruta = token_path()
    if ruta in _TOKEN_CACHE:
        return _TOKEN_CACHE[ruta]
    try:
        if ruta.is_file():
            t = ruta.read_text(encoding="utf-8").strip()
            if t:
                _TOKEN_CACHE[ruta] = t
                return t
    except OSError:
        pass
    t = secrets.token_urlsafe(32)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(t + "\n", encoding="utf-8")
        try:
            os.chmod(ruta, 0o600)
        except OSError:
            pass  # en NTFS chmod no hace nada
    except OSError:
        # Sin permisos de escritura se acepta en memoria: el token valdrá
        # mientras viva el proceso, que es mejor que no aceptar conexiones.
        pass
    _TOKEN_CACHE[ruta] = t
    return t


def coincide(candidato: str | None) -> bool:
    """Comparacion en tiempo constante, para no filtrar el token a palos."""
    if not candidato:
        return False
    return hmac.compare_digest(str(candidato).strip(), token())


# Orígenes que SÍ pueden hablar con el cerebro. Todo lo demás se rechaza, y con
# esto el ataque por pestaña abierta queda tapado aunque el token se filtrara.
#
# "null" está porque Chromium manda Origin: null en las páginas file://, que es
# como se carga la interfaz de Electron.
_ORIGENES_PERMITIDOS = (
    "http://localhost", "http://127.0.0.1", "file://", "null",
    "https://localhost", "https://127.0.0.1",
)


def origen_permitido(origen: str | None) -> bool:
    """Sin cabecera Origin (un cliente de escritorio, por ejemplo) o de una app
    nuestra: permitido."""
    if not origen:
        return True
    o = str(origen).strip().lower()
    for ok in _ORIGENES_PERMITIDOS:
        if o == ok or o.startswith(ok + ":") or o.startswith(ok + "/"):
            return True
    # La IP del propio equipo, para cuando se abre por la red local.
    if o.startswith("http://") or o.startswith("https://"):
        try:
            import socket
            host = o.split("//", 1)[1].split("/", 1)[0].split(":")[0]
            if host in ("localhost", "127.0.0.1", "::1"):
                return True
            ip = socket.gethostbyname(socket.gethostname())
            if host == ip:
                return True
        except Exception:
            return False
    return False


def token_por_cabecera(cabeceras) -> str | None:
    """Saca el token de donde lo mande el cliente: cabecera o query."""
    if not cabeceras:
        return None
    # Authorization: Bearer <token>
    try:
        auth = cabeceras.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
    except Exception:
        pass
    # X-Jarvis-Token: <token>
    try:
        x = cabeceras.get("x-jarvis-token")
        if x:
            return x.strip()
    except Exception:
        pass
    return None


def resumen_para_logs(mensaje: str) -> str:
    """Para no imprimir el token en un log por error."""
    return mensaje.replace(token(), "<token>") if token() else mensaje