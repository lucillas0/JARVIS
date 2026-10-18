"""Cliente HTTP de la API real de OpenCode.

OpenCode es cliente-servidor: el TUI, `opencode run` y nosotros hablamos con
un servidor que es el dueno de las sesiones, los permisos y las herramientas.
Por eso aqui no hay subprocess ni bucles de texto: hay llamadas HTTP a la
API documentada (/api/session, /api/event, /api/vcs/status...).

Solo urllib de la biblioteca estandar: JARVIS no anade dependencias nuevas.

Contrato usado (OpenCode V2, /openapi.json):
  POST   /api/session                                  crea sesion
  GET    /api/session/{id}                             estado real de la sesion
  POST   /api/session/{id}/prompt                      envia el prompt y ejecuta
  GET    /api/session/{id}/message                     mensajes (actividad real)
  GET    /api/session/{id}/permission                  permisos pendientes
  POST   /api/session/{id}/permission/{pid}/reply      once | always | reject
  GET    /api/session/{id}/form                        preguntas pendientes
  POST   /api/session/{id}/form/{fid}/reply            responde
  POST   /api/session/{id}/interrupt                   detiene el turno
  GET    /api/vcs/status?location[directory]=...       cambios reales de Git
  GET    /api/event                                    flujo SSE de eventos
  GET    /api/info                                     salud del servidor
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, Optional

log = logging.getLogger("brain.opencode.client")


class OpenCodeError(RuntimeError):
    """Fallo hablando con OpenCode. Conserva el codigo HTTP para poder decidir."""

    def __init__(self, message: str, status: int = 0, payload: object = None):
        super().__init__(message)
        self.status = int(status or 0)
        self.payload = payload


class OpenCodeClient:
    """Envoltura fina de urllib con la autenticación que usa el servicio local.

    El servicio de OpenCode se autentica con HTTP Basic (usuario fijo
    `opencode` y la contraseña que genera él en su fichero de registro).
    """

    def __init__(self, base_url: str, username: str = "", password: str = "",
                 timeout: float = 30.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.timeout = float(timeout or 30.0)

    # ------------------------------------------------------------------ #
    def _auth_header(self) -> dict:
        if not self.password:
            return {}
        token = base64.b64encode(
            f"{self.username or 'opencode'}:{self.password}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def _request(self, method: str, path: str, body: object = None,
                 timeout: Optional[float] = None) -> object:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json", **self._auth_header()}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                crudo = resp.read()
        except urllib.error.HTTPError as exc:  # 4xx/5xx con cuerpo JSON
            detalle = ""
            try:
                cuerpo = json.loads(exc.read().decode("utf-8", "replace") or "{}")
                if isinstance(cuerpo, dict):
                    detalle = str(cuerpo.get("message") or cuerpo.get("error") or cuerpo)
            except Exception:  # noqa: BLE001 - el cuerpo puede no ser JSON
                detalle = ""
            raise OpenCodeError(detalle or f"OpenCode respondió HTTP {exc.code}",
                                status=exc.code, payload=detalle) from exc
        except urllib.error.URLError as exc:
            raise OpenCodeError(f"No hay conexión con el servidor de OpenCode: {exc.reason}") from exc
        except OSError as exc:
            raise OpenCodeError(f"No se pudo hablar con OpenCode: {exc}") from exc
        if not crudo:
            return {}
        try:
            return json.loads(crudo.decode("utf-8", "replace"))
        except ValueError as exc:
            raise OpenCodeError("OpenCode devolvió una respuesta que no es JSON") from exc

    # ------------------------------------------------------------------ #
    def get(self, path: str, params: dict | None = None, timeout: Optional[float] = None):
        if params:
            limpio = {k: v for k, v in params.items() if v not in (None, "")}
            if limpio:
                path = f"{path}?{urllib.parse.urlencode(limpio)}"
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, body: object = None, timeout: Optional[float] = None):
        return self._request("POST", path, body if body is not None else {},
                             timeout=timeout)

    def delete(self, path: str, timeout: Optional[float] = None):
        return self._request("DELETE", path, timeout=timeout)

    # ------------------------------------------------------------------ #
    def stream_events(self, stop=None, timeout: Optional[float] = None) -> Iterator[dict]:
        """Lee /api/event (SSE) y entrega cada evento JSON que venga.

        No se ata a la forma exacta de cada evento: se entrega tal cual y quien
        lo consume decide. Asi una version nueva de OpenCode con tipos
        desconocidos no rompe el ciclo; solo deja de memberi detalle fino.
        """
        url = f"{self.base_url}/api/event"
        headers = {"Accept": "text/event-stream", **self._auth_header()}
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                for linea in resp:
                    if stop is not None and stop.is_set():
                        return
                    texto = linea.decode("utf-8", "replace").strip()
                    if not texto or not texto.startswith("data:"):
                        continue
                    cuerpo = texto[5:].strip()
                    if not cuerpo or cuerpo == "[DONE]":
                        continue
                    try:
                        evento = json.loads(cuerpo)
                    except ValueError:
                        # Una línea mal formada no debe tumbar el flujo entero,
                        # pero se anota: si los eventos llegan todos vacíos, el
                        # síntoma es "no pasa nada" y sin esto no hay por dónde.
                        log.debug("evento de OpenCode ilegible: %s", cuerpo[:120])
                        continue
                    yield evento if isinstance(evento, dict) else {"data": evento}
        except (urllib.error.URLError, OSError) as exc:
            log.debug("flujo de eventos de OpenCode cerrado: %s", exc)
        except Exception as exc:  # noqa: BLE001 - el stream nunca debe tumbar al llamador
            log.debug("flujo de eventos de OpenCode: %s", exc)

    # ------------------------------------------------------------------ #
    @staticmethod
    def query_path(path: str, **params) -> str:
        """Rutas con clave de query con corchetes, como location[directory]."""
        limpio = {k: v for k, v in params.items() if v not in (None, "")}
        if not limpio:
            return path
        return f"{path}?{urllib.parse.urlencode(limpio)}"