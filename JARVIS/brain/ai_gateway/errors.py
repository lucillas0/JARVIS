"""Qué ha fallado exactamente, y qué se puede decir de ello en voz alta.

Un fallo de proveedor llega como un `HTTPError` con un cuerpo JSON cualquiera.
Aquí se traduce a un tipo cerrado: el gateway decide con eso si reintenta, si
cambia de proveedor o si se rinde, y JARVIS nunca repite el error tal cual,
porque un proveedor puede devolver la clave dentro del mensaje.

Regla de oro: `sano()` no deja pasar ni la clave ni nada que huela a cabecera.
"""
from __future__ import annotations

import json
import re
from typing import Optional

# Tipos de fallo que el gateway entiende. Los de HTTP se deducen del estado;
# los de red y de contenido se declaran al lanzar.
AUTH_ERROR = "auth_error"            # 401: clave ausente, mala o caducada
FORBIDDEN = "forbidden"              # 403: la clave no puede usar eso
NOT_FOUND = "not_found"              # 404: el modelo no existe en el proveedor
RATE_LIMITED = "rate_limited"        # 429: cuota o peticiones por minuto
TIMEOUT = "timeout"                  # se pasó el plazo
NETWORK = "network"                  # DNS, TLS, conexión cortada
UNAVAILABLE = "unavailable"          # 5xx del proveedor
MALFORMED = "malformed"              # llegó algo que no es la respuesta esperada
CANCELLED = "cancelled"              # lo pidió el señor o el sistema
NO_KEY = "no_key"                    # no hay clave configurada

CLASES = (AUTH_ERROR, FORBIDDEN, NOT_FOUND, RATE_LIMITED, TIMEOUT, NETWORK,
          UNAVAILABLE, MALFORMED, CANCELLED, NO_KEY)

# Lo que se reintenta: son fallos de paso, no de fondo. Un 401 no mejora solo.
REINTENTABLES = (RATE_LIMITED, UNAVAILABLE, NETWORK, TIMEOUT)

# Lo que cambia de proveedor: un 401 con la clave mala no se arregla cambiando
# de modelo dentro de Groq, pero sí se arregla yendo a otro proveedor.
CAMBIAN_PROVEEDOR = (AUTH_ERROR, FORBIDDEN, NOT_FOUND, RATE_LIMITED, TIMEOUT,
                     NETWORK, UNAVAILABLE, MALFORMED)

_ESTADO_A_TIPO = {400: MALFORMED, 401: AUTH_ERROR, 403: FORBIDDEN, 404: NOT_FOUND,
                  408: TIMEOUT, 422: MALFORMED, 429: RATE_LIMITED, 408.5: TIMEOUT}


class AIError(Exception):
    """Fallo de un proveedor, ya clasificado y sin secretos."""

    def __init__(self, tipo: str, mensaje: str = "", proveedor: str = "",
                 estado: Optional[int] = None, retry_after: float = 0.0,
                 cuerpo: str = ""):
        self.tipo = tipo if tipo in CLASES else MALFORMED
        self.proveedor = proveedor
        self.estado = estado
        self.retry_after = retry_after
        # `cuerpo` es el texto crudo del proveedor, bounded y sin clave: sirve
        # para el registro, nunca para hablar con el señor.
        self.cuerpo = _cortar(cuerpo, 600)
        super().__init__(sano(mensaje or self.tipo))

    @property
    def reintentable(self) -> bool:
        return self.tipo in REINTENTABLES

    @property
    def cambia_proveedor(self) -> bool:
        return self.tipo in CAMBIAN_PROVEEDOR

    def para_human(self) -> str:
        """Una frase, sin jerga y sin secretos, para decirla en voz alta."""
        return {
            AUTH_ERROR: "La clave de {p} no vale: revísala en /config.",
            FORBIDDEN: "{p} no me deja usar eso con la clave que hay.",
            NOT_FOUND: "El modelo que le pido a {p} ya no existe.",
            RATE_LIMITED: "{p} me ha dicho que no hay cuota ahora mismo.",
            TIMEOUT: "{p} ha tardado demasiado y lo he parado.",
            NETWORK: "No he podido ni hablar con {p}.",
            UNAVAILABLE: "{p} está fallingando por dentro ahora mismo.",
            MALFORMED: "{p} ha contestado algo que no entiendo.",
            CANCELLED: "He parado la petición.",
            NO_KEY: "No hay clave de {p} puesta.",
        }.get(self.tipo, "{p} ha fallado.").format(p=self.proveedor or "el proveedor")

    def __repr__(self) -> str:  # pragma: no cover - ayuda al registro
        return f"AIError({self.tipo}, proveedor={self.proveedor!r}, estado={self.estado})"


def desde_http(estado: int, cuerpo: str, proveedor: str = "",
               retry_after: float = 0.0) -> AIError:
    """Traduce una respuesta HTTP de proveedor a un `AIError` clasificado."""
    tipo = _ESTADO_A_TIPO.get(int(estado))
    if tipo is None:
        tipo = UNAVAILABLE if int(estado) >= 500 else MALFORMED
    if int(estado) == 408:
        tipo = TIMEOUT
    mensaje, codigo = _extraer_mensaje(cuerpo)
    # Groq responde 400 con "model_not_found" cuando el modelo no existe; es un
    # 404 encubierto y tratarlo como error de formato haria perder el cambio de
    # proveedor.
    if tipo == MALFORMED and codigo in ("model_not_found", "model_deprecated"):
        tipo = NOT_FOUND
    if tipo == MALFORMED and "rate" in str(codigo).lower():
        tipo = RATE_LIMITED
    return AIError(tipo, mensaje, proveedor, estado=int(estado),
                   retry_after=retry_after, cuerpo=cuerpo)


def desde_excepcion(exc: Exception, proveedor: str = "") -> AIError:
    """Traduce una excepción de red a un `AIError` clasificado."""
    if isinstance(exc, AIError):
        return exc
    texto = str(exc)
    baja = texto.lower()
    if isinstance(exc, TimeoutError) or "timed out" in baja or "timeout" in baja:
        return AIError(TIMEOUT, texto, proveedor)
    if "certificate" in baja or "ssl" in baja or "dns" in baja \
            or "connection" in baja or "urlopen" in baja or "network" in baja:
        return AIError(NETWORK, texto, proveedor)
    return AIError(NETWORK if not texto else MALFORMED, texto, proveedor)


def sano(mensaje) -> str:
    """Quita claves, cabeceras y URLs con credenciales de cualquier texto."""
    texto = str(mensaje or "")
    texto = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1***", texto)
    texto = re.sub(r"(?i)\b(gsk|groq|AIza|sk)-[A-Za-z0-9._\-]{8,}", r"\1-***", texto)
    texto = re.sub(r"(?i)(api[_-]?key\"?\s*[:=]\s*)\S+", r"\1***", texto)
    texto = re.sub(r"(?i)(authorization\"?\s*[:=]\s*)\S+", r"\1***", texto)
    texto = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1***@", texto)
    return _cortar(texto, 400)


def _cortar(texto, limite: int) -> str:
    texto = str(texto or "")
    return texto if len(texto) <= limite else texto[:limite - 1] + "…"


def _extraer_mensaje(cuerpo: str) -> tuple[str, str]:
    """Saca el mensaje y el código del JSON de error de un proveedor."""
    try:
        datos = json.loads(cuerpo or "{}")
    except (ValueError, TypeError):
        return sano(cuerpo), ""
    if isinstance(datos, dict):
        err = datos.get("error")
        if isinstance(err, dict):
            return sano(err.get("message") or ""), str(err.get("code") or
                                                       err.get("type") or "")
        if isinstance(err, str):
            return sano(err), ""
        if isinstance(datos.get("message"), str):
            return sano(datos["message"]), str(datos.get("code") or "")
    return sano(cuerpo), ""