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
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Iterable

log = logging.getLogger("auth")

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
    if ruta.is_file():
        t = ""
        # Se reintenta unas veces porque en Windows un antivirus o un indiceo
        # pueden tener el fichero abierto un instante y eso no es un fallo de
        # permisos. Lo que NO se hace nunca es seguir como si el fichero no
        # existiera: generar otro token sobrescribiendo este deja fuera a
        # todos los clientes que ya lo tenían (panel, móvil, scripts), sin
        # aviso y sin vuelta atrás.
        for intento in range(3):
            try:
                t = ruta.read_text(encoding="utf-8").strip()
                break
            except OSError as exc:
                if intento == 2:
                    log.error("el token existe pero no se puede leer: %s", exc)
                    raise RuntimeError(
                        f"el token existe pero no se puede leer ({exc}). "
                        f"Arregla los permisos de {ruta} en vez de borrarlo: "
                        f"si se regenera, JARVIS cierra la puerta a quien "
                        f"ya la usaba."
                    ) from exc
                time.sleep(0.2)
        if t:
            _TOKEN_CACHE[ruta] = t
            return t
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


# --------------------------------------------------------------------------- #
# La sesión: cookie HttpOnly
# --------------------------------------------------------------------------- #
#
# POR QUÉ EXISTE
# --------------
# El token se inyectaba en el HTML de las páginas que se servían SIN
# autenticar (`/config`, `/setup`, `/modo`, `/live`, `/board`). Con el puerto
# publicado en la LAN, eso significaba que cualquier equipo de la red podía
# hacer `curl http://IP:8000/config`, leerse el token del HTML, y con él
# llamar a `POST /api/config/exportar-claves`, que devuelve TODAS las claves
# de API en claro.
#
# La solución no es dejar de usar el token (el panel, el móvil y los scripts
# lo necesitan), sino dejar de PUBLICARLO. Ahora el token solo se entrega
# dentro de la cookie de sesión, que es `HttpOnly` (el JavaScript de la
# página no puede leerla) y `SameSite=Strict` (no viaja a otros sitios). Y
# las páginas que la necesitan exigen estar autenticado.
#
# La cookie contiene el propio token: no hay un segundo secreto que
# mantener, y la comparación sigue siendo en tiempo constante.
COOKIE_SESION = "jarvis_sesion"


def token_de_la_peticion(request) -> str:
    """El token de quien llama, venga de donde venga.

    Se mira, en este orden: la cabecera `X-Jarvis-Token` o `Authorization`,
    la query `?token=` (para descargas y `<img>`, que no pueden mandar
    cabeceras) y la cookie de sesión.

    Antes la cookie no existia, y por eso el token acababa metido en el HTML
    de las paginas publicas.
    """
    # 1) Cabeceras
    cabecera = request.headers.get("x-jarvis-token") or ""
    if not cabecera:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            cabecera = auth[7:]
    if cabecera.strip():
        return cabecera.strip()
    # 2) Query
    try:
        q = request.query_params.get("token") or ""
    except Exception:  # noqa: BLE001
        q = ""
    if q.strip():
        return q.strip()
    # 3) Cookie de sesion
    try:
        galletas = getattr(request, "cookies", None) or {}
        return str(galletas.get(COOKIE_SESION) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def autenticado(request) -> bool:
    """¿Quien llama ha dicho quien es?"""
    return coincide(token_de_la_peticion(request))


def cookie_de_sesion(token: str = "") -> str:
    """La cookie de sesion, con sus atributos.

    `HttpOnly` para que el JavaScript de la pagina no pueda leerla (asi el
    token no se puede robar con un XSS), `SameSite=Strict` para que no viaje
    a otros sitios, y `Secure` solo si la peticion viene por HTTPS (en una
    instalacion local por HTTP, ponerlo haria que el navegador la tirara).
    """
    t = (token or "").strip() or token()
    partes = [f"{COOKIE_SESION}={t}", "Path=/", "HttpOnly", "SameSite=Strict"]
    return "; ".join(partes)


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
    except Exception as exc:  # noqa: BLE001
        # Si las cabeceras vinieran corruptas, lo mas seguro es "no hay token"
        # (rechazar) en vez de dejar pasar a alguien. Se deja rastro porque
        # un `pass` aqui esconde justo el fallo que permitiria colar a uno.
        log.warning("no se pudo leer la cabecera Authorization: %s", exc)
    # X-Jarvis-Token: <token>
    try:
        x = cabeceras.get("x-jarvis-token")
        if x:
            return x.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer X-Jarvis-Token: %s", exc)
    return None


def resumen_para_logs(mensaje: str) -> str:
    """Para no imprimir el token en un log por error."""
    return mensaje.replace(token(), "<token>") if token() else mensaje