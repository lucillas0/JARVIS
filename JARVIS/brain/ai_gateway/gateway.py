"""El AI Gateway: todo lo que JARVIS le pide a un modelo pasa por aquí.

    Petición (main.py, /api/ai/chat, la voz, el planificador)
        ↓
    registro.elegir(tarea)          → qué modelo
        ↓
    cadena de proveedores           → Groq, y si falla, Gemini, OpenRouter, Ollama
        ↓
    reintentos con espera creciente  → solo 429, 5xx y red
        ↓
    eventos llm.* por el bus        → JARVIS Live lo ve en directo
        ↓
    stats.registrar()               → tokens, latencia, reintentos, error

Decisiones que no se negocian aquí:

- **El Permission Manager no se toca.** El gateway no ejecuta herramientas: solo
  devuelve `tool_calls` y quien lo pidió (main.py) los pasa por
  `brain.tools.execute()`, que es el Permission Manager de siempre.
- **No hay bus paralelo.** Los eventos salen por `Brain.emit`, el de siempre.
- **No hay coste inventado.** Si el proveedor no lo da, se escribe `unknown`.
- **Una clave no válida no se reintenta**: se dice y se cambia de proveedor.
"""
from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from typing import Callable, Iterator, Optional

from config import Config

from . import errors, registry, stats

log = logging.getLogger("brain.ai.gateway")

_instances: dict = {}
_lock = threading.RLock()

# Peticiones vivas, para poder cancelar y para no pasar de cierto numero a la vez.
_vivas: dict = {}
_vivas_lock = threading.RLock()

# Callback opcional que sustituye a Brain.emit cuando el gateway se usa fuera del
# proceso del servidor (pruebas, scripts). El bus de verdad se conecta con
# `conectar_bus`.
_bus: Optional[Callable[[dict], None]] = None


def conectar_bus(emitir: Callable[[dict], None]) -> None:
    """Engancha el gateway al bus que ya existe (`Brain.emit`)."""
    global _bus
    _bus = emitir


def _emitir(evento: dict) -> None:
    """Publica en el bus de JARVIS. Si no hay bus, al log y a la nada.

    Nunca viaja una clave: los eventos llevan proveedor, modelo, tarea,
    latencia y el tipo de error, nada más.
    """
    if _bus is None:
        log.debug("evento %s sin bus conectado", evento.get("type"))
        return
    try:
        _bus(evento)
    except Exception as exc:  # noqa: BLE001 - el bus no puede romper la inferencia
        log.debug("no se pudo publicar %s: %s", evento.get("type"), exc)


class AIGateway:
    """Una instancia por proceso. Sin estado de conversación: solo inferencia."""

    def __init__(self, router=None):
        # El router viejo (Gemini/OpenRouter/Ollama y visión) se reutiliza tal
        # cual: el gateway no reescribe lo que ya funcionaba.
        self.router = router

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    def proveedores(self) -> list[dict]:
        """Qué proveedores hay, con su estado real y su clave puesta (o no)."""
        desde_stats = self._desde_stats()
        salida = []
        nombres = ("groq", "gemini", "openrouter", "ollama")
        for nombre in nombres:
            info = {
                "id": nombre,
                "habilitado": registry.habilitados().get(nombre, True),
                "modelos": [m for m in registry.configurados() if m["provider"] == nombre],
            }
            info.update(desde_stats.get(nombre, {}))
            salida.append(info)
        return salida

    def _desde_stats(self) -> dict:
        """Estado por proveedor deducido de lo que ha pasado de verdad."""
        filas = stats.listar(60 * 24 * 7, 200)
        salida: dict = {}
        for nombre in ("groq", "gemini", "openrouter", "ollama"):
            propias = [f for f in filas if f.get("provider") == nombre]
            if not propias:
                salida[nombre] = {"estado": "SIN DATOS", "peticiones": 0,
                                  "ultimo": None, "ultimo_error": None}
                continue
            ultima = propias[-1]
            ultimo_error = next((f for f in reversed(propias) if not f["ok"]), None)
            if ultimo_error and not ultima["ok"]:
                estado = _estado_de_error(ultimo_error.get("error_kind") or "")
            elif ultima["ok"]:
                estado = "CONNECTED"
            else:
                estado = _estado_de_error(ultimo_error.get("error_kind") or "") \
                    if ultimo_error else "ERROR"
            salida[nombre] = {
                "estado": estado,
                "peticiones": len(propias),
                "fallos": sum(1 for f in propias if not f["ok"]),
                "ultimo": {"at": ultima["at"], "model": ultima.get("model"),
                           "latency_ms": ultima.get("latency_ms")},
                "ultimo_error": ({"at": ultimo_error["at"],
                                  "tipo": ultimo_error.get("error_kind")}
                                 if ultimo_error else None),
            }
        return salida

    def estado(self) -> dict:
        """Lo que ve JARVIS Live: proveedores, modelos, uso real y selección."""
        return {
            "ok": True,
            "ts": time.time(),
            "proveedores": self.proveedores(),
            "modelos": registry.todos(),
            "asignados": registry.configurados(),
            "seleccion": registry.seleccion(),
            "fallback": registry.fallback_activo(),
            "uso": stats.resumen(),
            "limites": {
                "timeout": Config.LLM_TIMEOUT_SECONDS,
                "max_retries": Config.LLM_MAX_RETRIES,
                "max_concurrent": Config.LLM_MAX_CONCURRENT,
                # El que se aplica ahora mismo: el de arriba es el que puso el
                # señor, este puede estar más bajo si aprieta la memoria.
                "max_concurrent_efectivo": limite_concurrencia(),
                "stream": bool(Config.LLM_STREAM_ENABLED),
                "tools": bool(Config.LLM_TOOLS_ENABLED),
                "groq_base_url": (Config.GROQ_BASE_URL or "").rstrip("/"),
            },
            "en_vuelo": en_vuelo(),
        }

    # ------------------------------------------------------------------ #
    # Inferencia
    # ------------------------------------------------------------------ #
    def chat(self, mensajes: list[dict], tarea: str = registry.GENERAL, *,
             vision: Optional[str] = None, segundos: float = 0.0,
             temperature: Optional[float] = None, max_tokens: Optional[int] = None,
             stream: bool = False, tools: Optional[list] = None,
             proveedor_preferido: str = "",
             request_id: str = "") -> dict:
        """Pide una respuesta. Devuelve el mismo diccionario que el router viejo.

        Claves: reply, provider, model, fallback, latency_ms, usage, tokens,
        error_kind, retries, tool_calls, streamed, agotado, fallos, red.
        """
        if not isinstance(mensajes, list) or not mensajes:
            return {"ok": False, "reply": "", "provider": "none", "fallback": True,
                    "error": "No hay contexto para el modelo.", "error_kind": "malformed"}
        request_id = request_id or uuid.uuid4().hex[:12]
        eleccion = registry.elegir(tarea)
        if eleccion["provider"] == "opencode":
            # La programación no pasa por Groq: la lleva OpenCode. El gateway lo
            # dice claro en vez de inventarse una respuesta.
            return {"ok": False, "reply": "", "provider": "opencode",
                    "fallback": False, "model": "opencode",
                    "error": "La programacion la lleva OpenCode.",
                    "error_kind": "delegado", "request_id": request_id}

        inicio = time.time()
        limite = float(segundos or 0)
        global _T0
        _T0 = [inicio, limite]        # el reloj que comparten espera y proveedores
        _emitir({"type": "llm.requested", "provider": eleccion["provider"],
                 "model": eleccion["id"], "task": tarea, "stream": bool(stream),
                 "request_id": request_id})
        if limite:
            _plazo(limite)
        else:
            _plazo(Config.LLM_TIMEOUT_SECONDS)

        herramientas = self._herramientas(tools, eleccion)
        intentos = max(0, int(Config.LLM_MAX_RETRIES))
        reintentos = 0
        fallos: list = []
        cadena = self._cadena(eleccion, proveedor_preferido, vision)
        if not cadena:
            respuesta = {"ok": False, "reply": "", "provider": "none",
                         "fallback": True, "sin_claves": True,
                         "error": "No hay ninguna clave de modelo puesta.",
                         "error_kind": errors.NO_KEY, "request_id": request_id}
            stats.registrar(provider="none", model="", tarea=tarea, ok=False,
                            latency_ms=(time.time() - inicio) * 1000,
                            error_kind=errors.NO_KEY, request_id=request_id)
            _emitir({"type": "llm.failed", "provider": "none", "task": tarea,
                     "error_kind": errors.NO_KEY, "request_id": request_id})
            return respuesta

        _emitir({"type": "llm.started", "provider": cadena[0][0],
                 "model": cadena[0][1], "task": tarea, "request_id": request_id})

        for indice, (proveedor, modelo) in enumerate(cadena):
            for intento in range(intentos + 1):
                if _vencido(limite):
                    break
                try:
                    if proveedor == "groq":
                        from .providers import groq as _g

                        if stream:
                            return self._enstreaming(
                                _g, mensajes, modelo, tarea, inicio, limite,
                                reintentos, herramientas, vision, temperature,
                                max_tokens, request_id, fallos, cadena[0][0])
                        r = _g.completar(mensajes, modelo,
                                         temperature=temperature if temperature is not None
                                         else Config.GROQ_TEMPERATURE,
                                         max_tokens=max_tokens, vision=vision,
                                         tools=herramientas, timeout=_restante(limite))
                        return self._cierre_ok(r, proveedor, modelo, tarea, inicio,
                                               reintentos, request_id, fallos)
                    # Gemini / OpenRouter / Ollama: el router de siempre.
                    return self._via_router(proveedor, mensajes, tarea, inicio,
                                            limite, reintentos, request_id, vision)
                except errors.AIError as exc:
                    fallos.append({"provider": proveedor, "tipo": exc.tipo,
                                   "error": errors.sano(exc)})
                    log.warning("%s fallo (%s): %s", proveedor, exc.tipo,
                                errors.sano(exc))
                    _emitir({"type": "llm.failed", "provider": proveedor,
                             "model": modelo, "task": tarea, "error_kind": exc.tipo,
                             "codigo": exc.estado, "intento": intento,
                             "request_id": request_id})
                    if exc.tipo == errors.RATE_LIMITED:
                        _emitir({"type": "llm.rate_limited", "provider": proveedor,
                                 "model": modelo, "task": tarea,
                                 "retry_after": exc.retry_after,
                                 "request_id": request_id})
                    if not exc.reintentable:
                        break
                    if intento < intentos and not _vencido(limite):
                        espera = self._espera(exc, intento)
                        reintentos += 1
                        _emitir({"type": "llm.retry", "provider": proveedor,
                                 "model": modelo, "task": tarea, "intento": intento + 1,
                                 "espera_s": round(espera, 2),
                                 "error_kind": exc.tipo, "request_id": request_id})
                        _dormir(espera)
                        continue
                    break
            if indice < len(cadena) - 1:
                siguiente = cadena[indice + 1]
                _emitir({"type": "llm.fallback", "desde": proveedor,
                         "hacia": siguiente[0], "model": siguiente[1],
                         "task": tarea, "motivo": fallos[-1]["tipo"] if fallos else "",
                         "request_id": request_id})
                log.info("cambio de proveedor: %s -> %s", proveedor, siguiente[0])
            if _vencido(limite):
                break

        # Todo ha fallado: se devuelve un error controlado y explicable.
        detalle = "; ".join(f"{f['provider']}: {f['tipo']}" for f in fallos)
        stats.registrar(provider=fallos[-1]["provider"] if fallos else "none",
                        model="", tarea=tarea, ok=False,
                        latency_ms=(time.time() - inicio) * 1000,
                        retries=reintentos,
                        error_kind=fallos[-1]["tipo"] if fallos else "desconocido",
                        request_id=request_id)
        return {"ok": False, "reply": "", "provider": "none", "fallback": True,
                "agotado": _vencido(limite), "fallos": fallos,
                "retries": reintentos, "request_id": request_id,
                "latency_ms": round((time.time() - inicio) * 1000, 1),
                "error": "No he podido hablar con ningún modelo" +
                         (f" ({detalle})." if detalle else "."),
                "error_kind": fallos[-1]["tipo"] if fallos else "unknown"}

    # -------------------------- streaming -------------------------- #
    def _enstreaming(self, groq_mod, mensajes, modelo, tarea, inicio, limite,
                     reintentos, herramientas, vision, temperature,
                     max_tokens, request_id, fallos, proveedor_primero) -> dict:
        """Pide en streaming y devuelve el texto junto con la traza de eventos."""
        evento_cancelacion = threading.Event()
        with _vivas_lock:
            _vivas[request_id] = evento_cancelacion
        partes: list = []
        try:
            for trozo in groq_mod.transmitir(
                    mensajes, modelo,
                    temperature=temperature if temperature is not None
                    else Config.GROQ_TEMPERATURE,
                    max_tokens=max_tokens, vision=vision, tools=herramientas,
                    timeout=_restante(limite), cancelar=evento_cancelacion):
                partes.append(trozo)
                # Al bus: el texto que acaba de llegar, sin acumularlo entero.
                _emitir({"type": "llm.token", "provider": "groq", "model": modelo,
                         "task": tarea, "texto": trozo, "request_id": request_id})
        except errors.AIError as exc:
            texto = "".join(partes)
            if texto:
                # Respuesta parcial: mejor esto que un silencio.
                _emitir({"type": "llm.failed", "provider": "groq", "model": modelo,
                         "task": tarea, "error_kind": exc.tipo,
                         "parcial": True, "request_id": request_id})
                stats.registrar(provider="groq", model=modelo, tarea=tarea, ok=True,
                                latency_ms=(time.time() - inicio) * 1000,
                                retries=reintentos, error_kind="", streaming=True,
                                request_id=request_id)
                return {"ok": True, "reply": texto, "provider": "groq", "model": modelo,
                        "fallback": False, "streamed": True, "parcial": True,
                        "latency_ms": round((time.time() - inicio) * 1000, 1),
                        "usage": groq_mod.ultimo_stream().get("usage") or {},
                        "request_id": request_id}
            raise
        finally:
            with _vivas_lock:
                _vivas.pop(request_id, None)
        resumen = groq_mod.ultimo_stream()
        cancelado = bool(resumen.get("cancelado")) or evento_cancelacion.is_set()
        texto = "".join(partes)
        uso = resumen.get("usage") or {}
        latencia = (time.time() - inicio) * 1000
        stats.registrar(provider="groq", model=modelo, tarea=tarea, ok=not cancelado,
                        input_tokens=uso.get("input_tokens"),
                        output_tokens=uso.get("output_tokens"),
                        total_tokens=uso.get("total_tokens"),
                        latency_ms=latencia, retries=reintentos,
                        error_kind=errors.CANCELLED if cancelado else "",
                        streaming=True, cost=uso.get("cost") or "unknown",
                        request_id=request_id)
        _emitir({"type": "llm.cancelled" if cancelado else "llm.completed",
                 "provider": "groq", "model": modelo, "task": tarea,
                 "latency_ms": round(latencia, 1), "streamed": True,
                 "request_id": request_id})
        return {"ok": not cancelado or bool(texto), "reply": texto, "provider": "groq",
                "model": modelo, "fallback": False, "streamed": True,
                "cancelado": cancelado, "usage": uso, "retries": reintentos,
                "latency_ms": round(latencia, 1), "request_id": request_id,
                "tokens": uso.get("total_tokens")}

    # -------------------------- streaming público -------------------------- #
    def stream(self, mensajes: list[dict], tarea: str = registry.GENERAL, *,
               vision: Optional[str] = None, segundos: float = 0.0,
               max_tokens: Optional[int] = None, temperature: Optional[float] = None,
               request_id: str = "") -> Iterator[dict]:
        """Generador de trozos para quien los pida por HTTP.

        Devuelve dictos: {"type": "token", "texto": ...} por cada trozo y
        {"type": "done", ...} al terminar. Los mismos trozos salen por el bus,
        que es lo que pinta JARVIS Live.
        """
        request_id = request_id or uuid.uuid4().hex[:12]
        eleccion = registry.elegir(tarea)
        # Mismo límite de concurrencia que en chat(): si hay demasiadas
        # peticiones vivas y esta no es urgente, espera un hueco. Se comprueba
        # después de resolver el proveedor, que aquí sí es Groq.
        if eleccion["provider"] == "groq":
            _espera_hueco(tarea)
        inicio = time.time()
        global _T0
        _T0 = [inicio, float(segundos or 0)]
        if eleccion["provider"] != "groq":
            yield {"type": "error",
                   "error": "Esa tarea no la lleva Groq.",
                   "provider": eleccion["provider"]}
            return
        from .providers import groq as _groq

        if not _groq.tiene_clave():
            yield {"type": "error", "error": "No hay clave de Groq puesta.",
                   "provider": "groq"}
            return
        evento_cancelacion = threading.Event()
        with _vivas_lock:
            _vivas[request_id] = evento_cancelacion
        _emitir({"type": "llm.requested", "provider": "groq", "model": eleccion["id"],
                 "task": tarea, "stream": True, "request_id": request_id})
        _emitir({"type": "llm.started", "provider": "groq", "model": eleccion["id"],
                 "task": tarea, "stream": True, "request_id": request_id})
        partes: list = []
        try:
            for trozo in _groq.transmitir(
                    mensajes, str(eleccion["id"]).split("/", 1)[-1],
                    temperature=temperature if temperature is not None
                    else Config.GROQ_TEMPERATURE,
                    max_tokens=max_tokens, vision=vision,
                    timeout=_restante(segundos), cancelar=evento_cancelacion):
                partes.append(trozo)
                _emitir({"type": "llm.token", "provider": "groq",
                         "model": eleccion["id"], "task": tarea, "texto": trozo,
                         "request_id": request_id})
                yield {"type": "token", "texto": trozo}
        except errors.AIError as exc:
            yield {"type": "error", "error": exc.para_human(), "tipo": exc.tipo,
                   "provider": "groq", "parcial": "".join(partes)}
            stats.registrar(provider="groq", model=str(eleccion["id"]),
                            tarea=tarea, ok=bool(partes),
                            latency_ms=(time.time() - inicio) * 1000,
                            error_kind=exc.tipo, streaming=True,
                            request_id=request_id)
            _emitir({"type": "llm.failed", "provider": "groq", "task": tarea,
                     "error_kind": exc.tipo, "request_id": request_id})
            return
        finally:
            with _vivas_lock:
                _vivas.pop(request_id, None)
        resumen = _groq.ultimo_stream()
        uso = resumen.get("usage") or {}
        latencia = (time.time() - inicio) * 1000
        stats.registrar(provider="groq", model=str(eleccion["id"]), tarea=tarea,
                        ok=True, input_tokens=uso.get("input_tokens"),
                        output_tokens=uso.get("output_tokens"),
                        total_tokens=uso.get("total_tokens"),
                        latency_ms=latencia, streaming=True,
                        cost=uso.get("cost") or "unknown", request_id=request_id)
        _emitir({"type": "llm.completed", "provider": "groq", "model": eleccion["id"],
                 "task": tarea, "latency_ms": round(latencia, 1), "streamed": True,
                 "request_id": request_id})
        yield {"type": "done", "texto": "".join(partes), "usage": uso,
               "latency_ms": round(latencia, 1), "model": str(eleccion["id"])}

    # -------------------------- otros proveedores -------------------------- #
    def _via_router(self, proveedor, mensajes, tarea, inicio, limite,
                    reintentos, request_id, vision) -> dict:
        """Gemini, OpenRouter u Ollama: se usan SUS métodos, no su `respond`.

        Importa no llamar a `ModelRouter.respond()`: ese método va a delegar en
        este gateway, y llamarse a sí mismo sería un bucle infinito. Aquí se
        habla directamente con el proveedor, que es lo que hacia antes.
        """
        if self.router is None:
            raise errors.AIError(errors.UNAVAILABLE,
                                 "El router de proveedores no está disponible.",
                                 proveedor)
        _plazo(_restante(limite))
        metodo = {"gemini": "_chat_gemini", "openrouter": "_chat_openrouter",
                  "ollama": "_chat_ollama"}.get(proveedor)
        funcion = getattr(self.router, metodo, None) if metodo else None
        if funcion is None:
            raise errors.AIError(errors.UNAVAILABLE,
                                 f"{proveedor} no está disponible.", proveedor)
        texto = funcion(mensajes, vision) if metodo != "_chat_ollama" else funcion(mensajes)
        texto = str(texto or "")
        latencia = (time.time() - inicio) * 1000
        nombre = proveedor
        ok = bool(texto) and nombre not in ("none", "timeout")
        stats.registrar(provider=nombre, model="", tarea=tarea, ok=ok,
                        latency_ms=latencia, retries=reintentos,
                        request_id=request_id)
        if not ok:
            raise errors.AIError(errors.TIMEOUT if nombre == "timeout"
                                 else errors.UNAVAILABLE,
                                 f"{nombre} no devolvio texto.", nombre)
        _emitir({"type": "llm.completed", "provider": nombre, "task": tarea,
                 "latency_ms": round(latencia, 1), "request_id": request_id})
        # El proveedor es distinto del que se pidio: eso ya es un fallback.
        return {"ok": True, "reply": texto, "provider": nombre, "model": "",
                "fallback": nombre != proveedor or reintentos > 0,
                "latency_ms": round(latencia, 1), "request_id": request_id,
                "usage": {}, "tokens": None}

    # ------------------------------------------------------------------ #
    # Utilidades internas
    # ------------------------------------------------------------------ #
    def _cadena(self, eleccion: dict, preferido: str,
                vision: Optional[str]) -> list[tuple[str, str]]:
        """Proveedores a probar, en orden. Groq primero si es lo elegido."""
        hab = registry.habilitados()
        modelo = str(eleccion["id"]).split("/", 1)[-1]
        if vision:
            modelo = Config.GROQ_MODEL_VISION or modelo
        cadena: list[tuple[str, str]] = []
        groq = ("groq", modelo)
        if preferido == "groq" or not preferido:
            if hab.get("groq"):
                cadena.append(groq)
        if vision:
            # La imagen solo la saben mandar los proveedores compatibles; no se
            # manda a Gemini ni a OpenRouter por el camino corto del router.
            return cadena
        if not registry.fallback_activo():
            return cadena
        if preferido == "gemini" and hab.get("gemini"):
            cadena.insert(0, ("gemini", Config.GEMINI_MODEL_HEAVY))
        if hab.get("openrouter") and not any(c[0] == "openrouter" for c in cadena):
            cadena.append(("openrouter", Config.OPENROUTER_MODEL))
        # Ollama es el ultimo recurso: sin internet no hay quien conteste.
        if self.router is not None and self.router.ollama_cached():
            cadena.append(("ollama", Config.FALLBACK_MODEL))
        return cadena

    def _herramientas(self, tools: Optional[list], eleccion: dict) -> Optional[list]:
        """Herramientas solo si el modelo sabe usarlas y están permitidas.

        Una capacidad desconocida se trata como "no": preguntar tool calling a
        un modelo que no lo soporta devuelve 400 y quema el turno.
        """
        if not tools or not Config.LLM_TOOLS_ENABLED:
            return None
        meta = registry.buscar(str(eleccion["id"]).split("/", 1)[-1])
        if not meta or meta.get("supports_tools") is not True:
            log.info("modelo %s: herramientas no ofrecidas (capacidad %s)",
                     eleccion.get("id"), (meta or {}).get("supports_tools"))
            return None
        return tools

    @staticmethod
    def _espera(exc: errors.AIError, intento: int) -> float:
        """Espera antes de reintentar: la que dice el proveedor si la da."""
        base = 0.6 * (2 ** max(0, intento))
        if exc.retry_after:
            return min(8.0, max(exc.retry_after, base))
        if exc.tipo == errors.RATE_LIMITED:
            base = max(base, 1.5)
        return min(8.0, base + random.uniform(0, 0.25))

    def _cierre_ok(self, r: dict, proveedor: str, modelo: str, tarea: str,
                   inicio: float, reintentos: int, request_id: str,
                   fallos: list) -> dict:
        """Respuesta buena: se guarda, se avisa al bus y se devuelve."""
        uso = r.get("usage") or {}
        latencia = (time.time() - inicio) * 1000
        texto = str(r.get("texto") or "")
        llamadas = r.get("tool_calls") or []
        stats.registrar(provider=proveedor, model=r.get("modelo") or modelo,
                        tarea=tarea, ok=True,
                        input_tokens=uso.get("input_tokens"),
                        output_tokens=uso.get("output_tokens"),
                        total_tokens=uso.get("total_tokens"),
                        latency_ms=latencia, retries=reintentos,
                        cost=uso.get("cost") or "unknown", request_id=request_id)
        _emitir({"type": "llm.completed", "provider": proveedor,
                 "model": r.get("modelo") or modelo, "task": tarea,
                 "latency_ms": round(latencia, 1), "tokens": uso.get("total_tokens"),
                 "fallback": bool(fallos), "request_id": request_id})
        return {"ok": True, "reply": texto, "provider": proveedor,
                "model": r.get("modelo") or modelo,
                "fallback": bool(fallos), "latency_ms": round(latencia, 1),
                "usage": uso, "tokens": uso.get("total_tokens"), "retries": reintentos,
                "tool_calls": llamadas, "request_id": request_id}

    # ------------------------------------------------------------------ #
    # Cancelación
    # ------------------------------------------------------------------ #
    def cancelar(self, request_id: str = "") -> dict:
        """Corta una petición en marcha (o todas si no se dice cuál)."""
        with _vivas_lock:
            objetivo = [request_id] if request_id else list(_vivas.keys())
            for clave in objetivo:
                evento = _vivas.get(clave)
                if evento is not None:
                    evento.set()
        if not objetivo:
            return {"ok": False, "error": "No hay ninguna peticion en marcha."}
        _emitir({"type": "llm.cancelled", "request_ids": objetivo})
        return {"ok": True, "canceladas": objetivo}


# --------------------------------------------------------------------------- #
# Instancia única y utilidades de plazo
# --------------------------------------------------------------------------- #
def instancia(router=None) -> AIGateway:
    with _lock:
        if "gateway" not in _instances:
            _instances["gateway"] = AIGateway(router=router)
        elif router is not None:
            _instances["gateway"].router = router
        return _instances["gateway"]


def en_vuelo() -> int:
    with _vivas_lock:
        return len(_vivas)


# --------------------------------------------------------------------------- #
# Límite de concurrencia (FASE 3).
# El estático es `Config.LLM_MAX_CONCURRENT`, que es el que puso el señor. El
# Resource Manager puede BAJARLO en caliente cuando aprieta la memoria, pero
# JAMÁS subirlo: por eso el mínimo con el que se sale es el estático.
#
# No se cambia `Config` para hacer esto. Config es lo que el señor configuró y
# lo que se lee al arrancar; tocarlo en caliente mezclaría lo decidido por una
# persona con lo decidido por una máquina, y al reiniciar se perdería el
# criterio del sistema.
# --------------------------------------------------------------------------- #
# Tareas que pueden esperar un poco si ya hay demasiadas en marcha. Una
# respuesta urgente (revisar un plan, contestar una pregunta) no espera.
URGENTES = ("REASONING", "VISION", "CODING")
# Cuánto se espera como máximo antes de continuar igualmente. Existe para que
# el sistema no se quede parado: esperando sin límite es peor que no esperar.
ESPERA_MAXIMA_S = 5.0


def limite_concurrencia() -> int:
    """El tope de peticiones a modelos a la vez, ahora mismo."""
    estatico = max(1, int(getattr(Config, "LLM_MAX_CONCURRENT", 4)))
    try:
        import resource_manager

        return max(1, min(estatico, int(resource_manager.limite_concurrencia())))
    except Exception:  # noqa: BLE001
        return estatico


def _espera_hueco(tarea: str) -> dict:
    """Si ya hay demasiadas peticiones en marcha, espera un poco.

    Solo espera si la tarea NO es urgente, y nunca más de ESPERA_MAXIMA_S. Con
    lo que se llega al final, se continúa igualmente y se avisa por el bus:
    quedarse quieto sin avisar sería peor que ir un poco más lento.
    """
    limite = limite_concurrencia()
    for _ in range(int(ESPERA_MAXIMA_S * 4)):
        if en_vuelo() < limite:
            return {"esperado": False, "motivo": "hay hueco"}
        if tarea in URGENTES:
            _emitir({"type": "llm.throttled", "task": tarea, "en_vuelo": en_vuelo(),
                     "limite": limite, "esperado": False,
                     "motivo": "tarea urgente: no espera"})
            return {"esperado": False, "motivo": "urgente, no espera"}
        _emitir({"type": "llm.deferred", "task": tarea, "en_vuelo": en_vuelo(),
                 "limite": limite, "esperado": True,
                 "motivo": "hay peticiones en marcha: espera un hueco"})
        time.sleep(0.25)
    return {"esperado": True, "motivo": "se agotó la espera: continúa igualmente"}


def _plazo(segundos: float) -> None:
    import model_router as _mr

    _mr._DEADLINE = time.time() + max(2.0, float(segundos))


_T0 = [0.0, 0.0]   # [inicio, limite] de la peticion en curso


def _restante(limite: float) -> float:
    """Cuánto queda del reloj de la petición (mínimo 2 s)."""
    if not limite:
        return float(Config.LLM_TIMEOUT_SECONDS)
    return max(2.0, limite - (time.time() - _T0[0]))


def _vencido(limite: float) -> bool:
    if not limite:
        return False
    return (time.time() - _T0[0]) > limite


def _dormir(segundos: float) -> None:
    """Espera a trozos para poder parar antes si se agota el reloj."""
    fin = time.time() + max(0.0, segundos)
    while time.time() < fin:
        if _vencido(_T0[1]):
            return
        time.sleep(min(0.1, max(0.0, fin - time.time())))


def _estado_de_error(tipo: str) -> str:
    return {errors.AUTH_ERROR: "AUTH_ERROR", errors.FORBIDDEN: "AUTH_ERROR",
            errors.RATE_LIMITED: "RATE_LIMITED", errors.TIMEOUT: "TIMEOUT",
            errors.NETWORK: "DISCONNECTED", errors.NO_KEY: "SIN CLAVE",
            errors.UNAVAILABLE: "ERROR", errors.NOT_FOUND: "ERROR",
            errors.MALFORMED: "ERROR"}.get(tipo, "ERROR")


def conectar_router(router) -> None:
    """Al arrancar el cerebro: el gateway reutiliza su router."""
    instancia(router=router)
    log.info("AI Gateway conectado al Model Router")


def iniciar(router=None) -> AIGateway:
    """Arranque del gateway: engancha el bus y el router existentes."""
    try:
        from main import brain

        conectar_bus(brain.emit)
    except Exception as exc:  # noqa: BLE001
        log.debug("sin bus todavia: %s", exc)
    conectar_router(router)
    return instancia()


def probar(proveedor: str = "groq", clave: str = "") -> dict:
    """Prueba de conexion real, solo cuando se pide desde /api/ai/test."""
    if proveedor != "groq":
        return {"ok": False, "provider": proveedor,
                "estado": "NO SOPORTADO",
                "error": "Solo Groq tiene prueba directa aqui."}
    from .providers import groq as _g

    resultado = _g.comprobar(clave_override=clave)
    registry.olvidar_descubrimiento()
    if resultado.get("ok"):
        try:
            _g.listar_modelos()
        except Exception:  # noqa: BLE001
            log.debug("la lista de modelos no se pudo leer tras la prueba")
    return resultado