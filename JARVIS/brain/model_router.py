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


class ModelRouter:
    def __init__(self):
        self._groq_client = None
        self._gemini = None
        self._openrouter = None
        self._provider = "groq"

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
    def _chat_groq(self, messages: list[dict], vision: Optional[str] = None,
                   model: Optional[str] = None) -> Optional[str]:
        # Si Groq acaba de decir que se le acab\u00f3 la cuota, no se vuelve a
        # llamar. Antes se probaba dos veces (el modelo peque\u00f1o y el grande),
        # las dos con el mismo problema, y se perd\u00edan hasta diez segundos
        # antes de caer en otro proveedor. Con este aviso se va all\u00ed de una.
        global _GROQ_COOLDOWN, _GROQ_PENALTY
        if _GROQ_COOLDOWN > time.time():
            return None
        client = self.groq()
        if not client:
            return None
        start = time.time()
        try:
            kwargs = {"model": model or Config.GROQ_MODEL_FAST,
                      "messages": messages, "temperature": 0.7}
            if vision:
                kwargs["model"] = Config.GROQ_MODEL_VISION
                kwargs["messages"] = _attach_vision(messages, vision)
            try:
                completion = client.chat.completions.create(**kwargs, timeout=_queda())
            except Exception as e1:
                # gpt-oss intenta function-calling nativo y Groq lo rechaza con
                # 400 ("Tool choice is none, but model called a tool"). Reintentar
                # con otro prompt no lo arregla: las herramientas no viajan en
                # kwargs, las pone el propio modelo, asi que la segunda llamada
                # falla igual y solo anade otro segundo de espera. Se avisa una
                # vez y se cae al siguiente proveedor, que es lo que queda bien.
                if "400" in str(e1) and "tool" in str(e1).lower():
                    log.warning("Groq: el modelo insists en llamar a una "
                        "herramienta; se pasa al siguiente proveedor.")
                    return None
                raise
            log.info("Groq OK en %.2fs", time.time() - start)
            # Si va bien se olvida el castigo anterior: un 429 viejo no debe
            # seguir mandando en la espera cuando Groq ya responde.
            if _GROQ_PENALTY:
                _GROQ_PENALTY = 0
            return completion.choices[0].message.content
        except Exception as e:
            # 429 por cuota (tokens por d\u00eda o por minuto): se apaga un rato
            # en vez de reintentar y perder el tiempo. El enfriamiento empieza
            # corto y sube solo si Groq sigue igual.
            if "429" in str(e) or "rate_limit" in str(e).lower() \
                    or "quota" in str(e).lower():
                _GROQ_PENALTY += 1
                espera = _groq_enfriado(_GROQ_PENALTY)
                _GROQ_COOLDOWN = time.time() + espera
                log.warning("Groq sin cuota: %d s de espera (intento %d).",
                            espera, _GROQ_PENALTY)
            else:
                # Un fallo que no es de cuota no demuestra nada sobre el
                # l\u00edmite, as\u00ed que no se penaliza: se prueba normal.
                _GROQ_COOLDOWN = 0.0
            log.warning("Groq fall\u00f3: %s", e)
            return None

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
            log.warning("Gemini falló: %s", e)
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
            log.warning("OpenRouter falló: %s", e)
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
        providers = []
        if _has_internet():
            if heavy:
                providers = [
                    ("gemini", lambda: self._chat_gemini(messages, vision)),
                    ("groq", lambda: self._chat_groq(messages, vision)),
                    ("openrouter", lambda: self._chat_openrouter(messages, vision)),
                ]
            elif light and not vision:
                providers = [
                    ("groq-light", lambda: self._chat_groq(messages, vision, Config.GROQ_MODEL_LIGHT)),
                    ("groq", lambda: self._chat_groq(messages, vision)),
                    ("gemini", lambda: self._chat_gemini(messages, vision)),
                    ("openrouter", lambda: self._chat_openrouter(messages, vision)),
                ]
            else:
                providers = [
                    ("groq", lambda: self._chat_groq(messages, vision)),
                    ("gemini", lambda: self._chat_gemini(messages, vision)),
                    ("openrouter", lambda: self._chat_openrouter(messages, vision)),
                ]
        else:
            log.warning("Sin internet → modo limitado local (Ollama).")

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
            except Exception as e:
                log.warning("Proveedor %s lanzó excepción: %s", name, e)

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
        log.error("Todos los proveedores fallaron, internet=%s", _has_internet())
        return {"reply": "Mis disculpas, señor. Sin modelos disponibles no puedo procesar eso.",
                "provider": "none", "fallback": True}


_internet_cache = {"t": 0.0, "v": False}
_INTERNET_TTL = 10.0


def _has_internet() -> bool:
    now = time.time()
    if now - _internet_cache["t"] <= _INTERNET_TTL:
        return _internet_cache["v"]
    ok = False
    try:
        with urllib.request.urlopen("https://api.groq.com", timeout=3) as resp:
            ok = True
    except urllib.error.HTTPError:
        # api.groq.com devuelve 403/404 al root: respuesta HTTP == hay internet.
        ok = True
    except Exception:
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
