"""Proveedor Groq: habla la API oficial, no un simulacro.

Groq expone una API compatible con OpenAI en
`https://api.groq.com/openai/v1`. Aqui se llama **por HTTP** con `urllib`, que
ya venia en la biblioteca estandar, por tres motivos:

1. El streaming es una lectura de Server-Sent Events linea a linea; el SDK la
   esconde detras de un iterador y hace falta ver cada trozo para emitirlo por
   el bus de eventos (JARVIS Live lo ve token a token).
2. La cancelacion es cerrar la respuesta a mitad: con un iterador del SDK eso no
   se puede garantizar.
3. El clasificador de errores necesita el cuerpo crudo y el estado HTTP, no una
   excepcion ya(envuelta.

Nada de esto anade dependencias: `urllib.request` viene en la stdlib.

La clave **nunca** se escribe en un log, ni se devuelve, ni viaja en un evento.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Optional

from config import Config

from .. import errors

log = logging.getLogger("brain.ai.groq")

PROVEEDOR = "groq"
BASE_POR_DEFECTO = "https://api.groq.com/openai/v1"

# Cabeceras de contenido de Groq: una peticion normal y una de listado.
_CT = "application/json"
_TIMEOUT_MINIMO = 2.0


def base_url() -> str:
    return (Config.GROQ_BASE_URL or BASE_POR_DEFECTO).rstrip("/")


def _clave() -> str:
    """La clave que se usa ahora: la de la prueba si hay, si no la de siempre."""
    return _clave_prueba or Config.GROQ_API_KEY or ""


def tiene_clave() -> bool:
    return bool(_clave())


def modelos_configurados() -> dict:
    """Los tres modelos que JARVIS usa, ya con sus tareas."""
    return {
        "default": Config.GROQ_DEFAULT_MODEL or Config.GROQ_MODEL_FAST,
        "fast": Config.GROQ_FAST_MODEL or Config.GROQ_MODEL_LIGHT,
        "reasoning": Config.GROQ_REASONING_MODEL or Config.GROQ_DEFAULT_MODEL
        or Config.GROQ_MODEL_FAST,
        "vision": Config.GROQ_MODEL_VISION or Config.GROQ_MODEL_FAST,
    }


# --------------------------------------------------------------------------- #
# Utilidades HTTP
# --------------------------------------------------------------------------- #
# Groq está detrás de Cloudflare, y Cloudflare rechaza por DEFECTO las
# peticiones cuya firma no parece la de un cliente real: código 1010
# ("browser signature banned"). `urllib` manda `User-Agent: Python-urllib/3.x`
# y con eso TODAS las llamadas devolvían 403, aunque la clave fuese correcta y
# la red fuese buena.
#
# Comprobado dentro del contenedor, con la misma clave y en el mismo instante:
#     python-urllib   -> HTTP 403  error code: 1010
#     curl/8.4.0      -> HTTP 200  (11 modelos)
#     openai-python   -> HTTP 200
#     httpx           -> HTTP 200
#
# El SDK de Groq (que usa httpx) sí pasaba; por eso el camino viejo del router
# funcionaba y el del gateway no, y el fallo parecía intermitente.
_USER_AGENT = "openai-python/1.40.0"


def _peticion(metodo: str, url: str, cuerpo: Optional[dict], *,
              timeout: float, stream: bool = False,
              cancelacion: Optional[threading.Event] = None):
    """Una llamada a la API de Groq. Devuelve la respuesta HTTP abierta."""
    if not tiene_clave():
        raise errors.AIError(errors.NO_KEY, "No hay clave de Groq puesta.",
                             PROVEEDOR)
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    cabeceras = {"Authorization": f"Bearer {_clave()}",
                "Accept": "text/event-stream" if stream else _CT,
                "User-Agent": _USER_AGENT}
    if datos is not None:
        cabeceras["Content-Type"] = _CT
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras,
                                      method=metodo)
    try:
        return urllib.request.urlopen(peticion, timeout=max(_TIMEOUT_MINIMO, timeout))
    except urllib.error.HTTPError as exc:
        cuerpo_error = ""
        try:
            cuerpo_error = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        espera = _retry_after(exc.headers)
        raise errors.desde_http(exc.code, cuerpo_error, PROVEEDOR, espera) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise errors.desde_excepcion(exc, PROVEEDOR) from None


def _retry_after(cabeceras) -> float:
    try:
        valor = (cabeceras or {}).get("Retry-After") or ""
        return max(0.0, float(valor))
    except (TypeError, ValueError):
        return 0.0


def listar_modelos(timeout: float = 12.0) -> list[dict]:
    """`GET /models`: la lista real de modelos que da el proveedor."""
    respuesta = _peticion("GET", f"{base_url()}/models", None, timeout=timeout)
    try:
        datos = json.loads(respuesta.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise errors.AIError(errors.MALFORMED, "La lista de modelos no es JSON",
                             PROVEEDOR, cuerpo=str(exc)) from None
    finally:
        _cerrar(respuesta)
    if isinstance(datos, dict):
        datos = datos.get("data") or []
    return [d for d in (datos or []) if isinstance(d, dict)]


def comprobar(clave_override: str = "", modelo: str = "",
              timeout: float = 15.0) -> dict:
    """Prueba de conexion real: un ping de 1 token.

    Es la unica llamada de coste que hace el gateway, y solo cuando se pide.
    Devuelve el estado real; si la clave no vale, lo dice con el codigo.
    """
    global _clave_prueba
    anterior, _clave_prueba = _clave_prueba, clave_override or None
    try:
        respuesta = _peticion(
            "POST", f"{base_url()}/chat/completions",
            {"model": modelo or modelos_configurados()["fast"],
             "messages": [{"role": "user", "content": "ok"}],
             "max_tokens": 1, "temperature": 0},
            timeout=timeout)
        try:
            cuerpo = json.loads(respuesta.read().decode("utf-8", "replace"))
        finally:
            _cerrar(respuesta)
        modelo_usado = ""
        if isinstance(cuerpo, dict):
            modelo_usado = str((cuerpo.get("model") or "")[:80])
        return {"ok": True, "estado": "CONNECTED", "modelo": modelo_usado,
                "detalle": "Groq ha contestado."}
    except errors.AIError as exc:
        return {"ok": False, "estado": _estado_de(exc.tipo), "modelo": modelo,
                "error": exc.para_human(), "tipo": exc.tipo, "codigo": exc.estado,
                "detalle": errors.sano(exc)}


# La clave que se usa en la prueba. None = la de configuracion. Se guarda solo
# durante la llamada y se borra en el finally: nunca se imprime ni se devuelve.
_clave_prueba: Optional[str] = None


def _estado_de(tipo: str) -> str:
    return {errors.AUTH_ERROR: "AUTH_ERROR", errors.FORBIDDEN: "AUTH_ERROR",
            errors.RATE_LIMITED: "RATE_LIMITED", errors.TIMEOUT: "DISCONNECTED",
            errors.NETWORK: "DISCONNECTED", errors.UNAVAILABLE: "ERROR",
            errors.NOT_FOUND: "ERROR", errors.NO_KEY: "DISCONNECTED",
            errors.MALFORMED: "ERROR"}.get(tipo, "ERROR")


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
def completar(mensajes: list[dict], modelo: str, *, temperature: float = 0.7,
              max_tokens: Optional[int] = None,
              vision: Optional[str] = None,
              tools: Optional[list] = None,
              timeout: float = 45.0,
              cancelar: Optional[threading.Event] = None) -> dict:
    """Una llamada sin streaming. Devuelve el texto y el uso real de tokens."""
    cuerpo = {"model": modelo, "messages": _preparar(mensajes, vision),
              "temperature": temperature}
    if max_tokens:
        cuerpo["max_tokens"] = int(max_tokens)
    if tools:
        cuerpo["tools"] = tools
        cuerpo["tool_choice"] = "auto"
    respuesta = _peticion("POST", f"{base_url()}/chat/completions", cuerpo,
                          timeout=timeout)
    try:
        crudo = respuesta.read()
    finally:
        _cerrar(respuesta)
    datos = _json(crudo)
    texto, llamadas = _elegir_mensaje(datos)
    if not texto and not llamadas:
        raise errors.AIError(errors.MALFORMED, "Groq no devolvio texto.",
                             PROVEEDOR, cuerpo=json.dumps(datos)[:400])
    return {"texto": texto, "tool_calls": llamadas, "usage": _uso(datos),
            "modelo": str(datos.get("model") or modelo), "raw": datos}


_ultimo_stream: dict = {}


def transmitir(mensajes: list[dict], modelo: str, *,
               temperature: float = 0.7, max_tokens: Optional[int] = None,
               vision: Optional[str] = None, tools: Optional[list] = None,
               timeout: float = 45.0,
               cancelar: Optional[threading.Event] = None):
    """Generador de trozos de texto (Server-Sent Events).

    Cada trozo sale en cuanto llega, que es lo que permite que JARVIS Live
    escriba la respuesta mientras se genera. Si el señor cancela, se para en el
    siguiente trozo y se cierra la respuesta.
    """
    cuerpo = {"model": modelo, "messages": _preparar(mensajes, vision),
              "temperature": temperature, "stream": True,
              "stream_options": {"include_usage": True}}
    if max_tokens:
        cuerpo["max_tokens"] = int(max_tokens)
    if tools:
        cuerpo["tools"] = tools
        cuerpo["tool_choice"] = "auto"
    respuesta = _peticion("POST", f"{base_url()}/chat/completions", cuerpo,
                          timeout=timeout, stream=True)
    texto = []
    uso: dict = {}
    global _ultimo_stream
    try:
        for linea in respuesta:
            if cancelar is not None and cancelar.is_set():
                log.info("peticion a Groq cancelada a mitad")
                _ultimo_stream = {"cancelado": True, "texto": "".join(texto),
                                  "usage": uso, "modelo": modelo}
                return
            crudo = linea.decode("utf-8", "replace").strip()
            if not crudo or not crudo.startswith("data:"):
                continue
            trozo = crudo[5:].strip()
            if trozo == "[DONE]":
                break
            try:
                evento = json.loads(trozo)
            except ValueError:
                log.debug("trozo ilegible de Groq, se ignora")
                continue
            if evento.get("usage"):
                uso = _uso(evento)
            for trozo_texto in _trozos(evento):
                texto.append(trozo_texto)
                yield trozo_texto
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Si se corta la conexion a mitad, lo que ha llegado hasta aqui no se
        # tira: se entrega como respuesta parcial y se avisa.
        raise errors.desde_excepcion(exc, PROVEEDOR) from None
    finally:
        _cerrar(respuesta)
    # Un generador no puede "devolver" un valor, asi que el resumen de la
    # transmision queda a mano para quien lo consume.
    _ultimo_stream = {"cancelado": False, "texto": "".join(texto), "usage": uso,
                      "modelo": modelo}
    log.debug("Groq en streaming: %d caracteres", len(_ultimo_stream["texto"]))


_ultimo_stream: dict = {}


def ultimo_stream() -> dict:
    """Texto y uso acumulados en la última transmisión de este proceso."""
    return dict(_ultimo_stream)


# --------------------------------------------------------------------------- #
# Lectura de la respuesta
# --------------------------------------------------------------------------- #
def _json(crudo: bytes) -> dict:
    try:
        datos = json.loads(crudo.decode("utf-8", "replace"))
    except ValueError as exc:
        raise errors.AIError(errors.MALFORMED, "Groq no devolvio JSON.",
                             PROVEEDOR, cuerpo=str(exc)) from None
    if not isinstance(datos, dict):
        raise errors.AIError(errors.MALFORMED, "Groq devolvio algo que no es un objeto.",
                             PROVEEDOR)
    return datos


def _elegir_mensaje(datos: dict) -> tuple[str, list]:
    """Saca el texto y las llamadas a herramienta de la respuesta."""
    opciones = datos.get("choices") or []
    if not opciones:
        return "", []
    opcion = opciones[0] if isinstance(opciones[0], dict) else {}
    mensaje = opcion.get("message") or {}
    texto = str(mensaje.get("content") or "").strip()
    llamadas = [c for c in (mensaje.get("tool_calls") or []) if isinstance(c, dict)]
    return texto, llamadas


def _trozos(evento: dict):
    """Los `delta.content` de un trozo de streaming."""
    opciones = evento.get("choices") or []
    if not opciones:
        return
    opcion = opciones[0] if isinstance(opciones[0], dict) else {}
    delta = opcion.get("delta") or {}
    contenido = delta.get("content")
    if isinstance(contenido, str) and contenido:
        yield contenido
    razonamiento = delta.get("reasoning") or delta.get("reasoning_content")
    # El razonamiento NO se enseña ni se habla: va al log y a las estadisticas,
    # pero la respuesta del señor es solo el contenido final (§17 del prompt).
    if isinstance(razonamiento, str) and razonamiento:
        log.debug("groq razonamiento: %d caracteres", len(razonamiento))


def _uso(datos: dict) -> dict:
    """Tokens reales que declara el proveedor. Si no los declara, no se inventan."""
    uso = datos.get("usage") or {}
    entrada = uso.get("prompt_tokens")
    salida = uso.get("completion_tokens")
    total = uso.get("total_tokens")
    if not isinstance(entrada, int):
        entrada = None
    if not isinstance(salida, int):
        salida = None
    if not isinstance(total, int):
        total = (entrada + salida) if isinstance(entrada, int) and isinstance(salida, int) else None
    # Groq no devuelve precio. Se dice "unknown" y no se calcula nada.
    return {"input_tokens": entrada, "output_tokens": salida, "total_tokens": total,
            "cost": "unknown"}


def _preparar(mensajes: list[dict], vision: Optional[str]) -> list[dict]:
    """Si hay imagen, se manda en el ultimo mensaje de usuario (formato Groq)."""
    if not vision:
        return [dict(m) for m in mensajes]
    sin_sistema = [m for m in mensajes if m.get("role") != "system"]
    sistema = [m for m in mensajes if m.get("role") == "system"]
    ultimo = sin_sistema[-1] if sin_sistema else {"role": "user", "content": ""}
    resto = sin_sistema[:-1]
    contenido = [{"type": "text", "text": ultimo.get("content") or ""},
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/png;base64,{vision}"}}]
    return sistema + resto + [{"role": ultimo.get("role", "user"),
                               "content": contenido}]


def _cerrar(respuesta) -> None:
    try:
        respuesta.close()
    except Exception:  # noqa: BLE001
        pass