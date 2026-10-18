"""Doble local de la API de Groq.

Habla el mismo protocolo que `https://api.groq.com/openai/v1`:
`GET /models` y `POST /chat/completions`, con streaming por Server-Sent Events.
No es un mock de `urllib`: es un servidor HTTP de verdad, y el código bajo
prueba (el proveedor Groq del gateway) abre conexiones contra el.

El comportamiento se pide por nombre de modelo, para poder provocar cada fallo
sin tocar el código:

    error-401      -> 401 con el cuerpo de error real de Groq
    error-403, error-404, error-429, error-500, error-malformed
    lento-*        -> tarda (para probar el timeout y la cancelación)
    sin-usage      -> responde sin bloque `usage`

Además guarda lo que le han pedido, para poder comprobar en las pruebas que el
contexto, las herramientas y la clave viajan como deben.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAVE_QUE_SI_FUNcionA = "gsk_clave_de_prueba_no_valida"


class Estado:
    """Lo que el doble recuerda, para que las pruebas lo comprueben."""

    def __init__(self):
        self.peticiones: list = []
        self.veces = 0
        self.lock = threading.Lock()

    def anota(self, cuerpo: dict, cabeceras: dict) -> None:
        with self.lock:
            self.veces += 1
            self.peticiones.append({"cuerpo": cuerpo,
                                    "cabeceras": {k.lower(): v for k, v in cabeceras.items()}})

    def ultima(self) -> dict:
        with self.lock:
            return self.peticiones[-1] if self.peticiones else {}

    def limpiar(self) -> None:
        with self.lock:
            self.peticiones.clear()


def _error(codigo: str, mensaje: str) -> dict:
    return {"error": {"message": mensaje, "type": codigo, "code": codigo}}


class Manejador(BaseHTTPRequestHandler):
    estado: Estado = Estado()

    def log_message(self, *_a):
        pass

    # ------------------------------------------------------------------ #
    def do_GET(self):
        if not self.path.startswith("/models"):
            self._json(404, {"error": {"message": "ruta desconocida"}})
            return
        if self._clave() != CLAVE_QUE_SI_FUNcionA:
            self._json(401, _error("invalid_api_key", "Invalid API Key"))
            return
        self._json(200, {"object": "list", "data": [
            {"id": "openai/gpt-oss-120b", "context_window": 131072, "owned_by": "openai"},
            {"id": "llama-3.3-70b-versatile", "context_window": 128000, "owned_by": "meta"},
        ]})

    # ------------------------------------------------------------------ #
    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        crudo = self.rfile.read(largo)
        try:
            cuerpo = json.loads(crudo.decode("utf-8") or "{}")
        except ValueError:
            self._json(400, _error("invalid_request_error", "cuerpo no JSON"))
            return
        self.estado.anota(cuerpo, dict(self.headers))
        modelo = str(cuerpo.get("model") or "")

        if self._clave() != CLAVE_QUE_SI_FUNcionA:
            self._json(401, _error("invalid_api_key",
                                   "Invalid API Key: gsk_*** no es valida"))
            return
        if modelo.startswith("error-401"):
            self._json(401, _error("invalid_api_key", "Invalid API Key"))
        elif modelo.startswith("error-403"):
            self._json(403, _error("permission_denied", "no tienes acceso"))
        elif modelo.startswith("error-404"):
            self._json(404, _error("model_not_found", f"model {modelo} not found"))
        elif modelo.startswith("error-429"):
            self._send(429, {"Retry-After": "1"})
        elif modelo.startswith("error-500"):
            self._json(500, _error("server_error", "se ha roto por dentro"))
        elif modelo.startswith("error-malformed"):
            self._send(200, b"esto no es json", "text/plain")
        elif modelo.startswith("lento"):
            time.sleep(float(modelo.split("-", 1)[1] or "3"))
            self._json(200, self._respuesta(cuerpo, modelo))
        elif cuerpo.get("stream"):
            self._streaming(cuerpo, modelo)
        else:
            self._json(200, self._respuesta(cuerpo, modelo))

    # ------------------------------------------------------------------ #
    def _clave(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _respuesta(self, cuerpo: dict, modelo: str) -> dict:
        uso = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        texto = "Buenos días, señor. Todo en marcha."
        respuesta = {"id": "chatcmpr-doble", "object": "chat.completion",
                     "created": int(time.time()), "model": modelo,
                     "choices": [{"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": texto}}]}
        if not str(modelo).startswith("sin-usage"):
            respuesta["usage"] = uso
        return respuesta

    def _streaming(self, cuerpo: dict, modelo: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        partes = ["Buenos ", "días, ", "señor. ", "Todo en marcha."]
        for trozo in partes:
            evento = {"id": "chatcmpr-doble", "object": "chat.completion.chunk",
                      "model": modelo,
                      "choices": [{"index": 0, "delta": {"content": trozo},
                                   "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(evento)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)
        fin = {"id": "chatcmpr-doble", "object": "chat.completion.chunk",
               "model": modelo, "choices": [{"index": 0, "delta": {},
                                             "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                         "total_tokens": 18}}
        self.wfile.write(f"data: {json.dumps(fin)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _json(self, codigo: int, datos: dict):
        self._send(codigo, json.dumps(datos).encode(), "application/json")

    def _send(self, codigo: int, cuerpo: bytes, tipo: str = "application/json"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        if codigo == 429:
            self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(cuerpo)


def arrancar(puerto: int = 0):
    """Levanta el doble y devuelve (servidor, hilo, estado)."""
    global Manejador
    Manejador.estado = Estado()
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, hilo, Manejador.estado


def url(servidor) -> str:
    return f"http://127.0.0.1:{servidor.server_address[1]}/openai/v1"


def parar(servidor) -> None:
    try:
        servidor.shutdown()
        servidor.server_close()
    except Exception:  # noqa: BLE001
        pass