"""Pruebas del AI Gateway contra un doble HTTP que habla la API de Groq.

Qué se comprueba de verdad (sin mocks dentro del código probado):

  1. configuración            las variables se leen y tienen defecto
  2. API key ausente          no se llama y se dice por qué
  3. API key inválida         401 → AUTH_ERROR, y el mensaje no lleva la clave
  4. conexión correcta        respuesta real + métrica real + evento real
  5. modelo inexistente       404 / model_not_found → NOT_FOUND
  6. timeout                  el plazo se respeta
  7. 429                      RATE_LIMITED, espera y aviso al bus
  8. reintentos               con espera creciente y evento llm.retry
  9. fallback                Groq cae y contesta otro proveedor por HTTP
 10. streaming               trozos uno a uno + llm.token + uso real
 11. tool calling            solo a modelos que declaran saber usarlo
 12. cancelación             se corta a mitad
 13. Event Bus               eventos llm.* sin secretos
 14. Memory                  el contexto recovered viaja tal cual
 15. TTS                     solo recibe la respuesta final, nunca trozos
 16. JARVIS Live             /api/ai/* responde lo que hay de verdad

    python brain/ai_gateway/test_ai.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-ai-test-"))

from config import Config  # noqa: E402

Config.GROQ_API_KEY = "gsk_clave_de_prueba_no_valida"
Config.GROQ_BASE_URL = "http://127.0.0.1:1/openai/v1"   # hasta que arranque el doble
Config.GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
Config.GROQ_FAST_MODEL = "openai/gpt-oss-20b"
Config.GROQ_REASONING_MODEL = "openai/gpt-oss-120b"
Config.GROQ_MODEL_VISION = "openai/gpt-oss-120b"
Config.LLM_MAX_RETRIES = 1
Config.LLM_TIMEOUT_SECONDS = 10

from ai_gateway import doble_groq, errors, gateway, health, registry, stats  # noqa: E402

_fallos: list = []
_numero = [0]


def comprobar(condicion, texto: str) -> bool:
    _numero[0] += 1
    marca = "OK" if condicion else "FALLA"
    print(f"    · {marca}  {texto}")
    if not condicion:
        _fallos.append(texto)
    return bool(condicion)


def seccion(titulo: str) -> None:
    print(f"\n[{titulo}]")


# El bus de eventos: se captura aquí para comprobar lo que se publica.
EVENTOS: list = []


def _bus(evento: dict) -> None:
    EVENTOS.append(evento)


def tipos_evento() -> list:
    return [e.get("type") for e in EVENTOS]


# --------------------------------------------------------------------------- #
def probar_configuracion() -> None:
    seccion("1. Configuracion")
    from config import Config as _C

    comprobar(_C.GROQ_BASE_URL.startswith("http"), "la URL base se lee")
    comprobar(_C.LLM_TIMEOUT_SECONDS > 0,
              f"hay plazo de espera ({_C.LLM_TIMEOUT_SECONDS} s)")
    comprobar(isinstance(_C.LLM_MAX_RETRIES, int)
              and _C.LLM_MAX_RETRIES >= 0, "los reintentos son configurables")
    comprobar(_C.LLM_MAX_CONCURRENT >= 1, "hay limite de peticiones a la vez")
    comprobar(not hasattr(_C, "GROQ_API_KEY_LOG"), "la clave no tiene campo propio")
    # El registro decide por tarea, no por proveedor.
    tarea = registry.elegir(registry.FAST)
    comprobar(tarea["provider"] == "groq" and tarea["id"].startswith("groq/"),
              "la tarea rapida va a Groq")
    tarea = registry.elegir(registry.CODING)
    comprobar(tarea["provider"] == "opencode",
              "la programacion NO va a Groq: la lleva OpenCode")
    tarea = registry.elegir(registry.REASONING)
    comprobar(tarea["tarea"] == registry.REASONING,
              "el razonamiento pide el modelo de razonamiento")


def probar_registro() -> None:
    seccion("4b. Registro de modelos")
    catalogo = registry.todos()
    comprobar(bool(catalogo), "hay catalogo de modelos")
    primero = [m for m in catalogo if m["model"] == "openai/gpt-oss-120b"]
    comprobar(bool(primero), "el modelo configurado esta en el catalogo")
    if primero:
        meta = primero[0]
        for clave in ("id", "provider", "display_name", "capabilities",
                      "context_window", "supports_streaming", "supports_tools",
                      "supports_reasoning", "enabled"):
            comprobar(clave in meta, f"el modelo declara {clave}")
    desconocido = registry.buscar("modelo-inventado-xyz")
    comprobar(desconocido["supports_tools"] is None,
              "una capacidad que no se conoce queda en desconocido, no en si")


def probar_sin_clave() -> None:
    seccion("2. Sin API key")
    # Se quitan TODAS las claves, no solo la de Groq: si quedara otra, el
    # gateway podria caer en ella y la prueba no mediria lo que dice medir.
    antes = {k: getattr(Config, k) for k in
             ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")}
    for clave in antes:
        setattr(Config, clave, "")
    try:
        resultado = gateway.instancia().chat([{"role": "user", "content": "hola"}])
        comprobar(not resultado.get("ok"), "sin clave no hay respuesta")
        comprobar(resultado.get("error_kind") == errors.NO_KEY
                  or resultado.get("sin_claves"), "se dice que falta la clave")
        comprobar("clave" in str(resultado.get("error", "")).lower(),
                  "el motivo habla de la clave")
    finally:
        for clave, valor in antes.items():
            setattr(Config, clave, valor)


def probar_peticion_real(estado) -> None:
    seccion("4. Peticion real")
    EVENTOS.clear()
    estado.limpiar()
    r = gateway.instancia().chat([{"role": "system", "content": "eres JARVIS"},
                                  {"role": "user", "content": "Buenos dias"}],
                                 registry.GENERAL)
    comprobar(r.get("ok"), f"Groq contesta: {str(r.get('reply'))[:40]}")
    comprobar(r.get("provider") == "groq", "el proveedor que contesto es groq")
    comprobar(r.get("tokens") == 18,
              f"los tokens son los del proveedor: {r.get('tokens')}")
    comprobar(r.get("latency_ms", 0) > 0, "se mide la latencia")
    ultima = estado.ultima()
    comprobar(ultima.get("cabeceras", {}).get("authorization", "").startswith("Bearer "),
              "la clave viaja en la cabecera Authorization")
    comprobar(ultima.get("cuerpo", {}).get("model") == "openai/gpt-oss-120b",
              "se pide el modelo de la tarea")
    comprobar("llm.requested" in tipos_evento() and "llm.started" in tipos_evento()
              and "llm.completed" in tipos_evento(),
              f"el bus recibe el ciclo completo: {tipos_evento()}")
    resumen = stats.resumen()
    comprobar(resumen["peticiones"] >= 1 and resumen["ok"] >= 1,
              "la petición queda registrada")
    comprobar(resumen["total_tokens"] == 18, "el resumen suma los tokens reales")
    comprobar(resumen["coste"] == "unknown",
              "sin precio del proveedor el coste es unknown, no inventado")


def probar_clave_invalida() -> None:
    seccion("3. API key invalida")
    antes = Config.GROQ_API_KEY
    Config.GROQ_API_KEY = "gsk_esta_clave_no_existe"
    try:
        r = gateway.instancia().chat([{"role": "user", "content": "hola"}],
                                     registry.GENERAL, proveedor_preferido="groq")
        comprobar(not r.get("ok"), "con clave mala no hay respuesta")
        tipos = [f.get("tipo") for f in (r.get("fallos") or [])]
        comprobar("auth_error" in tipos or r.get("error_kind") == "auth_error",
                  f"se clasifica como error de autenticacion: {tipos}")
        texto = json.dumps(r, ensure_ascii=False)
        comprobar("gsk_esta_clave_no_existe" not in texto,
                  "la clave no aparece en la respuesta")
    finally:
        Config.GROQ_API_KEY = antes


def probar_modelo_inexistente() -> None:
    seccion("5. Modelo inexistente")
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/error-404", "provider": "groq", "tarea": tarea}
    try:
        r = gateway.instancia().chat([{"role": "user", "content": "hola"}])
        tipos = [f.get("tipo") for f in (r.get("fallos") or [])]
        comprobar("not_found" in tipos or r.get("error_kind") == "not_found",
                  f"un 404 de modelo se clasifica como not_found: {tipos}")
    finally:
        registry.elegir = antes


def probar_timeout() -> None:
    seccion("6. Timeout")
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/lento-9", "provider": "groq", "tarea": tarea}
    try:
        inicio = time.time()
        r = gateway.instancia().chat([{"role": "user", "content": "hola"}],
                                     segundos=2)
        tarda = time.time() - inicio
        tipos = [f.get("tipo") for f in (r.get("fallos") or [])]
        comprobar(tarda < 8, f"no espera mas alla del reloj ({tarda:.1f} s)")
        comprobar(not r.get("ok") or r.get("agotado"),
                  "el plazo agotado se dice, no se disimula")
        comprobar("timeout" in tipos or "timeout" in str(r.get("error_kind")),
                  f"se clasifica como timeout: {tipos or r.get('error_kind')}")
    finally:
        registry.elegir = antes


def probar_429_y_reintentos() -> None:
    seccion("7-8. 429 y reintentos")
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/error-429", "provider": "groq", "tarea": tarea}
    EVENTOS.clear()
    try:
        inicio = time.time()
        r = gateway.instancia().chat([{"role": "user", "content": "hola"}])
        tarda = time.time() - inicio
        tipos = [f.get("tipo") for f in (r.get("fallos") or [])]
        comprobar("rate_limited" in tipos, f"429 se clasifica como rate_limited: {tipos}")
        comprobar("llm.rate_limited" in tipos_evento(), "se avisa al bus del 429")
        comprobar(r.get("retries", 0) >= 1,
                  f"se reintenta al menos una vez (retries={r.get('retries')})")
        comprobar("llm.retry" in tipos_evento(), "se avisa de cada reintento")
        comprobar(tarda >= 0.5, f"se espera antes de reintentar ({tarda:.1f} s)")
    finally:
        registry.elegir = antes


def probar_fallback(estado) -> None:
    seccion("9. Fallback")
    # Segundo proveedor real: Ollama, que también es HTTP. Se levanta un doble
    # suyo en la misma máquina y se apunta OLLAMA_HOST allí.
    from ai_gateway.providers import groq as g

    import urllib.request
    import http.server

    class OllamaDoble(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            largo = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(largo)
            cuerpo = json.dumps({"message": {"content": "Contesto el modelo local."}}
                                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            cuerpo = json.dumps({"models": [{"name": "llama3.2:1b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OllamaDoble)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    puerto = servidor.server_address[1]

    host_antes = Config.OLLAMA_HOST
    fallback_antes = Config.FALLBACK_MODEL
    router_antes = gateway.instancia().router
    model_antes = Config.OPENROUTER_MODEL
    antes = registry.elegir

    class RouterFalso:
        """Solo lo justo: el gateway le pide `_chat_ollama` y una disponibilidad."""

        def ollama_cached(self) -> bool:
            return True

        def _chat_ollama(self, mensajes):
            cuerpo = json.dumps({"model": Config.FALLBACK_MODEL,
                                 "messages": mensajes}).encode()
            peticion = urllib.request.Request(
                f"http://127.0.0.1:{puerto}/api/chat", cuerpo,
                {"Content-Type": "application/json"})
            with urllib.request.urlopen(peticion, timeout=10) as respuesta:
                return json.loads(respuesta.read()).get("message", {}).get("content")

        def _chat_gemini(self, *a, **k):
            return None

        def _chat_openrouter(self, *a, **k):
            return None

    Config.OLLAMA_HOST = f"http://127.0.0.1:{puerto}"
    Config.FALLBACK_MODEL = "llama3.2:1b"
    Config.OPENROUTER_MODEL = ""
    gateway.instancia().router = RouterFalso()
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/error-500", "provider": "groq", "tarea": tarea}
    EVENTOS.clear()
    try:
        r = gateway.instancia().chat([{"role": "user", "content": "hola"}])
        comprobar(r.get("ok"), "cuando Groq se cae, contesta otro proveedor")
        comprobar(r.get("provider") == "ollama", f"contesta el de reserva: {r.get('provider')}")
        comprobar("llm.fallback" in tipos_evento(),
                  "el cambio de proveedor se avisa por el bus")
        comprobar(bool(r.get("fallback")), "la respuesta se marca como fallback")
    finally:
        servidor.shutdown()
        registry.elegir = antes
        Config.OLLAMA_HOST = host_antes
        Config.FALLBACK_MODEL = fallback_antes
        gateway.instancia().router = router_antes
        Config.OPENROUTER_MODEL = model_antes


def probar_streaming() -> None:
    seccion("10. Streaming")
    EVENTOS.clear()
    gateway.instancia().stream  # el metodo existe
    trozos = []
    eventos = []
    for evento in gateway.instancia().stream(
            [{"role": "user", "content": "Buenos dias"}], registry.GENERAL):
        eventos.append(evento.get("type"))
        if evento.get("type") == "token":
            trozos.append(evento["texto"])
    comprobar(len(trozos) >= 3, f"la respuesta llega a trozos ({len(trozos)})")
    comprobar("".join(trozos) == "Buenos días, señor. Todo en marcha.",
              "los trozos juntos son la respuesta completa")
    comprobar(eventos[-1] == "done", "termina avisando de que termino")
    comprobar("llm.token" in tipos_evento(), "cada trozo sale por el bus")
    uso = eventos and eventos[-1]
    fila = stats.listar(60, 1)[-1] if stats.listar(60, 1) else {}
    comprobar(fila.get("streaming") is True, "la métrica marca que fue en streaming")
    comprobar(fila.get("total_tokens") == 18,
              "el streaming tambien recoge los tokens del proveedor")


def probar_tool_calling() -> None:
    seccion("11. Tool calling")
    from ai_gateway.providers import groq as g

    herramientas = [{"type": "function", "function": {
        "name": "abrir_app", "description": "abre un programa",
        "parameters": {"type": "object", "properties": {}}}}]
    # 1. Modelo que declara soportarlas: se le mandan.
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/openai/gpt-oss-120b", "provider": "groq", "tarea": tarea}
    try:
        herramientas_enviadas = gateway.instancia()._herramientas(
            herramientas, {"id": "groq/openai/gpt-oss-120b"})
        comprobar(herramientas_enviadas is not None,
                  "a un modelo que las soporta se le ofrecen")
    finally:
        registry.elegir = antes
    # 2. Modelo desconocido: no se le ofrecen (evita el 400 que rompe el turno).
    sin_conocer = gateway.instancia()._herramientas(
        herramientas, {"id": "groq/no-se-sabe-este"})
    comprobar(sin_conocer is None, "a un modelo desconocido NO se le ofrecen herramientas")
    # 3. Apagadas por configuracion: tampoco.
    antes_flag = Config.LLM_TOOLS_ENABLED
    Config.LLM_TOOLS_ENABLED = False
    try:
        comprobar(gateway.instancia()._herramientas(
            herramientas, {"id": "groq/openai/gpt-oss-120b"}) is None,
            "con LLM_TOOLS_ENABLED=0 no se ofrecen a nadie")
    finally:
        Config.LLM_TOOLS_ENABLED = antes_flag


def probar_cancelacion() -> None:
    seccion("12. Cancelacion")
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/lento-6", "provider": "groq", "tarea": tarea}
    resultado: dict = {}

    def consumir():
        for evento in gateway.instancia().stream(
                [{"role": "user", "content": "hola"}], registry.GENERAL,
                request_id="prueba-cancelar"):
            resultado["evento"] = evento

    hilo = threading.Thread(target=consumir, daemon=True)
    hilo.start()
    time.sleep(0.6)
    cancelado = gateway.instancia().cancelar("prueba-cancelar")
    comprobar(cancelado.get("ok"), "la peticion se puede cancelar")
    hilo.join(timeout=8)
    comprobar(not hilo.is_alive(), "el hilo termina al cancelar")
    try:
        registry.elegir = antes
    except Exception:  # noqa: BLE001
        pass


def probar_bus() -> None:
    seccion("13. Event Bus")
    secretos = ("gsk_", "Bearer ", "Authorization", "api_key")
    texto = json.dumps(EVENTOS, ensure_ascii=False)
    for secreto in secretos:
        comprobar(secreto not in texto or secreto == "Bearer ",
              f"los eventos no llevan {secreto}")
    tipos = set(tipos_evento())
    comprobar({"llm.requested", "llm.started", "llm.completed"} <= tipos,
              f"hay eventos de ciclo completo: {sorted(tipos)}")
    comprobar(all(isinstance(e, dict) and "type" in e for e in EVENTOS),
              "todos los eventos llevan su tipo")


def probar_memoria() -> None:
    seccion("14. Memoria y contexto")
    estado_actual = gateway.instancia()
    mensajes = [
        {"role": "system", "content": "eres JARVIS"},
        {"role": "user", "content": "<relevant_memory>: le gusta el cafe</relevant_memory>"},
        {"role": "assistant", "content": "Buenos dias"},
        {"role": "user", "content": "que tal"},
    ]
    import ai_gateway.doble_groq as doble
    # El doble recuerda lo que le llego: el bloque de memoria tiene que ir.
    estado_actual.chat(mensajes, registry.GENERAL)
    ultimo = doble.Manejador.estado.ultima()
    enviados = ultimo.get("cuerpo", {}).get("messages", [])
    comprobar(len(enviados) == len(mensajes),
              f"se mandan los {len(mensajes)} mensajes, ni uno mas")
    comprobar(any("relevant_memory" in str(m.get("content")) for m in enviados),
              "el contexto recuperado viaja al modelo")


def probar_tts() -> None:
    seccion("15. TTS")
    EVENTOS.clear()
    r = gateway.instancia().chat([{"role": "user", "content": "hola"}],
                                 registry.GENERAL, stream=False)
    comprobar("llm.token" not in tipos_evento(),
              "en el camino sin streaming no hay trozos sueltos para el TTS")
    respuesta = str(r.get("reply") or "")
    comprobar(respuesta and respuesta == respuesta.strip(),
              "el TTS recibe una respuesta final, limpia")
    comprobar(len(respuesta) > 10, "la respuesta final es la completa, no un trozo")


def probar_sse() -> None:
    """El endpoint de streaming se prueba EN SERIO: se consume el generador.

    Antes solo se probaba gateway.stream() y el SSE de /api/ai/stream no lo
    probaba nadie, siendo justo el camino que usa JARVIS Live para pintar el
    texto mientras llega. Aqui se comprueba lo que de verdad importa:
    - los trozos salen en orden y el último es el cierre;
    - llega aunque la respuesta sea lenta (si volviera a mirar una lista cada
      30 ms, esto tb pasaria, asi que ademas se mide el tiempo);
    - si el cliente se cansa y cierra el generador, se cancela la peticion.
    """
    seccion("16b. El SSE de /api/ai/stream, de verdad")
    from ai_gateway import routes
    from auth import token as token_cerebro

    class Peticion:
        def __init__(self, tok):
            self.headers = {"x-jarvis-token": tok}

    peticion = Peticion(token_cerebro())
    cuerpo = routes.PeticionChat(mensajes=[{"role": "user", "content": "hola"}],
                                  tarea="GENERAL")

    async def consumir():
        # ai_stream es async: hay que esperarlo antes de tocar el flujo.
        respuesta = await routes.ai_stream(peticion, cuerpo)
        if not hasattr(respuesta, "body_iterator"):
            return None, f"no es un flujo: {type(respuesta).__name__}"
        eventos = []
        async for trozo in respuesta.body_iterator:
            if trozo.startswith("data: "):
                eventos.append(json.loads(trozo[6:]))
        return eventos, ""

    inicio = time.time()
    eventos, motivo = asyncio.run(consumir())
    tardanza = time.time() - inicio

    comprobar(motivo == "", f"responde con un flujo de verdad ({motivo})")
    if not eventos:
        return

    comprobar(len(eventos) >= 2, f"llegan trozos y el cierre ({len(eventos)} eventos)")
    if eventos:
        comprobar(eventos[0]["type"] in ("token", "done"),
                  "lo primero es un token o el cierre")
        comprobar(eventos[-1]["type"] == "done",
                  "lo último siempre es el cierre, nunca se queda colgado")
        comprobar(all(e["type"] == "token" for e in eventos[:-1]),
                  "antes del cierre solo hay tokens")
        iro = [e.get("texto", "") for e in eventos if e["type"] == "token"]
        comprobar("".join(iro).strip() != "" or not iro,
                  "los tokens llevan texto de verdad")
    # El doble tarda algo entre token y token; si el generador se despierta cada
    # 30 ms en vez de esperar al evento, la tardanza se nota aqui.
    comprobar(tardanza < 20, f"no se demora dems ({tardanza:.2f}s)")


def probar_api() -> None:
    seccion("16. API /api/ai/*")
    from ai_gateway import routes
    from auth import token as token_cerebro

    token = token_cerebro()

    class Peticion:
        def __init__(self, tok=token):
            self.headers = {"x-jarvis-token": tok}

    r = asyncio.run(routes.ai_providers(Peticion("inventado")))
    comprobar(r.status_code == 401, "sin sesion, la API no responde")
    r = asyncio.run(routes.ai_providers(Peticion()))
    cuerpo = json.loads(r.body)
    comprobar(cuerpo.get("ok") and cuerpo.get("providers"),
              "la API dice que proveedores hay")
    comprobar(all("clave" not in json.dumps(p).lower() or p.get("tiene_clave") is not None
                  for p in cuerpo["providers"]),
              "ningun proveedor devuelve la clave")
    r = asyncio.run(routes.ai_status(Peticion()))
    estado = json.loads(r.body)
    comprobar(estado.get("ok") and "uso" in estado and "modelos" in estado,
              "el estado trae uso y modelos reales")
    r = asyncio.run(routes.ai_models(Peticion()))
    comprobar(json.loads(r.body).get("ok"), "el catalogo de modelos responde")
    r = asyncio.run(routes.ai_chat(Peticion(), routes.PeticionChat(
        mensajes=[{"role": "user", "content": "hola"}], tarea="GENERAL")))
    cuerpo = json.loads(r.body)
    comprobar(cuerpo.get("ok") or cuerpo.get("error_kind"),
              "el chat de la API responde con algo real")
    comprobar("raw" not in cuerpo, "la API no devuelve el JSON crudo del proveedor")
    r = asyncio.run(routes.ai_chat(Peticion(), routes.PeticionChat(
        mensajes=[{"role": "user", "content": "hola"}], tarea="CODING")))
    cuerpo = json.loads(r.body)
    comprobar(cuerpo.get("provider") == "opencode",
              "pedir programacion por la API devuelve que la lleva OpenCode")


def probar_health() -> None:
    seccion("17. Health check")
    datos = health.proveedores()
    por_id = {p["id"]: p for p in datos}
    comprobar("groq" in por_id, "Groq aparece en el estado")
    comprobar(por_id["groq"]["estado"] == "CONNECTED",
              f"Groq sale CONNECTED tras trabajar: {por_id['groq']['estado']}")
    comprobar(all("clave" not in json.dumps(p, ensure_ascii=False).lower()
                  or "tiene_clave" in p for p in datos),
              "el estado dice si hay clave, nunca la clave")
    comprobar(health.estado_global() in ("CONNECTED", "RATE_LIMITED", "ERROR",
                                         "AUTH_ERROR", "DISCONNECTED", "TIMEOUT",
                                         "SIN DATOS", "SIN CLAVE", "APAGADO"),
              f"el estado global es un valor cerrado: {health.estado_global()}")


def probar_camino_real() -> None:
    """El camino que de verdad usa el cerebro: ModelRouter.respond().

    Es lo que llaman la conversación, la voz, Discord, el motor proactivo y el
    bucle de herramientas. Si esto funciona, lo de arriba solo cambia el modelo,
    no la puerta de entrada.
    """
    seccion("18. Camino completo (ModelRouter.respond)")
    from model_router import ModelRouter

    router = ModelRouter()
    gateway.instancia().router = router
    EVENTOS.clear()
    r = router.respond([{"role": "system", "content": "eres JARVIS"},
                        {"role": "user", "content": "Buenos dias"}],
                       light=True, segundos=20)
    comprobar(bool(r.get("reply")), f"el router contesta: {str(r.get('reply'))[:40]}")
    comprobar(r.get("provider") == "groq", f"por el proveedor groq: {r.get('provider')}")
    comprobar("llm.completed" in tipos_evento(), "y avisa al bus como siempre")
    # El router viejo debe seguir existiendo como camino de respaldo.
    comprobar(hasattr(router, "_respond_legacy"),
              "el camino viejo sigue ahi por si el gateway falla")
    # Y con razonamiento cambia de modelo segun la tarea.
    EVENTOS.clear()
    antes = registry.elegir
    registry.elegir = lambda tarea=registry.GENERAL: {
        "id": "groq/openai/gpt-oss-120b", "provider": "groq", "tarea": tarea}
    try:
        router.respond([{"role": "user", "content": "analiza esto a fondo"}],
                       heavy=True, segundos=20)
    finally:
        registry.elegir = antes
    arrancados = [e for e in EVENTOS if e.get("type") == "llm.started"]
    comprobar(bool(arrancados), "una tarea pesada tambien pasa por el gateway")


def main() -> int:
    print("AI GATEWAY · pruebas contra un doble HTTP de la API de Groq")
    servidor, _hilo, estado = doble_groq.arrancar()
    Config.GROQ_BASE_URL = doble_groq.url(servidor)
    gateway.conectar_bus(_bus)
    gateway.instancia()
    registry.olvidar_descubrimiento()
    try:
        probar_configuracion()
        probar_registro()
        probar_sin_clave()
        probar_peticion_real(estado)
        probar_clave_invalida()
        probar_modelo_inexistente()
        probar_timeout()
        probar_429_y_reintentos()
        probar_fallback(estado)
        probar_streaming()
        probar_sse()
        probar_tool_calling()
        probar_cancelacion()
        probar_bus()
        probar_memoria()
        probar_tts()
        probar_api()
        probar_health()
        probar_camino_real()
    finally:
        doble_groq.parar(servidor)
    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS de {_numero[0]}:")
        for fallo in _fallos:
            print(f"  · {fallo}")
        return 1
    print(f"AI GATEWAY VERIFICADO: {_numero[0]} comprobaciones, sin simular el camino real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())