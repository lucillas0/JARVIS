"""Model Router de 3 niveles (sección 2 y 12.5 de la especificación).

1. Groq  → respuestas conversacionales simples y rápidas (primer intento)
2. Gemini → tareas pesadas: razonamiento complejo, visión, planificar pasos
3. OpenRouter → última alternativa si Groq y Gemini fallan

Fallback offline: Ollama (modelo local pequeño) cuando fallan los tres o no hay
internet. JARVIS nunca se queda mudo.
"""
import base64
import io
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from config import Config

log = logging.getLogger("model_router")

# Hasta cuándo no se vuelve a llamar a Groq si dice que se le acabó la cuota.
# El enfriamiento es progresivo y arranca corto: un 429 puntual por minuto
# cuesta ahora 45 s, no diez minutos. Antes, un solo 429 dejaba al seńor
# esperando 5-13 s en Gemini el resto de la sesión, y por eso las búsquedas
# parecían lentas aunque la búsqueda en sí tardara 0,8 s.
_GROQ_COOLDOWN = 0.0
_GROQ_PENALTY = 0
_GROQ_ESPERA_MIN = 45
_GROQ_ESPERA_MAX = 600


def _groq_enfriado(veces: int) -> float:
    """Espera tras un 429: 45 s, 90, 180... hasta 10 min."""
    return min(_GROQ_ESPERA_MAX, _GROQ_ESPERA_MIN * (2 ** max(0, veces - 1)))

# Reloj de la petición en curso. Si está puesto, ninguna llamada HTTP puede
# pasarse: es lo que evita que una respuesta lenta se convierta en medio
# minuto de espera para el señor.
_DEADLINE = 0.0


def _queda() -> int:
    """Segundos que le quedan a la petición, o 45 si no hay reloj."""
    if not _DEADLINE:
        return 45
    return max(2, int(_DEADLINE - time.time()))

_ollama_cache = {"t": 0.0, "v": False}
_OLLAMA_TTL = 8.0


def _tiene_clave(nombre: str) -> bool:
    """Si hay clave puesta para este proveedor.

    "groq-light" es el mismo Groq con otro modelo, asi que cuenta la misma
    clave. Se lee de Config en cada llamada y no de una variable cacheada:
    /setup actualiza Config al instante y quiero que se note sin reiniciar.
    """
    if nombre.startswith("groq"):
        return bool(Config.GROQ_API_KEY)
    if nombre == "gemini":
        return bool(Config.GEMINI_API_KEY)
    if nombre == "openrouter":
        return bool(Config.OPENROUTER_API_KEY)
    return False


class ModelRouter:
    def __init__(self):
        self._groq_client = None
        self._gemini = None
        self._openrouter = None
        self._provider = "groq"
        # Por qué falló cada proveedor en el último intento.
        #
        # Antes no existía: cada _chat_X devolvía None al fallar y el router
        # anotaba solo el NOMBRE ("groq"). El motivo estaba a una línea de
        # distancia, en el log, y se tiraba. Por eso el mensaje final solo
        # podía decir "todos fallaron, mire el registro", que no es un
        # diagnóstico: es devolver el problema al que pregunta.
        self._errores: dict[str, str] = {}
        # Si ya se ha esperado al enfriamiento de Groq en ESTA petición: no se
        # vuelve a esperar, o dos entradas de Groq (groq-light y groq) harían
        # esperar el doble.
        self._groq_esperado = False

    def _fallo(self, nombre: str, exc: Exception | str) -> None:
        texto = str(exc)
        self._errores[nombre] = texto[:200]
        log.warning("%s falló: %s", nombre, texto)

    # Traduce el fallo del proveedor a algo que el señor pueda arreglar. El
    # detalle técnico se queda en el log; aquí solo se dice la causa, porque
    # "mire el registro" no es una respuesta: es devolverle el problema a quien
    # ha preguntado. Y la clave de la API nunca sale de aquí.
    _MOTIVOS = {
        "auth_error": "la clave no es válida o está caducada",
        "rate_limited": "se ha acabado la cuota o hay demasiado tráfico",
        "not_found": "el modelo configurado no existe o ya no está disponible",
        "unavailable": "su servicio está caído o no se le puede alcanzar",
        "malformed": "el modelo ha devuelto algo que no se puede usar",
        "timeout": "ha tardado demasiado y se ha cortado",
    }

    def _motivo(self, exc) -> str:
        tipo = getattr(exc, "tipo", "") or ""
        if tipo in self._MOTIVOS:
            return self._MOTIVOS[tipo]
        estado = getattr(exc, "estado", None)
        if estado in (401, 403):
            return self._MOTIVOS["auth_error"]
        if estado == 429:
            return self._MOTIVOS["rate_limited"]
        if estado == 404:
            return self._MOTIVOS["not_found"]
        if estado in (500, 502, 503, 504):
            return self._MOTIVOS["unavailable"]
        texto = str(exc).lower()
        if "api key" in texto or "unauthorized" in texto or "forbidden" in texto:
            return self._MOTIVOS["auth_error"]
        if "connection" in texto or "resolve" in texto or "network" in texto \
                or "dns" in texto or "unreachable" in texto:
            return "no se le puede alcanzar desde aquí"
        if "rate" in texto or "quota" in texto or "limit" in texto:
            return self._MOTIVOS["rate_limited"]
        return "ha fallado sin motivo claro"

    # ---------- clientes (init perezoso, cada proveedor independiente) ----------
    def groq(self):
        if self._groq_client is None and Config.GROQ_API_KEY:
            try:
                from groq import Groq
                # max_retries=0 es importante: por dentro el SDK reintenta
                # solo cuando hay tiempo, y con un reloj de segundos cada
                # reintento multiplicaba la espera por dos o por tres. Que
                # reintente el router, que sabe cuándo le queda tiempo.
                self._groq_client = Groq(api_key=Config.GROQ_API_KEY,
                                         max_retries=0)
            except Exception as e:
                log.warning("Groq client no disponible: %s", e)
        return self._groq_client

    def gemini(self):
        if self._gemini is None and Config.GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=Config.GEMINI_API_KEY)
                self._gemini = genai.GenerativeModel(Config.GEMINI_MODEL_HEAVY)
            except Exception as e:
                log.warning("Gemini no disponible: %s", e)
        return self._gemini

    def openrouter(self):
        if self._openrouter is None and Config.OPENROUTER_API_KEY:
            try:
                from openai import OpenAI
                self._openrouter = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=Config.OPENROUTER_API_KEY,
                    max_retries=0,
                    default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "JARVIS"},
                )
            except Exception as e:
                log.warning("OpenRouter no disponible: %s", e)
        return self._openrouter

    def ollama_available(self) -> bool:
        now = time.time()
        if now - _ollama_cache["t"] > _OLLAMA_TTL:
            _ollama_cache["t"] = now
            _ollama_cache["v"] = bool(_probe_ollama())
        return _ollama_cache["v"]

    def ollama_cached(self) -> bool:
        """Lee la disponibilidad SIN lanzar un probe en vivo (nunca bloquea)."""
        return _ollama_cache["v"] if time.time() - _ollama_cache["t"] <= _OLLAMA_TTL else False

    # ---------- llamadas por proveedor ----------
    def _modelo_alterno_groq(self, modelo: str) -> str:
        """Otro modelo de Groq para cuando este se ha quedado sin cuota.

        Se le pregunta a Groq qué modelos tiene (`GET /models`), que es la
        única fuente fiable: la lista escrita a mano se queda vieja, y ya ha
        pasado —la clave ya no tiene `llama-3.3-70b-versatile` ni
        `llama-3.1-8b-instant`.

        De los que devuelve se descartan los de voz (whisper) y los de
        vigilancia, y se elige el más grande que quede: mejor respuesta antes
        que respuesta rápida.
        """
        try:
            from ai_gateway.providers import groq as _groq_api
            modelos = _groq_api.listar_modelos(timeout=8.0)
        except Exception as e:  # noqa: BLE001
            log.debug("no se pudo pedir la lista de modelos a Groq: %s", e)
            return ""
        ids = []
        for m in modelos or []:
            if isinstance(m, dict):
                ident = str(m.get("id") or "")
            else:
                ident = str(m or "")
            if not ident or ident == modelo:
                continue
            if "whisper" in ident or "guard" in ident:
                continue
            ids.append(ident)
        if not ids:
            return ""
        # A mayor tamaño de modelo, mejor respuesta. El nombre suele traer el
        # número de parámetros ("120b", "27b"), pero no siempre es el primer
        # número: "qwen/qwen3.8-27b" tiene el 27 al final, y el 3.8 es la
        # versión. Se coge el número más grande de los que haya.
        import re as _re

        def _clave(ident: str):
            numeros = [int(n) for n in _re.findall(r"\d+", ident)]
            return (-(max(numeros) if numeros else 0), ident)

        return sorted(ids, key=_clave)[0]

    def _chat_groq(self, messages: list[dict], vision: Optional[str] = None,
                   model: Optional[str] = None) -> Optional[str]:
        """Groq por su API oficial, a traves del proveedor del AI Gateway.

        Antes esta funcion usaba el SDK de Groq. Ahora el mismo camino lo lleva
        `ai_gateway.providers.groq`, que habla la API por HTTP: asi el gateway
        puede emitir cada trozo por el bus, cortar a mitad y clasificar los
        errores. No hay dosimplementaciones de Groq, solo una.
        """
        global _GROQ_COOLDOWN, _GROQ_PENALTY
        # Si Groq acaba de decir que se le acabó la cuota, no se vuelve a
        # llamar. Antes se probaba dos veces (el modelo pequeño y el grande),
        # las dos con el mismo problema, y se perdían hasta diez segundos
        # antes de caer en otro proveedor.
        if _GROQ_COOLDOWN > time.time():
            # PERO si la petición todavía tiene sitio, se espera a que Groq se
            # enfríe y se vuelve a intentarlo. Medido en el escritorio: con el
            # enfriamiento puesto, la cuarta pregunta seguidas se rendía con
            # «Groq y Gemini fallaron», cuando un minuto después Groq
            # contestaba sin problema. Es peor tardar medio minuto que no
            # contestar nada.
            faltan = _GROQ_COOLDOWN - time.time()
            if (not self._groq_esperado and _DEADLINE
                    and _queda() > faltan + 5):
                self._groq_esperado = True
                log.info("Groq está en enfriamiento (%.0f s); se espera y se "
                         "vuelve a intentar", faltan)
                espera = min(faltan, max(0.0, _queda() - 3))
                if espera > 0:
                    time.sleep(espera)
            else:
                return None
        if not _tiene_clave("groq"):
            return None
        from ai_gateway.errors import AIError
        from ai_gateway.providers import groq as _groq_api

        start = time.time()
        modelo = model or Config.GROQ_MODEL_FAST
        if vision:
            modelo = Config.GROQ_MODEL_VISION or modelo
        try:
            r = _groq_api.completar(messages, modelo, vision=vision,
                                    timeout=_queda())
        except AIError as e:
            # 429 por cuota (tokens por día o por minuto): se apaga un rato
            # en vez de reintentar y perder el tiempo. El enfriamiento empieza
            # corto y sube solo si Groq sigue igual.
            if e.tipo == "rate_limited":
                # ANTES de rendirse, se prueba OTRO modelo del mismo
                # proveedor. El límite de Groq es por modelo, no de la cuenta
                # entera: medido, con `openai/gpt-oss-120b` ya limitado por
                # tokens por minuto, `qwen/qwen3.8-27b` contestaba sin
                # problema en el mismo instante.
                #
                # Antes esto se quitó porque se probaban el modelo pequeño y el
                # grande, y los dos tenían el mismo problema: son el mismo
                # modelo con distinto nombre. Lo que hace falta es cambiar de
                # modelo de verdad.
                alterno = self._modelo_alterno_groq(modelo)
                if alterno:
                    try:
                        r2 = _groq_api.completar(messages, alterno,
                                                 vision=vision, timeout=_queda())
                    except AIError as e2:
                        if e2.tipo != "rate_limited":
                            log.warning("Groq: el modelo alterno %s falló: %s",
                                        alterno, e2)
                    else:
                        log.info("Groq sin cuota en %s; se contesta con %s",
                                 modelo, alterno)
                        self._fallo("Groq", f"sin cuota en {modelo}; "
                                            f"se usó {alterno}")
                        if _GROQ_PENALTY:
                            _GROQ_PENALTY = 0
                        return r2.get("texto")
                _GROQ_PENALTY += 1
                espera = _groq_enfriado(_GROQ_PENALTY)
                _GROQ_COOLDOWN = time.time() + espera
                log.warning("Groq sin cuota: %d s de espera (intento %d).",
                            espera, _GROQ_PENALTY)
                self._fallo("Groq", f"sin cuota (límite de la API), "
                                    f"esperando {espera} s")
                return None
            # El prompt no cabe (p. ej. historial con un volcado grande): se
            # reintenta UNA vez con el sistema y los dos ultimos intercambios.
            # Sin esto, cada peticion siguiente falla igual hasta que el
            # historial rote y el cerebro queda mudo un rato.
            if "too large" in str(e).lower() or e.estado == 413:
                log.warning("Groq: prompt demasiado grande, reintento encogido.")
                try:
                    encogido = ([messages[0]] + messages[-4:]
                                if len(messages) > 5 else messages[-4:])
                    r = _groq_api.completar(encogido, modelo, timeout=_queda())
                    log.info("Groq OK en %.2fs (encogido)", time.time() - start)
                    _GROQ_PENALTY = 0
                    return r.get("texto")
                except AIError as e2:
                    log.warning("Groq encogido fallo: %s", e2)
                    self._fallo("Groq", f"la pregunta no cabe ni encogida: {e2}")
                    return None
            # gpt-oss intenta function-calling nativo y Groq lo rechaza con 400
            # ("Tool choice is none, but model called a tool"). Las herramientas
            # no viajan en los mensajes, las pone el propio modelo, asi que una
            # segunda llamada falla igual: se avisa y se cae al siguiente
            # proveedor, que es lo que queda bien.
            if e.estado == 400 and "tool" in str(e).lower():
                log.warning("Groq: el modelo insiste en llamar a una "
                            "herramienta; se pasa al siguiente proveedor.")
                self._fallo("Groq", "el modelo pidió usar una herramienta y "
                                    "este camino no se las pasa")
                return None
            # Un fallo que no es de cuota no demuestra nada sobre el limite,
            # asi que no se penaliza: se prueba normal.
            _GROQ_COOLDOWN = 0.0
            log.warning("Groq fallo: %s", e)
            self._fallo("Groq", self._motivo(e))
            return None
        log.info("Groq OK en %.2fs", time.time() - start)
        # Si va bien se olvida el castigo anterior: un 429 viejo no debe seguir
        # mandando en la espera cuando Groq ya responde.
        if _GROQ_PENALTY:
            _GROQ_PENALTY = 0
        return r.get("texto")


    def _chat_gemini(self, messages: list[dict], vision: Optional[str] = None) -> Optional[str]:
        model = self.gemini()
        if not model:
            return None
        start = time.time()
        try:
            # Sin timeout generate_content puede colgarse para siempre y dejar
            # a JARVIS en "thinking" eternamente. 45s como el resto.
            req_opts = {"timeout": _queda()}
            if vision:
                image = _load_image_for_gemini(vision)
                response = model.generate_content([messages[-1]["content"], image],
                                                  request_options=req_opts)
            else:
                system = next((m["content"] for m in messages if m.get("role") == "system"), "")
                transcript = "\n".join(
                    f'{"Asistente" if m["role"]=="assistant" else "Usuario"}: {m["content"]}'
                    for m in messages if m.get("role") != "system"
                )
                response = model.generate_content(f"{system}\n\n{transcript}",
                                                  request_options=req_opts)
            log.info("Gemini OK en %.2fs", time.time() - start)
            return response.text
        except Exception as e:
            self._fallo("Gemini", e)
            return None

    def _chat_openrouter(self, messages: list[dict], vision: Optional[str] = None) -> Optional[str]:
        client = self.openrouter()
        if not client:
            return None
        start = time.time()
        try:
            kwargs = {"messages": messages, "model": Config.OPENROUTER_MODEL}
            if vision:
                kwargs.update(_vision_payload_openrouter(messages, vision))
            completion = client.chat.completions.create(**kwargs, timeout=_queda())
            log.info("OpenRouter OK en %.2fs", time.time() - start)
            return completion.choices[0].message.content
        except Exception as e:
            self._fallo("OpenRouter", e)
            return None

    def _chat_ollama(self, messages: list[dict]) -> Optional[str]:
        if not _probe_ollama():
            return None
        try:
            payload = json.dumps({"model": Config.FALLBACK_MODEL, "messages": messages, "stream": False}).encode()
            req = urllib.request.Request(f"{Config.OLLAMA_HOST}/api/chat", payload,
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=_queda()) as resp:
                data = json.loads(resp.read())
            return data.get("message", {}).get("content")
        except Exception as e:
            log.warning("Ollama falló: %s", e)
            return None

    # ---------- fachada pública ----------
    def respond(self, messages: list[dict], heavy: bool = False, vision: Optional[str] = None,
                light: bool = False, segundos: float = 0.0,
                stream: bool = False) -> dict:
        """Punto de entrada de siempre: lo llama todo el cerebro.

        Ahora pasa por el AI Gateway, que es quien elige modelo, hace los
        reintentos, avisa al bus y guarda las metricas. La firma y el
        diccionario que devuelve son los de siempre, asi que los seis sitios
        que lo llaman (conversacion, voz, Discord, proactive, tool loop) no se
        han tocado. Si el gateway no esta disponible, sigue el camino viejo.
        """
        try:
            from ai_gateway import gateway as _gateway
            from ai_gateway import registry as _registry

            # Cada petición empieza sin esperas previas: el enfriamiento se
            # puede esperar una vez, no arrastrarse de una petición a otra.
            self._groq_esperado = False

            tarea = _registry.REASONING if heavy else (
                _registry.FAST if (light and not vision) else _registry.GENERAL)
            resultado = _gateway.instancia(self).chat(
                messages, tarea, vision=vision, segundos=segundos,
                stream=bool(stream) and Config.LLM_STREAM_ENABLED)
            if resultado.get("provider") != "opencode":
                # Un resultado SIN texto no es una respuesta: es un fallo. Y un
                # fallo del gateway no debe ser el final del camino, porque el
                # camino viejo existe justo para cuando el gateway falla.
                #
                # Antes solo se caía a `_respond_legacy` si el gateway LANZABA
                # una excepción. Si el gateway devolvía {"ok": false} —que es
                # lo que hace cuando los proveedores fallan o se acaba el
                # tiempo— se devolvía tal cual y la red de seguridad no se
                # probaba nunca. Un fallo del gateway (streaming, registro,
                # reloj) dejó a JARVIS sin poder hablar aunque el camino
                # antiguo hubiera contestado.
                if (resultado.get("ok") is False
                        or not str(resultado.get("reply") or "").strip()):
                    log.warning("el AI Gateway no devolvio texto (%s); "
                                "se prueba el camino viejo.",
                                resultado.get("provider"))
                else:
                    return resultado
        except Exception as exc:  # noqa: BLE001 - el gateway no puede romper la voz
            log.warning("el AI Gateway no respondio (%s); se usa el camino viejo.",
                        exc)
        return self._respond_legacy(messages, heavy=heavy, vision=vision,
                                    light=light, segundos=segundos)

    def _respond_legacy(self, messages: list[dict], heavy: bool = False, vision: Optional[str] = None,
                        light: bool = False, segundos: float = 0.0) -> dict:
        """Devuelve {'reply': str, 'provider': str, 'fallback': bool}.

        light=True (mensajes cortos cotidianos) → Groq pequeño primero.
        heavy=True → intenta Gemini primero (tareas pesadas).
        segundos > 0 → reloj: no se pasa de ese tiempo ni probando proveedores
                       de uno en uno, y si se agota se contesta corto.
        Sin internet → directo a Ollama (modo limitado).
        Fallback offline cuando fallan Groq, Gemini y OpenRouter.
        """
        limite = float(segundos or 0)
        _t0 = time.time()
        global _DEADLINE
        # El plazo se propaga a cada llamada: si una se queda colgada, el
        # cliente aborta al llegar el plazo en vez de esperar su propio timeout.
        _DEADLINE = (_t0 + limite) if limite else 0.0

        # ---- Que proveedores se prueban --------------------------------
        # Antes esta lista se metia dentro de "if _has_internet()", y ese
        # sondeo consultaba UN SOLO host, api.groq.com. Si ese host no
        # contestaba, la lista se quedaba VACIA y se caia directamente en
        # "Sin modelos disponibles" sin haber probado ni uno. Da igual que
        # las tres claves fueran validas.
        #
        # En un NAS eso pasa por tres cosas a la vez: el DNS del router que
        # resuelve otra cosa, el router bloqueando el dominio, o un corte de
        # dos segundos. Por eso salia "de repente", sin que hubiera cambiado
        # nada de la configuracion. Y si solo usas Gemini, que api.groq.com
        # no te llegue no deberia importarte: no lo hace.
        #
        # Ahora lo que decide es QUE CLAVES HAY, que es lo unico que importa.
        # Cada proveedor ya tiene su propio timeout y su propio manejo de
        # error, asi que no hace falta un sondeo previo para decidir nada.
        if heavy:
            orden = [
                ("gemini", lambda: self._chat_gemini(messages, vision)),
                ("groq", lambda: self._chat_groq(messages, vision)),
                ("openrouter", lambda: self._chat_openrouter(messages, vision)),
            ]
        elif light and not vision:
            orden = [
                ("groq-light", lambda: self._chat_groq(messages, vision, Config.GROQ_MODEL_LIGHT)),
                ("groq", lambda: self._chat_groq(messages, vision)),
                ("gemini", lambda: self._chat_gemini(messages, vision)),
                ("openrouter", lambda: self._chat_openrouter(messages, vision)),
            ]
        else:
            orden = [
                ("groq", lambda: self._chat_groq(messages, vision)),
                ("gemini", lambda: self._chat_gemini(messages, vision)),
                ("openrouter", lambda: self._chat_openrouter(messages, vision)),
            ]
        # Un proveedor sin clave no se prueba: se pierde hasta su timeout
        # golpeando una API a la que no tiene acceso.
        providers = [(n, f) for n, f in orden if _tiene_clave(n)]

        if not providers:
            log.error("No hay ninguna clave de modelo configurada.")
            return {"reply": "Señor, no hay ninguna clave de modelo puesta. "
                             "Metela en /setup y vuelvo a intentarlo.",
                    "provider": "none", "fallback": True, "sin_claves": True}

        # El sondeo se sigue haciendo, pero ya NO decide nada: solo se deja
        # anotado en el log para saber si el fallo es de red o de las claves.
        # Y va despues de montar la lista, no antes, para no retrasarla.

        # Se apunta quien fallo y por que, para que el mensaje de abajo diga
        # algo util en vez del mismo "no hay modelos" siempre.
        fallos: list[str] = []
        for name, fn in providers:
            # Con reloj: si ya no queda tiempo, se deja de probar. Antes se
            # acumulaban cuatro intentos y el señor se comia treinta
            # segundos de espera.
            if limite and time.time() - _t0 > limite:
                log.info("Se agotó el tiempo (%s) antes de probar %s", limite, name)
                break
            try:
                reply = fn()
                if reply:
                    self._provider = name
                    return {"reply": reply, "provider": name, "fallback": False}
                fallos.append(name)
            except Exception as e:
                fallos.append(f"{name}: {str(e)[:70]}")
                log.warning("Proveedor %s lanzó excepción: %s", name, e)

        # Un proveedor sin clave no es un fallo suyo: no se le culpa en el
        # mensaje, solo los que de verdad se esperaban a contestar.
        probados = [n for n, _ in providers]

        if limite and time.time() - _t0 > limite:
            # Se acabó el reloj. No se prueba ni el modelo local: aquí es donde
            # se perdían los últimos segundos.
            log.info("Reloj agotado (%ss) sin respuesta de proveedor", limite)
            return {"reply": "", "provider": "timeout", "fallback": True,
                    "agotado": True}

        reply = self._chat_ollama(messages)
        if reply:
            self._provider = "ollama"
            return {"reply": reply, "provider": "ollama", "fallback": True}

        if limite and time.time() - _t0 > limite:
            return {"reply": "", "provider": "timeout", "fallback": True,
                    "agotado": True}
        # El sondeo va aqui, y solo para el log: si dice que no hay red pero
        # los proveedores tenian clave, lo raro es la red; si los proveedores
        # fallaron con 401 o 429, lo raro son las claves. Sin esto el log
        # decía siempre lo mismo y no habia por donde tirar.
        red = _has_internet()
        log.error("Todos los proveedores fallaron. Probados=%s red=%s",
                  ", ".join(probados), "si" if red else "no")
        for f in fallos:
            log.error("   %s", f)

        # El mensaje dice que FALLO, no que no hay modelos. Antes decia "sin
        # modelos disponibles" tambien cuando las claves estaban bien y lo
        # que faltaba era un host que no contestaba, que es lo que confundia.
        return {"reply": _mensaje_sin_modelos(probados, self._errores, red),
                "provider": "none", "fallback": True,
                "fallos": fallos, "red": red,
                "errores": dict(self._errores)}


_internet_cache = {"t": 0.0, "v": False}
_INTERNET_TTL = 10.0


# "groq-light" y "groq" son el MISMO servicio con distinto modelo. En la lista
# de probados salían los dos como si fueran dos proveedores distintos, y el
# mensaje decía "los tres han fallado" con la palabra "tres" escrita a mano:
# con una sola clave puesta la frase era falsa de arriba abajo.
_PROVEEDOR_DE = {
    "groq-light": "Groq", "groq": "Groq",
    "gemini": "Gemini", "openrouter": "OpenRouter", "ollama": "Ollama",
}


def _mensaje_sin_modelos(probados: list, errores: dict, hay_red: bool) -> str:
    """Dice qué se intentó y POR QUÉ falló, no solo que falló.

    Se agrupan por servicio (Groq y groq-light son el mismo) y se cuenta con
    la lista real, no con un número escrito de memoria. El motivo sale de lo
    que registró el router: sin eso, lo único que se podía decir era "mire el
    registro del servidor", que no es un diagnóstico sino devolverle el
    problema al que ha preguntado.
    """
    nombres: list[str] = []
    for clave in probados:
        nombre = _PROVEEDOR_DE.get(clave, clave)
        if nombre not in nombres:
            nombres.append(nombre)

    if not nombres:
        return ("No he podido hablar con ningún modelo, señor. "
                "El detalle está en el registro del servidor.")

    # Un motivo por servicio: si se probaron dos modelos del mismo, vale el
    # último que falló.
    detalles = []
    for nombre in nombres:
        motivo = errores.get(nombre)
        if motivo:
            detalles.append(f"{nombre}: {motivo}")

    # "Groq, Gemini y OpenRouter": los nombres propios no llevan artículo, y la
    # enumeración lleva "y" antes del último. Con una frase así no hay que
    # contar nada de palabra, que es donde antes se mentía ("los tres" con una
    # sola clave puesta).
    def _lista(items: list) -> str:
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + " y " + items[-1]

    cabeza = (f"Mis disculpas, señor. {_lista(nombres)} "
              f"{'falló' if len(nombres) == 1 else 'fallaron'} "
              f"y no he podido obtener respuesta.")

    if detalles:
        cuerpo = cabeza + " " + "; ".join(detalles) + "."
    elif not hay_red:
        cuerpo = cabeza + " Además, desde aquí no hay salida a internet."
    else:
        cuerpo = cabeza + " El detalle está en el registro del servidor."

    # Una pista accionable, y solo una: la causa más común y la más fácil de
    # arreglar por su cuenta.
    if any("clave" in d for d in detalles):
        cuerpo += " Revise las claves en /setup."
    elif any("cuota" in d or "tráfico" in d for d in detalles):
        cuerpo += " Puede esperar un rato o revisar su cuota."
    elif any("alcanzar" in d for d in detalles):
        cuerpo += " Compruebe la conexión del servidor."
    return cuerpo

# Cualquier respuesta HTTP vale como "hay internet", included el error. Lo que
# dice que no hay red es que no se pueda ni resolver el nombre ni conectar.
#
# El host que se consulta NO es api.groq.com. Son dos cosas distintas: que no
# llegues a Groq no significa que no tengas internet, y en un NAS que el router
# bloquee ese dominio era justo lo que hacia que JARVIS se rindiera entero.
# generate_204 es el "ping" de verdad de internet, lo sirve cualquier CDN y no
# pertenece a ningun proveedor de modelos.
_SONDA_INTERNET = "https://www.gstatic.com/generate_204"


def _has_internet() -> bool:
    """Solo diagnostico. Ya NO decide que proveedores se prueban.

    Se deja por dos cosas: el aviso "(Modo limitado, sin conexión.)" que se
    pone delante de la respuesta, y el log cuando fallan todos. Para lo
    demas es un estorbo: son 2 s de espera maxima por lo que no se
    puede seguir adelante.
    """
    now = time.time()
    if now - _internet_cache["t"] <= _INTERNET_TTL:
        return _internet_cache["v"]
    ok = False
    try:
        req = urllib.request.Request(_SONDA_INTERNET, method="HEAD")
        with urllib.request.urlopen(req, timeout=2) as resp:
            ok = True
    except urllib.error.HTTPError:
        # Un 403 o un 404 tambien es respuesta: hay internet.
        ok = True
    except Exception:
        # DNS, certificado, timeout o sin salida: eso si es que no hay red.
        ok = False
    _internet_cache["t"] = now
    _internet_cache["v"] = ok
    return ok


def _probe_ollama(timeout: float = 1.5) -> bool:
    try:
        import urllib.parse
        u = urllib.parse.urlsplit(Config.OLLAMA_HOST)
        port = u.port or 11434
        # Fuerza 127.0.0.1 (IPv4) para no sufrir el tiempo de espera por ::1
        probe_url = f"{u.scheme}://127.0.0.1:{port}/api/tags"
        with urllib.request.urlopen(probe_url, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                return any(Config.FALLBACK_MODEL in (m.get("name") or "") for m in data.get("models", []))
        return False
    except Exception:
        return False


def _attach_vision(messages: list[dict], image_b64: str) -> list[dict]:
    user_msgs = [m for m in messages if m.get("role") != "system"]
    last = user_msgs[-1] if user_msgs else messages[-1]
    rest = user_msgs[:-1]
    content = [{"type": "text", "text": last["content"]},
               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]
    return rest + [{"role": last["role"], "content": content}]


def _vision_payload_openrouter(messages: list[dict], vision: str) -> dict:
    system_msgs = [m for m in messages if m.get("role") == "system"]
    new_messages = _attach_vision([m for m in messages if m.get("role") != "system"], vision)
    return {"model": Config.OPENROUTER_VISION_MODEL, "messages": system_msgs + new_messages}


def _load_image_for_gemini(b64: str):
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    return img
