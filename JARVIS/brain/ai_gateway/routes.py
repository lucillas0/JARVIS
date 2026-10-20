"""API interna de los modelos: /api/ai/*

Mismo contrato que el resto del panel: token de `brain/auth.py` y origen
permitido. La clave **no sale nunca**: se comprueba si hay, y para ponerla se
manda en el cuerpo y se escribe en `keys.env`, igual que hace /config.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import Config

from . import errors, gateway, health, registry, stats

log = logging.getLogger("brain.ai.routes")
router = APIRouter()


# --------------------------------------------------------------------------- #
# Autorización (la misma que el resto del centro de mando)
# --------------------------------------------------------------------------- #
def _authorized(request: Request) -> bool:
    """Cabecera, query o cookie de sesion (ver `auth.autenticado`)."""
    from auth import autenticado, origen_permitido

    return (origen_permitido(request.headers.get("origin"))
            and autenticado(request))


def _no_autorizado() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Sesion no autorizada. Recarga JARVIS Live."},
                        status_code=401, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# Modelos de entrada
# --------------------------------------------------------------------------- #
class PeticionChat(BaseModel):
    mensajes: list = Field(default_factory=list)
    tarea: str = Field(default="GENERAL", max_length=24)
    stream: bool = False
    segundos: float = Field(default=0, ge=0, le=300)
    temperatura: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    request_id: str = Field(default="", max_length=40)


class PeticionTest(BaseModel):
    provider: str = Field(default="groq", max_length=24)
    # Campo seguro: se escribe y no se devuelve. Si viene vacio, se prueba con
    # la clave ya configurada.
    clave: str = Field(default="", max_length=200)


class PeticionCancelar(BaseModel):
    request_id: str = Field(default="", max_length=40)


class PeticionConfig(BaseModel):
    FAST: str = Field(default="", max_length=160)
    GENERAL: str = Field(default="", max_length=160)
    REASONING: str = Field(default="", max_length=160)
    VISION: str = Field(default="", max_length=160)
    fallback: bool | None = None
    groq: bool | None = None
    gemini: bool | None = None
    openrouter: bool | None = None
    # Escritura de la clave: nunca se lee de vuelta.
    clave: str = Field(default="", max_length=200)


# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #
@router.get("/api/ai/providers")
async def ai_providers(request: Request):
    """Qué proveedores hay, con su estado real. Nunca la clave."""
    if not _authorized(request):
        return _no_autorizado()
    datos = await asyncio.to_thread(health.proveedores)
    return JSONResponse({"ok": True, "providers": datos,
                         "estado_global": health.estado_global(),
                         "ts": time.time()},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/ai/models")
async def ai_models(request: Request, consultar: int = 0):
    """Catálogo de modelos: lo declarado y, si se pide, lo que da el proveedor."""
    if not _authorized(request):
        return _no_autorizado()
    if consultar:
        await asyncio.to_thread(registry.todos, True)
    return JSONResponse({"ok": True, "models": registry.todos(),
                         "asignados": registry.configurados(),
                         "seleccion": registry.seleccion()})


@router.get("/api/ai/status")
async def ai_status(request: Request):
    """Estado completo: proveedores, modelos, uso real, límites y qué se está
    haciendo ahora mismo."""
    if not _authorized(request):
        return _no_autorizado()
    estado = await asyncio.to_thread(gateway.instancia().estado)
    estado["estado_global"] = health.estado_global()
    return JSONResponse(estado, headers={"Cache-Control": "no-store"})


@router.get("/api/ai/uso")
async def ai_uso(request: Request, minutos: int = 1440):
    """Cifras reales de uso: peticiones, tokens, latencia y errores."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse({"ok": True, "resumen": await asyncio.to_thread(
        stats.resumen, max(1, min(int(minutos), 60 * 24 * 30)))})


@router.post("/api/ai/chat")
async def ai_chat(request: Request, cuerpo: PeticionChat):
    """Una inferencia por la API, con las mismas reglas que la conversación."""
    if not _authorized(request):
        return _no_autorizado()
    tarea = (cuerpo.tarea or "GENERAL").upper()
    if tarea not in registry.TAREAS:
        return JSONResponse({"ok": False, "error": f"Tarea desconocida: {tarea}"},
                            status_code=400)
    resultado = await asyncio.to_thread(
        gateway.instancia().chat, cuerpo.mensajes, tarea,
        vision=None, segundos=cuerpo.segundos, temperature=cuerpo.temperatura,
        max_tokens=cuerpo.max_tokens, stream=False,
        request_id=cuerpo.request_id)
    # El razonamiento interno del modelo no sale: solo la respuesta final.
    resultado.pop("raw", None)
    return JSONResponse(resultado)


@router.post("/api/ai/stream")
async def ai_stream(request: Request, cuerpo: PeticionChat):
    """Devuelve la respuesta en trozos mientras se genera (Server-Sent Events).

    Cada trozo es un `data:` con JSON. Es la misma emisión que ve JARVIS Live
    por el WebSocket, servida por HTTP para quien la pida de otra manera.
    """
    if not _authorized(request):
        return _no_autorizado()
    if not Config.LLM_STREAM_ENABLED:
        return JSONResponse({"ok": False, "error": "El streaming esta apagado "
                                                  "(LLM_STREAM_ENABLED=0)."},
                            status_code=409)
    tarea = (cuerpo.tarea or "GENERAL").upper()
    if tarea not in registry.TAREAS:
        return JSONResponse({"ok": False, "error": f"Tarea desconocida: {tarea}"},
                            status_code=400)

    async def generador():
        # El hilo del proveedor mete los trozos en una cola y AVISA al bucle de
        # eventos. Antes se miraba una lista cada 30 ms: eso son 33 despertares
        # por segundo y por cliente, aunque no llegue ni un token, y cada token
        #pagaba hasta 30 ms de espera. Con la cola, el generador se duerme
        # hasta que hay algo de verdad que enviar (y un poco menos: si el
        # cliente se desconecta, el hilo no se queda empujando a la nada).
        import threading

        cola: "asyncio.Queue[dict]" = asyncio.Queue()
        bucle = asyncio.get_running_loop()
        cerrado = threading.Event()

        def bombear(indice: int):
            try:
                for evento in gateway.instancia().stream(
                        cuerpo.mensajes, tarea, segundos=cuerpo.segundos,
                        max_tokens=cuerpo.max_tokens, request_id=cuerpo.request_id):
                    if evento.get("type") == "token":
                        paquete = {"type": "token", "texto": evento.get("texto")}
                    elif evento.get("type") == "done":
                        paquete = {"type": "done", "usage": evento.get("usage"),
                                   "latency_ms": evento.get("latency_ms"),
                                   "model": evento.get("model")}
                    else:
                        paquete = {"type": "error", "error": evento.get("error"),
                                   "tipo": evento.get("tipo")}
                    bucle.call_soon_threadsafe(cola.put_nowait, paquete)
                    if paquete["type"] != "token":
                        break
            finally:
                cerrado.set()
                try:
                    bucle.call_soon_threadsafe(cola.put_nowait, {"type": "done"})
                except RuntimeError:
                    pass   # el bucle ya estaba cerrado: nada que hacer

        hilo = threading.Thread(target=bombear, args=(0,), daemon=True)
        hilo.start()
        while True:
            try:
                evento = await cola.get()
            except asyncio.CancelledError:
                # El cliente se fue. Se avisa al gateway para que cancele la
                # peticion: si no, el proveedor sigue gastando tokens de una
                # respuesta que nadie va a leer.
                try:
                    gateway.instancia().cancelar(cuerpo.request_id)
                except Exception as exc:  # noqa: BLE001
                    log.debug("cancelar stream: %s", exc)
                raise
            yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
            if evento.get("type") in ("done", "error"):
                break

    return StreamingResponse(generador(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


@router.post("/api/ai/test")
async def ai_test(request: Request, cuerpo: PeticionTest):
    """Prueba de conexión real: una llamada de 1 token. Solo Groq por HTTP."""
    if not _authorized(request):
        return _no_autorizado()
    resultado = await asyncio.to_thread(health.probar, cuerpo.provider, cuerpo.clave)
    if cuerpo.clave:
        await asyncio.to_thread(_guardar_clave, cuerpo.clave)
    return JSONResponse({"ok": bool(resultado.get("ok")), **resultado})


@router.post("/api/ai/cancel")
async def ai_cancel(request: Request, cuerpo: PeticionCancelar):
    """Corta una petición en marcha (o todas si no se dice cuál)."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(gateway.instancia().cancelar,
                                                cuerpo.request_id))


@router.post("/api/ai/config")
async def ai_config(request: Request, cuerpo: PeticionConfig):
    """Cambia qué modelo se usa para cada tarea y apaga proveedores.

    La clave se escribe a disco si viene; nunca se devuelve ni se lee de aqui.
    """
    if not _authorized(request):
        return _no_autorizado()
    if cuerpo.clave:
        await asyncio.to_thread(_guardar_clave, cuerpo.clave)
    datos = cuerpo.model_dump(exclude={"clave"})
    guardado = await asyncio.to_thread(registry.guardar_seleccion, datos)
    return JSONResponse({"ok": True, "seleccion": guardado,
                         "asignados": registry.configurados()})


# --------------------------------------------------------------------------- #
# Claves: se escriben como en /config, en keys.env
# --------------------------------------------------------------------------- #
def _guardar_clave(clave: str) -> bool:
    """Escribe GROQ_API_KEY en keys.env y la deja puesta en memoria."""
    if not clave or not clave.strip():
        return False
    import os

    from main import _keys_env_path  # el mismo sitio que usa /config

    ruta = _keys_env_path()
    clave = clave.strip()
    lineas = []
    if ruta:
        try:
            if os.path.isfile(ruta):
                with open(ruta, "r", encoding="utf-8-sig") as f:
                    lineas = f.read().splitlines()
        except OSError as exc:
            log.warning("no se pudo leer keys.env: %s", exc)
    puesta = False
    for i, linea in enumerate(lineas):
        if linea.strip().startswith("GROQ_API_KEY="):
            lineas[i] = f"GROQ_API_KEY={clave}"
            puesta = True
    if not puesta:
        lineas.append(f"GROQ_API_KEY={clave}")
    if ruta:
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("\n".join(lineas) + "\n")
        except OSError as exc:
            log.warning("no se pudo guardar la clave: %s", exc)
            return False
    Config.GROQ_API_KEY = clave          # para que surta efecto sin reiniciar
    os.environ["GROQ_API_KEY"] = clave
    log.info("clave de Groq guardada (no se muestra ni se registra).")
    return True
