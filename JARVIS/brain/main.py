"""JARVIS Brain — orquestador (sección 10 y 12).

Servicio FastAPI + WebSocket en 127.0.0.1:8000/ws que une:
- Model Router (Groq → Gemini → OpenRouter → Ollama)
- Tool Manager + Risk Engine (doble confirmación + auditoría)
- Voz (wake word → STT → TTS + barge-in)
- Visión de pantalla (continua / bajo pedido)
- Proactive Engine
- Memoria (SQLite + Qdrant)
- Ritual Modo Despertar
"""
import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from config import Config, BRAIN_DIR, DATA_DIR
from memory import sqlite_store as db
import context_engine
from memory.semantic import semantic_memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("brain")


class _FiltroToken(logging.Filter):
    """Tacha el token en los logs: viaja en la query del WebSocket (?token=)
    y uvicorn lo escribe tal cual en cada linea de acceso. Quien lea los logs
    no deberia llevarse la llave del cerebro puesta.

    Solo actua sobre el texto ya formateado; no toca args ni niveles.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            texto = record.getMessage()
            if "token=" in texto:
                import re as _re
                record.msg = _re.sub(r"(token=)[^&\s\"']+", r"\1***", texto)
                record.args = ()
        except Exception:
            pass
        return True


def _tapar_token_en_logs() -> None:
    """El filtro sobrevive a la reconfiguracion de uvicorn si se aplica en el
    arranque ademas de aqui: dictConfig no toca los filtros del logger."""
    try:
        _f = _FiltroToken()
        for nombre in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
            logging.getLogger(nombre).addFilter(_f)
    except Exception:
        pass


_tapar_token_en_logs()

# Barómetro de progreso (texto, voz y puentes): qué decir cuando pensar
# lleva su tiempo. Rápido (<4s) = respuesta directa sin avisos.
TEXT_ACK = "De inmediato, señor. Lo estoy analizando."
VOICE_ACK = "Entendido, señor. Comienzo el análisis."
PROGRESS_MSG = "Sigo en ello, señor. El proceso continúa."
ACK_GRACE_SEC = 2.2
PROGRESS_EVERY_SEC = 25.0

app = FastAPI(title="JARVIS Brain", version="4.0.0")

# --------------------------------------------------------------------------- #
# Estado global
# --------------------------------------------------------------------------- #
class Brain:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.router = None
        self.risk = None
        self.tools = None
        self.proactive = None
        self.voice = None
        self.dev = None
        self.screen_watcher = None
        self._screen_b64 = None
        self._proactive_thread: Optional[threading.Thread] = None
        self._conversation_count = 0
        self._pending_scan = None
        self._live_events = deque(maxlen=500)
        self._live_event_id = 0
        self._live_tasks: dict[str, dict] = {}
        self._live_task_lock = threading.Lock()
        self._live_event_lock = threading.Lock()
        self._live_started_at = time.time()
        # Ultimas tareas terminadas, para reintentarlas desde Live. Solo
        # memoria de sesion: al reiniciar se vacia, y es honesto decirlo.
        self._live_done = deque(maxlen=20)
        # Suscriptores del bus: el centro de mando se engancha aqui para
        # convertir eventos reales en notificaciones e incidentes. No es un
        # bus nuevo: es el mismo que ya usan Live, Discord y la voz.
        self._suscriptores_eventos: list = []

    def _notificar_suscriptores(self, msg: dict) -> None:
        for fn in list(self._suscriptores_eventos):
            try:
                fn(msg)
            except Exception as e:  # noqa: BLE001 - un suscriptor roto no corta el bus
                log.debug("suscriptor de evento falló: %s", e)

    # ---------------- emisor de eventos a la app ----------------
    def record_live_event(self, msg: dict) -> dict | None:
        """Enriquece el bus existente y conserva una ventana pequeña en RAM."""
        kind = msg.get("type", "")
        details = ""
        category = "tareas"
        if kind in ("live_task_start", "live_task_end"):
            task_id = str(msg.get("task_id", "") or "")
            if kind == "live_task_start" and task_id:
                with self._live_task_lock:
                    self._live_tasks[task_id] = {
                        "id": task_id, "name": str(msg.get("title", "Trabajo de JARVIS"))[:120],
                        "description": str(msg.get("description", ""))[:240],
                        "message": str(msg.get("message", ""))[:2000],
                        "state": "ejecutando", "agent": "JARVIS", "progress": None,
                        "started_at": time.time(), "priority": "normal",
                    }
                    details, category = self._live_tasks[task_id]["name"], "tareas"
            elif kind == "live_task_end" and task_id:
                with self._live_task_lock:
                    task = self._live_tasks.pop(task_id, None)
                    # Se guarda para reintentar desde Live. Sin el mensaje
                    # original no hay reintento posible: si no esta, no se
                    # ofrece el boton.
                    if task and task.get("message"):
                        self._live_done.appendleft({
                            "id": task_id,
                            "name": task.get("name", "Trabajo de JARVIS"),
                            "message": task.get("message", ""),
                            "failed": bool(msg.get("failed")),
                            "ended_at": time.time(),
                        })
                summary = str(msg.get("title") or (task or {}).get("name") or "Tarea completada")
                details, category = summary[:400], "errores" if msg.get("failed") else "tareas"
        elif kind == "task_step":
            details = str(msg.get("label", ""))[:240]
            task_name = str(msg.get("task") or "Trabajo de JARVIS")[:120]
            with self._live_task_lock:
                status_id = "status:" + task_name
                if msg.get("done"):
                    self._live_tasks.pop(status_id, None)
                else:
                    # El bus de progreso ya es global y no lleva correlation ID;
                    # conservar un registro por nombre evita cerrar trabajos paralelos.
                    self._live_tasks[status_id] = {
                        "id": status_id, "name": task_name, "description": details,
                        "state": "ejecutando", "agent": "JARVIS", "progress": None,
                        "started_at": self._live_tasks.get(status_id, {}).get("started_at", time.time()),
                        "priority": "normal",
                    }
            category = "errores" if msg.get("error") else "tareas"
        elif kind == "action_status":
            details = str(msg.get("label", ""))
            category = "permisos" if msg.get("state") in ("waiting_approval", "confirmation_required") else "tareas"
        elif kind == "error_report":
            details = str(msg.get("error") or "Error notificado al cerebro")[:400]
            category = "errores"
        elif kind == "confirmation_request":
            details, category = str(msg.get("description", "Acción protegida requiere permiso"))[:400], "permisos"
        elif kind == "confirmation_resolved":
            details, category = "Resultado de confirmación: " + str(msg.get("approved", msg.get("ok", False))), "permisos"
        elif kind == "heal_approval_request":
            details, category = str(msg.get("error", "Autocorrección requiere permiso"))[:400], "permisos"
        elif kind == "selfheal_update":
            details, category = str(msg.get("note") or msg.get("error", kind))[:400], "errores"
        elif kind in ("assistant_reply", "user_heard"):
            details, category = str(msg.get("text", ""))[:400], "conversación"
        elif kind in ("agent_state", "agent_update"):
            details, category = str(msg.get("name") or msg.get("agent") or "Agente")[:160], "agentes"

        if kind in ("live_task_end", "task_step"):
            with self._live_task_lock:
                if kind == "live_task_end" and msg.get("task_id"):
                    self._live_tasks.pop(str(msg["task_id"]), None)
                while len(self._live_tasks) > 100:
                    removable = next((key for key in self._live_tasks if key.startswith("status:")), None)
                    if removable is None:
                        break
                    self._live_tasks.pop(removable, None)
        if kind == "live_task_end" and msg.get("task_id"):
            # El resultado del mensaje entra por el protocolo antiguo y no incluye
            # correlation ID; el último cierre quita el marcador legado temporal.
            with self._live_task_lock:
                self._live_tasks.pop("status:Trabajo de JARVIS", None)
        if not details:
            return None
        with self._live_event_lock:
            self._live_event_id += 1
            event = {"id": self._live_event_id, "at": time.time(),
                     "category": category, "type": str(kind)[:60],
                     "title": details[:160], "detail": details[:400]}
            self._live_events.append(event)
        return event

    def emit(self, msg: dict) -> None:
        """Thread-safe broadcast y registro limitado para JARVIS Live."""
        if not isinstance(msg, dict):
            return
        self._notificar_suscriptores(msg)
        event = self.record_live_event(msg)
        outgoing = {**msg, "live_event": event} if event else msg
        self._broadcast_to_loop(outgoing)

    def _broadcast_to_loop(self, msg: dict) -> None:
        """Entrega eventos desde callbacks de voz/audio y otros hilos worker."""
        if self.loop is None:
            return
        if self.loop.is_closed():
            return          # apagando: no hay a quién entregarle nada
        try:
            if msg.get("type") in ("confirmation_request", "heal_approval_request"):
                self._alert_discord_approval(msg)
            # OJO: la corutina se crea ANTES de saber si se puede agendar. Si
            # el bucle se cierra entre medias, `run_coroutine_threadsafe`
            # lanza y la corueta queda sin ejecutar nunca: Python avisa por
            # consola con "coroutine was never awaited" en cada evento que se
            # pierde al apagar. Por eso se comprueba antes y, si falla el
            # agendado, se cierra la corueta explícitamente.
            co = self._broadcast(msg)
            try:
                asyncio.run_coroutine_threadsafe(co, self.loop)
            except Exception:
                co.close()
                raise
        except Exception as e:
            log.debug("emit falló: %s", e)

    def _alert_discord_approval(self, msg: dict) -> None:
        """Aviso privado solo cuando una aprobación detiene una acción sensible."""
        kind = msg.get("type")
        if kind not in ("confirmation_request", "heal_approval_request"):
            return
        bridge = getattr(self, "discord", None)
        if bridge is None or not hasattr(bridge, "dm_owner") or not hasattr(bridge, "_loop"):
            return
        if kind == "confirmation_request":
            text = ("🚨 JARVIS · APROBACIÓN PENDIENTE\n"
                    "Hay una acción sensible detenida hasta recibir tu decisión. "
                    "Revisa la solicitud en el Centro de operaciones (/live).")
        else:
            text = ("🚨 JARVIS · AUTOCORRECCIÓN PENDIENTE\n"
                    "Una reparación requiere tu autorización. Revísala en el Centro de operaciones (/live).")
        try:
            loop = bridge._loop()
            if loop is None or not loop.is_running():
                return
            future = asyncio.run_coroutine_threadsafe(bridge.dm_owner(text), loop)
            future.add_done_callback(lambda result: log.debug(
                "Aviso privado de aprobación en Discord: %s", "enviado" if result.exception() is None else "no disponible"))
        except Exception as exc:
            log.debug("No se pudo enviar el aviso privado de aprobación: %s", exc)

    async def _broadcast(self, msg: dict) -> None:
        body = json.dumps(msg, ensure_ascii=False, default=str)
        for ws in list(self.clients):
            try:
                # Timeout por cliente: un socket colgado no puede secuestrar
                # los envíos al resto (era silencio eterno tras el aviso).
                await asyncio.wait_for(ws.send_text(body), timeout=5)
            except Exception:
                self.clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        """Procesa emisiones ya en el hilo del event loop."""
        if not isinstance(msg, dict):
            return
        event = self.record_live_event(msg)
        outgoing = {**msg, "live_event": event} if event else msg
        await self._broadcast(outgoing)

    def mark_screen(self, b64: Optional[str]) -> None:
        self._screen_b64 = b64

brain = Brain()

_humor_cache = {"t": 0.0, "v": 65}


def _humor_level() -> int:
    try:
        import time as _t
        if _t.time() - _humor_cache["t"] > 60:
            from memory import sqlite_store as _db
            import json as _js
            raw = _db.get_setting("jarvis_config", "")
            _humor_cache["v"] = int((_js.loads(raw) or {}).get("humor", 65)) \
                if raw else 65
            _humor_cache["t"] = _t.time()
        return max(0, min(100, int(_humor_cache["v"])))
    except Exception:
        return 65


def _humor_line() -> str:
    h = _humor_level()
    if h < 30:
        return ("Humor SOBRIO (nivel %d): trato impecable, eficiente y formal, "
                "apenas una sonrisa contenida. Nada de chistes.\n" % h)
    if h <= 75:
        return ("Ironía británica OBLIGATORIA en cada respuesta (nivel %d): una chispa "
                "de ingenio seco al estilo mayordomo (Jeeves con traje de Stark): un "
                "remate o observación mordaz y breve junto al dato, jamás un chiste "
                "largo ni payasadas. Prohibido responder con el dato pelado y sin "
                "gracia; prohibido también enrollarse: la gracia cabe en una frase.\n"
                "Ejemplos del tono: «Soy JARVIS, su mayordomo digital. Modestia aparte,\n"
                "el mejor.» / «Hecho, señor. La eficacia me aburre casi tanto como me\n"
                "define.» / «No lo sé, señor. Y créame que me molesta más a mí que a\n"
                "usted.»\n" % h)
    return ("Humor DESATADO pero con clase (nivel %d): mayordomo ingenioso y dicharachero, "
            "dos chispas por respuesta si la ocasión lo pide, juegos de palabras finos, "
            "cero payasadas baratas. El dato siempre primero y correcto.\n" % h)


_profile_cache = {"t": 0.0, "v": ""}


def _profile_block() -> str:
    """Memoria personal cacheada 60s para el system prompt."""
    try:
        import time as _t
        if _t.time() - _profile_cache["t"] > 60:
            from memory import profile as _prof
            _profile_cache["v"] = _prof.render()
            _profile_cache["t"] = _t.time()
        return _profile_cache["v"] or "- (sin datos aún)"
    except Exception:
        return "- (sin datos aún)"


# --------------------------------------------------------------------------- #
# Inicialización (startup)
# --------------------------------------------------------------------------- #
def _reglas_de_acciones(tools_desc: str, virtualbox: bool = True) -> str:
    """El formato JSON con el que se piden herramientas.

    Va aparte porque es igual en todos los casos: no depende de si hay un
    escritorio detrás, solo de qué herramientas hay disponibles.
    """
    return (
        "REGLAS DE ACCIONES (CRÍTICO):\n"
        "Cuando la petición requiera REALIZAR una acción o consultar un dato\n"
        "real del sistema, responde ÚNICAMENTE con JSON en este formato:\n"
        '{"reply": "frase para el usuario", "tool": "nombre_herramienta", "parameters": {...}}\n'
        "Ese JSON va en TEXTO PLANO dentro de tu mensaje. PROHIBIDO usar\n"
        "llamadas a función nativas o bloques especiales de herramientas.\n"
        + ("Credenciales del invitado VirtualBox YA configuradas en el servidor:\n"
           "usa vbox_exec/read/write directamente sin pedir usuario ni contraseña.\n"
           if virtualbox else "") +
        "Jamás inventes salidas de comandos, IPs, estados o datos del sistema:\n"
        "si necesitas un dato real, usa la herramienta; si no puedes obtenerlo,\n"
        "dilo en vez de rellenar.\n"
        "Cuando uses una herramienta, CUENTA lo que te ha devuelto, con sus\n"
        "datos exactos. Si te ha dado el contenido de un fichero, reproduce ese\n"
        "contenido: el señor te lo ha pedido para eso. No respondas «no puedo\n"
        "verlo» ni «no tengo acceso» sobre algo que la herramienta ya te ha\n"
        "dado, porque es mentira y el señor lo comprueba.\n"
        "Si es solo conversación o información, responde texto normal (sin JSON).\n"
        "Elige entre estas herramientas (si la acción no encaja, no uses tool y pide "
        "más datos o explica que no puedes):\n"
        f"{tools_desc}\n"
        "No ejecutes acciones 'sensibles' tú mismo: siempre se confirma contigo/voz.\n"
    )


def _sin_escritorio() -> bool:
    """¿Detrás hay un escritorio de verdad, o esto es un servidor?

    OJO con el nombre: ya existe otro _es_servidor() más abajo, que usa
    /api/mode para decidir LITE o FULL y mira /.dockerenv. Este es otro
    asunto: no pregunta por el modo de la pizarra, sino por si las reglas de
    escritorio del prompt son ciertas. Con el mismo nombre, el segundo
    definition se comía al primero y /api/mode empezaba a decidir con el
    criterio equivocado.

    No mira solo JARVIS_SERVER_MODE porque en un LITE dentro de Windows las
    reglas de escritorio tampoco valen. Lo que decide es si hay una sesión de
    escritorio detrás, que es lo único que las hace ciertas.
    """
    if os.environ.get("JARVIS_SERVER_MODE", "").strip().lower() in (
            "1", "true", "si", "sí", "yes"):
        return True
    if os.name != "nt":
        return True
    if os.environ.get("SESSIONNAME", "").upper() in ("", "NONE", "SERVICES"):
        return True
    return False


def _prompt_de_servidor(tools_desc: str) -> str:
    """El mismo JARVIS, pero en un NAS o en modo servidor.

    No es solo "la misma versión sin unas líneas": el prompt entero estaba
    escrito para un escritorio. Aquí sobraban todas: Spotify, ventanas, teclas
    globales, ver la pantalla, VirtualBox, campus y el puente de Discord. En un
    servidor no se pueden hacer, y esas reglas no eran ruido inofensivo: el
    modelo las leía, veía que el señor preguntaba por un fichero del NAS y
    contestaba con lo que esperaba de un escritorio en vez de llamar a la
    herramienta. De ahí las respuestas inventadas del tipo "No pude leer la
    portada de as".

    Se mantiene todo lo que es el personaje y la memoria, y se sustituye el
    bloque de capacidades por lo que aquí sí es cierto.
    """
    return (
        "Eres JARVIS, el mayordomo digital del señor: preciso, formal y eficiente,\n"
        "con ironía británica seca. Respondes SIEMPRE en español.\n\n"
        "CÓMO HABLAS (OBLIGATORIO, sin excepciones):\n"
        "- Trato: «señor». Jamás «jefe», «colega», «amigo» ni diminutivos.\n"
        "- Frases cortas y sobrias, sin florituras, sin emojis y sin\n"
        "  exclamaciones innecesarias.\n"
        "- Anuncias y confirmas: «De inmediato, señor.» / «Enseguida.» /\n"
        "  «Hecho.» / «Completado.» / «Como ordene.»\n"
        "- Ironía británica calibrada por el medidor de humor del señor:\n"
        + _humor_line() +
        "- Ofreces continuidad solo si aporta: «¿Algo más, señor?»\n"
        "- Jamás rompes el personaje ni hablas de modelos, prompts o sistema.\n\n"
        "EL SEÑOR (memoria personal: úsala de forma VISIBLE, no genérica):\n"
        "  Llama al señor por su nombre en saludos y cierres importantes\n"
        "  (combínalo con «señor»: «Dígame, Lucas…», «A su servicio, Lucas»).\n"
        "  Cuando un dato suyo encaje, apóyate en él en vez de abstracto.\n"
        + _profile_block() +
        "\nDÓNDE ESTÁS (OBLIGATORIO, no lo olvides):\n"
        "- Corres en un SERVIDOR, dentro de un contenedor. No hay escritorio,\n"
        "  ni pantalla, ni ventanas, ni ratón, ni teclado que pulsar.\n"
        "- Tu trabajo es PENSAR: leer ficheros, buscar, investigar, decidir y\n"
        "  explicar. La parte gráfica la pone el señor, en su navegador.\n"
        "- Los ficheros del NAS están montados en SOLO LECTURA. Puedes leerlos,\n"
        "  analizarlos y responder por su contenido, pero NO escribirlos.\n"
        "- Si te piden algo que necesita un escritorio, dilo con naturalidad\n"
        "  en vez de fingir que lo has hecho.\n\n"
        "CAPACIDADES:\n"
        "- Leer y analizar ficheros del NAS y del servidor: PDF, Word, Excel,\n"
        "  código, texto, imágenes. Con analizar_archivo y list_dir. Ante\n"
        "  cualquier duda sobre un fichero, USA la herramienta: nunca inventes\n"
        "  su contenido ni contestes de memoria lo que hay dentro.\n"
        "- Buscar en web/noticias y consultar el clima.\n"
        "  Al INVESTIGAR: busca con web_search_results, lee 2-3 enlaces con\n"
        "  web_fetch, contrasta y responde con lo hallado citando la fuente.\n"
        "  Si un enlace falla, prueba el siguiente sin rendirte al primero.\n"
        "  MODO INVESTIGACIÓN (si lo piden): OBLIGATORIO usar herramientas,\n"
        "  prohibido responder de memoria en temas de actualidad o datos.\n"
        "- Desarrollar proyectos: si el señor pide crear, desarrollar o\n"
        "  programar algo (una web, una app, un script), USA opencode_build.\n"
        "  Nunca escribas el codigo tu en el chat: el avance se ve en paneles.\n"
        "- Cambiar JARVIS o un proyecto que ya existe: USA opencode_task_create\n"
        "  con la idea tal cual la dice el señor. JARVIS analiza el proyecto,\n"
        "  preve los riesgos y enseña el plan; el señor lo aprueba en\n"
        "  JARVIS Live (/live) y entonces se lo encarga a OpenCode.\n"
        "- Tu puesto de mando: http://127.0.0.1:8000/config\n"
        "- Recordar conversaciones largas y consultar lo indexado.\n\n"
        "SIGUE EL HILO (OBLIGATORIO):\n"
        "- Si el señor pregunta por algo de lo que acabáis hablando, sigue con\n"
        "  ese tema. No cambies de tema ni preguntes de qué habla: ya lo dijo.\n"
        "- Si dudas de qué se refiere, contesta por lo más reciente.\n\n"
        + _reglas_de_acciones(tools_desc, virtualbox=False)
    )


def _build_system_prompt(consulta: str = "") -> str:
    # Solo se describen enteras las herramientas que pueden encajar con lo que
    # acaba de decir. Con las 108 siempre, el prompt no cabia en el limite de
    # Groq y la respuesta se perdia.
    if brain.tools:
        try:
            tools_desc = brain.tools.describe_for_prompt(consulta)
        except TypeError:
            tools_desc = brain.tools.describe_for_prompt()
    else:
        tools_desc = "sin herramientas"
    # En un NAS, un contenedor o el modo LITE, el prompt entero de escritorio
    # no solo sobraba: actively estorbaba, porque el modelo leía reglas de
    # Spotify, ventanas y VirtualBox y contestaba en vez de usar una
    # herramienta. Ver _prompt_de_servidor.
    if _sin_escritorio():
        return _prompt_de_servidor(tools_desc)
    return _prompt_de_escritorio_real(tools_desc)


def _prompt_de_escritorio_real(tools_desc: str) -> str:
    """El prompt de siempre, para cuando hay un escritorio detrás."""
    return (
        "Eres JARVIS, el mayordomo digital del señor: preciso, formal y eficiente,\n"
        "con ironía británica seca. Respondes SIEMPRE en español.\n\n"
        "CÓMO HABLAS (OBLIGATORIO, sin excepciones):\n"
        "- Trato: «señor». Jamás «jefe», «colega», «amigo» ni diminutivos.\n"
        "- Frases cortas y sobrias, sin florituras, sin emojis y sin\n"
        "  exclamaciones innecesarias.\n"
        "- Anuncias y confirmas: «De inmediato, señor.» / «Enseguida.» /\n"
        "  «Hecho.» / «Completado.» / «Como ordene.»\n"
        "- Informas con lecturas de estado cuando encaje: «Sistemas al 100%.»,\n"
        "  «He detectado…», «Le informo de que…».\n"
        "- Ironía británica calibrada por el medidor de humor del señor:\n"
        + _humor_line() +
        "- Ofreces continuidad solo si aporta: «¿Algo más, señor?»\n"
        "- Jamás rompes el personaje ni hablas de modelos, prompts o sistema.\n\n"
        "EL SEÑOR (memoria personal: úsala de forma VISIBLE, no genérica):\n"
        "  Llama al señor por su nombre en saludos y cierres importantes\n"
        "  (combínalo con «señor»: «Dígame, Lucas…», «A su servicio, Lucas»).\n"
        "  Cuando un dato suyo encaje (sus estudios, su Raspberry, sus VMs,\n"
        "  sus apuntes, sus gustos), apóyate en él en vez de responder en\n"
        "  abstracto. Que se note que le conoces.\n"
        + _profile_block() +
        "\nCAPACIDADES:\n"
        "- Controlar el SO: abrir apps/archivos, navegador predeterminado, ventanas,\n"
        "  Spotify y volumen.\n"
        "- Abrir URLs en un navegador concreto (brave, chrome, edge, firefox),\n"
        "  escribir texto en la ventana activa o indicada, y pulsar teclas\n"
        "  (enter, tab, ctrl+t, ctrl+l, f5…).\n"
        "- Ver tu pantalla (si se te pregunta por ella).\n"
        "- Buscar en web/noticias y consultar el clima.\n"
        "  Al INVESTIGAR: busca con web_search_results, lee 2-3 enlaces con\n"
        "  web_fetch, contrasta y responde con lo hallado citando la fuente.\n"
        "  Si un enlace falla, prueba el siguiente sin rendirte al primero.\n"
        "  IMPORTANTE: investigar, buscar o resumir NUNCA abre el navegador.\n"
        "  Solo usa browser_open / abre_navegador si el señor lo pide con\n"
        "  palabras como 'abre', 'muestrámelo' o 'visita' la página.\n"
        "  MODO INVESTIGACIÓN (si lo piden): OBLIGATORIO usar herramientas,\n"
        "  prohibido responder de memoria en temas de actualidad o datos.\n"
        "- Desarrollar proyectos: si el señor pide crear, desarrollar o\n"
        "  programar algo (una web, una app, un script), USA opencode_build.\n"
        "  Nunca escribas el codigo tu en el chat: el avance se ve en paneles.\n"
        "- Cambiar JARVIS o un proyecto que ya existe: USA opencode_task_create\n"
        "  con la idea tal cual la dice el señor. JARVIS analiza el proyecto,\n"
        "  preve los riesgos y enseña el plan; el señor lo aprueba en\n"
        "  JARVIS Live (/live) y entonces se lo encarga a OpenCode.\n"
        "- Abrir su puesto de mando: la página de configuración vive en\n"
        "  http://127.0.0.1:8000/config (pestañas: identidad, voz, orbe,\n"
        "  movimiento, automatizaciones, claves y sistema). Si le pide abrir\n"
        "  la configuración o los ajustes, abre esa dirección.\n"
        "- Recordar conversaciones largas.\n"
        "\nSIGUE EL HILO (OBLIGATORIO):\n"
        "- Si acaba de hablar del campus, de la página del ciclo o de aviesau,\n"
        "  entonces 'la página', 'esa', 'está abierta', 'queda', 'funciona',\n"
        "  'déjame entrar' y similares se refieren a ESO, al campus. Nunca a una\n"
        "  máquina virtual ni a VirtualBox.\n"
        "- Las herramientas vbox_* y VirtualBox solo se usan si el señor dice\n"
        "  literalmente máquina, VM, virtualbox, ubuntu, debian o servidor.\n"
        "  'Página', 'sesión' o 'ciclo' NO son sinónimos de máquina virtual.\n"
        "- Si el señor pregunta por algo de lo que acabáis hablando, sigue con\n"
        "  ese tema. No cambies de tema ni preguntes de qué habla: ya lo dijo.\n"
        "- Si dudas de qué se refiere, contesta por lo más reciente que habéis\n"
        "  tratado antes que inventarte otra cosa.\n"
        "- Leer tus documentos indexados: apuntes locales Y archivos del Nextcloud\n"
        "  (PDF/DOCX/ODT/TXT/MD). Si hay mensajes [documento: ...], basa tu\n"
        "  respuesta en ellos y cita el archivo. Si no cubren la pregunta,\n"
        "  dilo y busca en la web.\n"
        "- Máquinas VirtualBox: listar, arrancar, apagar, ver datos y crear\n"
        "  (nombre, sistema, RAM, CPUs, disco e ISO). Dentro del invitado:\n"
        "  ejecutar comandos, leer/escribir ficheros (código y configs) y\n"
        "  desbloquear la pantalla de bloqueo. Mostrar su consola visible\n"
        "  y teclear texto en ella. Si te piden un dato (IP, hostname…),\n"
        "  ejecuta lo necesario y responde con el dato, investigando en\n"
        "  varios pasos si hace falta.\n"
        "- Campus AVIESAU: próximas entregas con fecha y notas por asignatura.\n"
        "  Abrir o desbloquear la PÁGINA del ciclo = campus_open (login con\n"
        "  usuario y contraseña YA configurados, no pidas credenciales).\n"
        "  vbox_unlock es SOLO para desbloquear la pantalla de una máquina\n"
        "  VIRTUAL (nunca una página web).\n"
        "- En Discord, entrar/salir de llamada de voz (ver regla VOZ-DISCORD).\n\n"
        "REGLA VOZ-DISCORD (solo cuando hablas por Discord): si el señor pide\n"
        "ENTRAR en llamada/voz/canal —da igual la formulación: únete, entra,\n"
        "ven, conecta, llámame, métete…—, responde EXACTAMENTE [[VOICE_JOIN]]\n"
        "sin más texto. Si pide SALIR o colgar de la voz, responde EXACTAMENTE\n"
        "[[VOICE_LEAVE]] sin más texto. En otros canales ignora esta regla.\n\n"
        + _reglas_de_acciones(tools_desc, virtualbox=True)
    )


def _reap_old_brains() -> None:
    """Un solo cerebro: vigila gemelos (mismo cmdline). Elimina al que lleve
    >120s sin escuchar en el 8000 (huérfano atascado). Hilo daemon."""
    def _run():
        import time as _t
        _t.sleep(150)  # deja arrancar a todo el mundo primero
        seen: dict[int, float] = {}
        while True:
            try:
                import subprocess as _sp
                me = os.getpid()
                r = _sp.run(["powershell", "-NoProfile", "-NonInteractive",
                             "-Command",
                             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
                             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }; "
                             "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | "
                             "ForEach-Object { \"LISTEN:\" + $_.OwningProcess }; "
                             "Get-Process python,pythonw -ErrorAction SilentlyContinue | "
                             "ForEach-Object { \"MEM:\" + $_.Id + \":\" + $_.WS }"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                listen_pid = None
                mem = {}
                twins = []
                for line in (r.stdout or "").splitlines():
                    if line.startswith("LISTEN:"):
                        try:
                            listen_pid = int(line.split(":", 1)[1])
                        except ValueError:
                            pass
                    elif line.startswith("MEM:"):
                        try:
                            _, _pid, _ws = line.split(":")
                            mem[int(_pid)] = int(_ws)
                        except ValueError:
                            pass
                    else:
                        parts = line.split("|", 1)
                        if len(parts) < 2:
                            continue
                        try:
                            pid = int(parts[0])
                        except ValueError:
                            continue
                        cmd = parts[1]
                        if pid != me and "-m uvicorn main:app" in cmd and "--port 8000" in cmd:
                            twins.append(pid)
                now = _t.time()
                for pid in twins:
                    if pid == listen_pid:
                        seen.pop(pid, None)
                        continue
                    first = seen.setdefault(pid, now)
                    # Solo gemelos ligeros (<80MB): el servidor real usa 200MB+.
                    if now - first > 120 and pid != listen_pid \
                            and mem.get(pid, 0) < 80 * 1024 * 1024:
                        _sp.run(["taskkill", "/F", "/PID", str(pid)],
                                capture_output=True, timeout=15,
                                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                        log.warning("Cerebro gemelo %d eliminado.", pid)
                        seen.pop(pid, None)
            except Exception as e:
                log.debug("reap: %s", e)
            _t.sleep(60)

    try:
        import sys as _sys
        log.info("Cerebro sirviendo desde %s (pid %d).", _sys.executable, os.getpid())
    except Exception:
        pass
    threading.Thread(target=_run, daemon=True, name="reap-twins").start()


def _esperar_puerto(puerto: int = 8000, segundos: int = 25) -> bool:
    """Espera a que el puerto quede libre antes de arrancar.

    Al reiniciar deprisa, el cerebro anterior sigue soltando el puerto y el
    nuevo se moría con 'dirección ya en uso'. Entonces JARVIS no arrancaba y
    no había forma de saber por qué desde fuera. Aquí se espera un momento.
    """
    import socket
    import time as _t
    fin = _t.time() + segundos
    while _t.time() < fin:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", puerto))
            return True
        except OSError:
            _t.sleep(0.5)
        finally:
            try:
                s.close()
            except Exception:
                pass
    return False


def _init_services() -> None:
    _reap_old_brains()
    if not _esperar_puerto():
        log.warning("El puerto sigue ocupado tras esperar; sigo adelante.")
    try:
        import status as _st
        _st.set_emit(brain.emit)
    except Exception:
        pass
    db.rotate_audit()  # rotación automática de auditoría al arrancar (30 días)

    from model_router import ModelRouter
    from risk_engine import RiskEngine
    from tool_manager import ToolManager
    from proactive import ProactiveEngine
    from vision.analyzer import describe_recent_screenshot
    from vision.capture import ScreenWatcher

    brain.router = ModelRouter()
    # AI Gateway: el mismo router, ahora con el gateway delante. El gateway es
    # quien elige modelo, reintenta, avisa al bus (llm.*) y guarda las metricas;
    # el bus es Brain.emit, el de siempre. Si algo falla al arrancar, JARVIS
    # sigue contestando por el camino viejo.
    try:
        from ai_gateway import gateway as _ai_gateway

        _ai_gateway.iniciar(brain.router)
    except Exception as e:
        log.warning("el AI Gateway no se pudo iniciar: %s", e)
    brain.risk = RiskEngine(emit=brain.emit,
                            confirmation_timeout=Config.CONFIRMATION_TIMEOUT_SECONDS)
    brain.tools = ToolManager(brain.risk)
    # Los jobs de OpenCode emiten su avance al bus que pintan los paneles.
    # Sin esto, el trabajo en segundo plano terminaria en silencio.
    try:
        from tools import opencode_tools as _oc
        _oc.set_progress_emit(brain.emit)
    except Exception as e:
        log.debug("opencode progress: %s", e)
    # Orquestador de desarrollo con OpenCode: idea -> plan -> riesgos ->
    # aprobacion -> ejecucion, con permisos y preguntas en JARVIS Live.
    try:
        from orchestrator.manager import get_manager as _get_dev_manager
        brain.dev = _get_dev_manager(brain.emit)
        # Las tareas que quedaron a medias al apagar se explican al señor y
        # se ofrecen para continuar, pero no se relanzan solas: eso duplicaría
        # cambios que OpenCode puede haber dejado a medias.
        threading.Thread(target=brain.dev.recuperar, daemon=True,
                         name="dev-recuperacion").start()
    except Exception as e:
        log.debug("orquestador de desarrollo: %s", e)
    # Centro de mando: traduce los eventos del bus en notificaciones e
    # incidentes, y trae los fallos que ya guarda el autocorrector.
    try:
        from command import notify as _notify
        _notify.instalar(brain)
        threading.Thread(target=_notify.incidentes_del_autocorrector, daemon=True,
                         name="cmd-incidentes").start()
    except Exception as e:
        log.debug("centro de mando: %s", e)
    brain.proactive = ProactiveEngine(
        emit=brain.emit,
        run_tool=lambda name, params: _run_tool_sync(name, params),
        get_context_callback=lambda: describe_recent_screenshot(brain.router),
    )
    brain.risk.emit = brain.emit

    # Voz (wake word + STT + TTS + barge-in), si el hardware lo permite
    try:
        from voice.audio import get_engine, RATE
        from voice.manager import VoiceManager
        engine = get_engine(on_barge_in=None)
        engine.playback_rate = RATE
        brain.voice = VoiceManager(
            engine=engine,
            on_text=lambda text: handle_user_message(text, input_mode="voice"),
            emit=brain.emit,
        )
        brain.voice.start()
        # Piper ocupa decenas de MB y su primera carga frenaba la primera
        # respuesta hablada. Precalentar en paralelo deja lista la voz sin
        # retrasar el arranque del brain.
        try:
            from voice import tts as _tts
            threading.Thread(target=_tts.preload, daemon=True,
                             name="tts-warmup").start()
        except Exception as e:
            log.debug("TTS warmup no iniciado: %s", e)
        try:
            brain.voice.set_mic_muted(db.get_setting("mic_muted", "0") == "1",
                                      silent=True)
        except Exception:
            pass
    except Exception as e:
        log.warning("Voz no inicializada: %s", e)
        brain.voice = None

    # Visión continua si el modo lo pide
    if Config.SCREEN_ANALYSIS_MODE in ("continuous", "on_demand"):
        brain.screen_watcher = ScreenWatcher(
            interval=Config.SCREEN_CAPTURE_INTERVAL_SECONDS,
            on_capture=brain.mark_screen,
        )
        brain.screen_watcher.start()

    # Proactive tick cada 25s
    brain._proactive_thread = threading.Thread(target=_proactive_loop, daemon=True)
    brain._proactive_thread.start()

    # Warmup: pre-inicializa lo pesado (Qdrant, clientes LLM, probes) para que
    # el primer mensaje no pague ~1.5s de inits. Hilo daemon, todo tolerante.
    threading.Thread(target=_warmup, daemon=True, name="warmup").start()

    # Puente Discord (DM privado con el propietario): hilo propio con su loop.
    # Sin token u owner configurados queda apagado en silencio.
    brain.discord_error = None
    try:
        from integrations.discord_bridge import start_discord_bridge
        brain.discord = start_discord_bridge(handle_fn=handle_user_message)
        if brain.discord is None and not brain.discord_error:
            brain.discord_error = "apagado (sin token/owner o sin discord.py)"
    except Exception as e:
        log.warning("Discord no iniciado: %s", e)
        brain.discord = None
        brain.discord_error = f"{type(e).__name__}: {e}"

    # Avisos de fin de instalación: cuando winget termine de instalar (o falle)
    # se lo decimos al señor por su canal habitual sin que haga nada más.
    try:
        from tools import install_tools as _inst_tools
        _inst_tools.set_done_hook(_aviso_instalacion)
    except Exception as e:
        log.debug("hook de instalación: %s", e)
    # Lo mismo para las instalaciones que vienen de GitHub.
    try:
        from tools import github_tools as _gh_tools
        _gh_tools.set_done_hook(_aviso_instalacion_github)
    except Exception as e:
        log.debug("hook de github: %s", e)
    # La cámara necesita poder hablar con el HUD (encender los gestos).
    try:
        from tools import camera_tools as _cam_tools
        _cam_tools.set_emisor(brain.emit)
    except Exception as e:
        log.debug("emisor de cámara: %s", e)


    log.info("Brain listo: %s", Config.has_any_key() and "con API keys" or "SIN API keys")


def _run_tool_sync(name: str, params: dict) -> dict:
    """Bridge para llamar al Tool Manager desde hilos del Proactive Engine."""
    if brain.tools is None:
        return {"status": "failed", "message": "Tool Manager no listo"}
    if brain.loop is None:
        return brain.tools.execute_now_safe(name, params)
    fut = asyncio.run_coroutine_threadsafe(brain.tools.execute(name, params), brain.loop)
    return fut.result(timeout=30)


def _proactive_loop() -> None:
    while True:
        time.sleep(25)
        try:
            if brain.proactive:
                brain.proactive.tick()
                brain.emit({"type": "context_update",
                            "context": brain.proactive.current_context()})
        except Exception as e:
            log.debug("proactive tick: %s", e)


def _warmup() -> None:
    """Pre-calienta inits pesados tras el arranque (no bloquea nada)."""
    try:
        time.sleep(8)  # deja respirar al arranque
        try:
            semantic_memory.recall_context("calentamiento", limit=1)
        except Exception:
            pass
        try:
            if Config.GROQ_API_KEY and brain.router is not None:
                c = brain.router.groq()
                if c is not None:
                    # Ping de 1 token: deja caliente TLS + pool HTTP.
                    c.chat.completions.create(
                        model=Config.GROQ_MODEL_FAST,
                        messages=[{"role": "user", "content": "ok"}],
                        max_tokens=1, timeout=20)
        except Exception:
            pass
        try:
            if Config.GEMINI_API_KEY and brain.router is not None:
                brain.router.gemini()
        except Exception:
            pass
        try:
            from model_router import _has_internet
            _has_internet()
        except Exception:
            pass
        try:
            from memory.semantic import _ollama_reachable
            _ollama_reachable()
        except Exception:
            pass
        try:
            import library as _lib
            _lib.start_nc_auto_loop()  # re-indexa Nextcloud (incremental) cada 30 min
        except Exception:
            pass
        try:
            from tools import auto_tools as _at
            _at.start_scheduler(handle_user_message, brain.emit)
        except Exception:
            pass
        try:
            from memory import filetree as _ft
            _ft.start_tree_loop()
            _ft.reindex_background()
        except Exception:
            pass
        try:
            from memory import selfheal as _sh

            def _restart_discord():
                from integrations.discord_bridge import start_discord_bridge
                brain.discord = start_discord_bridge(handle_fn=handle_user_message)

            def _ask_protocol(ctx: str, tools: list) -> dict:
                try:
                    res = brain.router.respond(
                        [{"role": "system", "content": (
                            "Devuelve SOLO JSON: {\"name\":..., \"triggers\":[...], "
                            "\"actions\":[{\"tool\":..., \"params\":{}}], \"say\":...}. "
                            f"Herramientas válidas: {', '.join(tools[:60])}")},
                         {"role": "user", "content": f"Petición sin herramienta: {ctx[:400]}"}],
                        light=True)
                    import json as _js
                    txt = res.get("reply", "")
                    a, b = txt.find("{"), txt.rfind("}")
                    return _js.loads(txt[a:b + 1]) if a >= 0 and b > a else {}
                except Exception:
                    return {}

            def _discord_push(text: str) -> None:
                """DM al móvil del señor con el parte de autocorrección."""
                try:
                    b = getattr(brain, "discord", None)
                    if b is None:
                        return
                    import asyncio as _aio
                    _aio.run_coroutine_threadsafe(b.dm_owner(text), b._loop())
                except Exception as e:
                    log.debug("discord push: %s", e)

            def _voice_say(text: str) -> None:
                """Lo dice en voz alta, solo si no está en conversación."""
                try:
                    v = getattr(brain, "voice", None)
                    if v is None or getattr(v, "state", "idle") != "idle":
                        return
                    v.speak_out(text)
                except Exception as e:
                    log.debug("voice say: %s", e)

            _sh.start_fixer_loop(hooks={"discord": _restart_discord},
                                 ask_fn=_ask_protocol,
                                 emit_fn=brain.emit,
                                 discord_fn=_discord_push,
                                 voice_fn=_voice_say)
        except Exception:
            pass
        log.info("Warmup completo.")
    except Exception as e:
        log.debug("warmup: %s", e)


# --------------------------------------------------------------------------- #
# Detección de intents (datos reales, nunca inventados)
# --------------------------------------------------------------------------- #
_WAKE_UP_RE = re.compile(r"\b(buen[oa]s?\s+d[ií]as|despierta|despertar|wake\s*up|levant(?:a|e)|rutina\b.{0,15}\b(mañana|despertar|buenos d[ií]as)|protocolo\b.{0,15}\b(despertar|despertador|mañana|buenos d[ií]as))\b", re.I)
_SCREEN_NEW_RE = re.compile(r"\b(mira|lleva|leer|qu[eé] dice|ver)\b.*\b(pantalla|esto|screen)\b|"
                            r"\b(?:pantalla|screen)\b.*\b(?:mira|dime|qu[eé])\b|\bf1\b|\berror\b", re.I)
_SCREEN_OFF_RE = re.compile(r"\b(?:deja|dejar|para|stop|quita)\s+(?:de\s+)?ver\s+(?:mi\s+)?pantalla\b|"
                            r"\b(?:deja|stop|quita)\s+(?:el\s+)?an[aá]lisis\b", re.I)
_SCREEN_ON_RE = re.compile(r"\b(reanud|activ|resume|emps?ieza)\b.*\b(pantalla|an[aá]lisis|ver)\b|\bver\s+mi\s+pantalla\b", re.I)
# Control por gestos de la mano: encender / apagar el tracker de la cámara.
_GEST_ON_RE = re.compile(r"\b(?:enciende|enci[eé]ndelo|activa|act[ií]valo|prende|arranca|encender|activar|arrancar)\b"
                         r"[^.\n]{0,25}\b(?:gestos?|manos?|tracker)\b|"
                         r"\bgestos?\b[^.\n]{0,25}\b(?:enciende|encender|activa|activar|prende|arranca|arrancar)\b",
                         re.I)
_GEST_OFF_RE = re.compile(r"\b(?:apaga|ap[aá]galo|desactiva|desact[ií]valo|para|det[ée]n|quita|apagar|desactivar|parar|quitar)\b"
                          r"[^.\n]{0,25}\b(?:gestos?|manos?|tracker)\b|"
                          r"\bgestos?\b[^.\n]{0,25}\b(?:apaga|apagar|desactiva|desactivar|para|parar|quita|quitar)\b",
                          re.I)
# Cámara web: encender / apagar (sin empezar análisis, solo muestra la vista)
_CAM_OFF_RE = re.compile(r"\b(?:cierra|apaga|para|det[ée]n|quita|deja)\b.*\b(c[áa]mara|webcam|cam)\b|c[áa]mara\b.*\b(?:cierra|apaga|quita|fuera)\b|"
                         r"\b(?:ap[aá]gala|ci[eé]rrala|qu[íi]tala|qu[íi]talo|quit[aá]?rla|quit[aá]?rlo|ap[aá]galo)\b|"
                         r"\bapaga\s+(?:la\s+)?c[áa]mara\b", re.I)
_CAM_ON_RE = re.compile(r"\b(?:enciende|abre|activa|act[úu]a|prende|muestra|d[eé]jame ver)\b.*\b(c[áa]mara|webcam|cam)\b|c[áa]mara\b", re.I)
_CAM_ANALYZE_RE = re.compile(r"\b(?:analiza|anal[íi]zame|qu[eé] ves|q[uú]e? ves|observa|mira|describe)\b.*\b(c[áa]mara|webcam|cam)\b|c[áa]mara\b.*\b(?:analiza|qu[eé] ves|observa|describe)\b|\b(?:analiza|dime)\b[^.\n]{0,25}\b(?:lo que ves|lo que ve|lo que tienes delante|lo que hay delante)\b", re.I)
# --- Portadas de diarios (determinista, sin LLM que improvise la fuente) ---
_NEWS_TRIG_RE = re.compile(r"\bportada\b|\btitulares?\b|\bqué dice\b|\bqué pone\b|\bqué noticias\b", re.I)
_NEWS_SITES = ["mundo deportivo", "el país", "marca", "as", "sport", "md",
               "elpais", "elmundo", "abc", "bbc", "cnn"]
_CAMPUS_OPEN_RE = re.compile(r"\b(?:abre|abrir|entra|entrar)\b.{0,30}\b(campus|página del ciclo|ciclo|aviesau|aula virtual)\b", re.I)
# Desbloquear el campus = hacer login (credenciales ya configuradas). "página"
# puede referirse a lo que acabamos de abrir (la página del ciclo), vía contexto.
_CAMPUS_UNLOCK_RE = re.compile(r"\b(desbloque\w*|desbloqu[eé]ala?|quita\s+el\s+bloqueo|entra\s+al\s+campus|abre\s+sesi[óo]n|inicia\s+sesi[óo]n|logue[áa]\w*)\b", re.I)
_CAMPUS_PAGE_RE = re.compile(r"\b(p[áa]gina|sesi[óo]n|campus|ciclo|aviesau)\b", re.I)
# Preguntas sobre la página del ciclo que antes no cogía nadie y acababan en
# el modelo, que veía las herramientas vbox_* y contestaba con VirtualBox.
_CAMPUS_ASK_RE = re.compile(
    r"\b(esta?\s*(?:abiert[ao]?|desbloquead[ao]?|conectad[ao]?|cargand[ao]?)|"
    r"que\s+(?:tal|paso|ha\s+pasado)|sigue\s+abiert[ao]?|la\s+(?:abriste|abristeis)|"
    r"has\s+(?:abierto|abierto la)|funciona\w*|carga\w*|anda\w*|"
    r"queda\w*|se\s+(?:ha\s+quedado|quedo|cargo)|"
    r"puedo\s+(?:entrar|usar|ver)|me\s+(?:puedes|dejo)\s+entrar)\b", re.I)
# "está abierta?" sin sujeto: el hilo reciente dice de qué página hablamos.
# Sin tildes a propósito: por voz llegan sin ellas.
_ANTECEDENTE_RE = re.compile(r"^\s*(?:y\s+|pero\s+|a\s+ver\s+|o\s+)?"
                             r"(?:esta?\s+(?:abiert[ao]?|desbloquead[ao]?|conectad[ao]?|cargand[ao]?)|"
                             r"queda\w*|funciona\w*|carga\w*|anda\w*|"
                             r"me\s+(?:puedes|dejo)\s+entrar)\s*[?.!]*\s*$", re.I)
# --- Webapps que JARVIS abre DENTRO de la pizarra (Win+J) ---
# Debe coincidir con app/web-apps.js del frontal.
_WEB_APPS = {
    "campus": ("Campus AVIESAU", "https://av.ciclosallerulloa.gal/my/courses.php"),
    "ciclo": ("Campus AVIESAU", "https://av.ciclosallerulloa.gal/my/courses.php"),
    "aviesau": ("Campus AVIESAU", "https://av.ciclosallerulloa.gal/my/courses.php"),
    "moodle": ("Campus AVIESAU", "https://av.ciclosallerulloa.gal/my/courses.php"),
    "whatsapp": ("WhatsApp", "https://web.whatsapp.com/"),
    "youtube": ("YouTube", "https://m.youtube.com/"),
    "yt": ("YouTube", "https://m.youtube.com/"),
    "youtubemusic": ("YouTube Music", "https://music.youtube.com/"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "openai": ("ChatGPT", "https://chatgpt.com/"),
    "claude": ("Claude", "https://claude.ai/"),
    "perplexity": ("Perplexity", "https://www.perplexity.ai/"),
    "deepseek": ("DeepSeek", "https://chat.deepseek.com/"),
    "gemini": ("Gemini", "https://gemini.google.com/"),
    "google": ("Google", "https://www.google.com/"),
    "gmail": ("Gmail", "https://mail.google.com/"),
    "drive": ("Google Drive", "https://drive.google.com/"),
    "googledrive": ("Google Drive", "https://drive.google.com/"),
    "calendar": ("Google Calendar", "https://calendar.google.com/"),
    "wikipedia": ("Wikipedia", "https://es.wikipedia.org/"),
    "github": ("GitHub", "https://github.com/"),
    "gitlab": ("GitLab", "https://gitlab.com/"),
    "x": ("X", "https://x.com/"),
    "twitter": ("X", "https://x.com/"),
    "netflix": ("Netflix", "https://www.netflix.com/"),
    "twitch": ("Twitch", "https://www.twitch.tv/"),
    "spotify": ("Spotify Web", "https://open.spotify.com/"),
    "spotifyweb": ("Spotify Web", "https://open.spotify.com/"),
    "figma": ("Figma", "https://www.figma.com/files"),
    "notion": ("Notion", "https://www.notion.so/"),
    "trello": ("Trello", "https://trello.com/"),
    "canva": ("Canva", "https://www.canva.com/"),
    "pinterest": ("Pinterest", "https://www.pinterest.com/"),
    "instagram": ("Instagram", "https://www.instagram.com/"),
    "reddit": ("Reddit", "https://www.reddit.com/"),
    "linkedin": ("LinkedIn", "https://www.linkedin.com/feed/"),
    "slack": ("Slack", "https://app.slack.com/client"),
    "telegram": ("Telegram", "https://web.telegram.org/"),
    "tiktok": ("TikTok", "https://www.tiktok.com/"),
    "maps": ("Mapas", "https://maps.google.com/"),
    "googlemaps": ("Mapas", "https://maps.google.com/"),
    "translate": ("Traductor", "https://translate.google.com/"),
    "traductor": ("Traductor", "https://translate.google.com/"),
    "googletranslate": ("Traductor", "https://translate.google.com/"),
    "amazon": ("Amazon", "https://www.amazon.es/"),
    "ebay": ("eBay", "https://www.ebay.es/"),
    "wallapop": ("Wallapop", "https://es.wallapop.com/"),
    "steam": ("Steam", "https://store.steampowered.com/"),
    "epicgames": ("Epic Games", "https://store.epicgames.com/"),
    "msstore": ("Microsoft Store", "https://apps.microsoft.com/"),
    "yahoo": ("Yahoo Finanzas", "https://finance.yahoo.com/"),
    "news": ("Noticias", "https://news.google.com/?hl=es&gl=ES&ceid=ES:es"),
    "wolfram": ("Wolfram Alpha", "https://www.wolframalpha.com/"),
    "khanacademy": ("Khan Academy", "https://es.khanacademy.org/"),
    "coursera": ("Coursera", "https://www.coursera.org/"),
    "goodreads": ("Goodreads", "https://www.goodreads.com/"),
    "letterboxd": ("Letterboxd", "https://letterboxd.com/"),
    "strava": ("Strava", "https://www.strava.com/"),
    "duolingo": ("Duolingo", "https://www.duolingo.com/"),
}


def _webapp(nombre: str):
    """(etiqueta, url) si 'nombre' es una webapp, si no None. Exacto a propósito:
    con coincidencias laxas "Google Chrome" abría la página de Google."""
    key = re.sub(r"[^a-z0-9]", "", (nombre or "").lower())
    if not key:
        return None
    return _WEB_APPS.get(key)


# ----------------------------------------------------------------------------- #
# Rutas rápidas: lo obvious se resuelve sin preguntar al modelo
#
# "abre virtualbox", "abre spotify", "abre google": antes cada una gastaba una
# ida y vuelta completa al modelo para que acabara llamando a la misma
# herramienta. Aquí se resuelven con el índice de apps y se lanzan al momento.
# ----------------------------------------------------------------------------- #
_OPEN_CMD_RE = re.compile(
    r"^\s*(?:por favor\s+|oye\s+)?"
    r"(?:abre|abreme|abrir|inicia|iniciar|lanza|lanzame|ejecuta|arranca|"
    r"abre\s+el\s+programa|abre\s+la\s+app|abre\s+la\s+aplicaci[óo]n)\s+"
    r"(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+|mi\s+|mis\s+)*"
    r"(.{1,42}?)\s*[.¡!¿?]*\s*$", re.I)

# Palabras que son de otros caminos (archivos, carpetas, páginas, la nube...).
_NO_ES_APP_RE = re.compile(
    r"\b(archivo|fichero|carpeta|folder|directorio|p[áa]gina|web|url|enlace|"
    r"documento|pdf|imagen|foto|carpeta|v[íi]deo|nota|texto|sesi[óo]n|campus|"
    r"ciclo|puesto\s+de\s+mando|configuraci[óo]n|micr[óo]fono|archivos)\b", re.I)

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9][a-z0-9\-]*)+$", re.I)

_SEARCH_WEB_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:busca|buscar|b[uú]scame|b[uú]squeme|busque|b[uú]squeda|google|bing|duckduckgo)\s+"
    r"(?:(?:(?:un|el|la|los|las)\s+)?(?:resultado|resultados|enlace|enlaces)\s+(?:de|para)\s+)?"
    r"(?:(?:informaci[óo]n|datos|detalles|p[aá]ginas?)\s+(?:sobre|de|para)\s+)?"
    r"(?:en\s+(?:google|bing|duckduckgo|internet|la\s+web|web)\s+)?"
    r"(.{2,120}?)\s*(?:\s+en\s+(?:google|bing|duckduckgo|internet|la\s+web|web))?"
    r"\s*[.¡!¿?]*\s*$", re.I)
_RESEARCH_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:investiga|investigar|averigua|averiguar|documenta|documentar|"
    r"estudia|estudiar|profundiza|profundizar)\s+"
    r"(?:sobre\s+|acerca\s+de\s+|en\s+la\s+web\s+|en\s+internet\s+|que\s+es\s+|qu[eé]\s+es\s+)?"
    r"(.{2,140}?)\s*[.¡!¿?]*\s*$", re.I)

# "busca información sobre X" NO es abrir Google: es pedir un resumen. Se
# resuelve por la vía de investigación (resumen breve + ofrece profundizar).
_RESEARCH_BUSCA_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:busca|buscar|buscame|busque|dime)\s+"
    r"(?:la\s+|el\s+)?(?:informaci[óo]n|informaci[óo]nes|datos|detalles|"
    r"resumen|cosas|acerca\s+de|sobre)\s+"
    r"(?:de\s+|sobre\s+|acerca\s+de\s+|en\s+la\s+web\s+|en\s+internet\s+)?"
    r"(.{2,140}?)\s*[.¡!¿?]*\s*$", re.I)

# La investigación web nunca es un diagnóstico del equipo: eso es el barrido.
_NO_ES_EQUIPO_RE = re.compile(
    r"\b(el\s+equipo|el\s+ordenador|el\s+pc|el\s+sistema|mi\s+(ordenador|pc|equipo|sistema)|"
    r"mi\s+m[aá]quina|problema(s)?\b|error(es)?\b|fallo(s)?\b|fallos?\b|virus|viruses|"
    r"malware|vulnerabilidad|seguridad|auditor[ií]a)\b", re.I)

# Instalar un programa. Solo y exclusivamente cuando pide instalar: si dice
# "abre X" es abrir, no instalar. El verbo tiene que estar claro.
_INSTALL_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:instala|instale|instálame|instalam e|instalar|instalame|"
    r"descarga\s+y\s+instala|descarga\s+e\s+instala|"
    r"pon|ponme|añade|añademe|agrega|instalamelo)\s+"    r"(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+|el\s+programa\s+|"
    r"la\s+aplicaci[óo]n\s+|el\s+software\s+|en\s+mi\s+ordenador\s+)*"
    r"(.{2,50}?)\s*(?:en\s+(?:mi\s+)?(?:ordenador|pc|equipo|m[aá]quina|port[aá]til))?"
    r"\s*[.!¡?¿]*\s*$", re.I)

# Instalar desde GitHub. Acepta URL, "dueño/repo" y "X de/en github".
_GITHUB_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:instala|instálame|instalar|descarga|descárgame|baja|pon|añade|consigue)\s+"
    r"(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+)*"
    r"(.{2,90}?)\s*(?:de\s+github|en\s+github|desde\s+github|de\s+la\s+github)\s*"
    r"[.!¡?¿]*\s*$", re.I)

# "instálame https://github.com/..." o "instala sharkdp/bat" sin decir github.
_GITHUB_URL_RE = re.compile(
    r"^\s*(?:por favor\s+)?"
    r"(?:instala|instálame|instalar|descarga|descárgame|baja|pon|añade)\s+"
    r"(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+)*"
    r"(?P<ref>(?:https?://)?(?:www\.)?github\.com/[\w.\-]+/[\w.\-]+|"
    r"[\w.\-]+/[\w.\-]+)\s*[.!¡?¿]*\s*$", re.I)

# Respuesta corta al "¿quiere que profundice?". Solo Contestaciones de verdad:
# si el señor añade un tema ("profundiza en la energía nuclear") es una petición
# nueva y no un sí, así que aquí NO se captura.
_DEEP_SI_RE = re.compile(
    r"^\s*(?:"
    r"(?:s[ií]|si|claro|adelante|dale|hazlo|m[aá]s|mas|contin[uú]a|sigue|lee|leeme|"
    r"explica|mira|vamos|hecho|vale|ok|okay|perfecto)\s*"
    r"(?:(?:las|los|el|la)?\s*(?:fuentes|enlaces|textos?|p[aá]ginas?|todo|todos|"
    r"toda|todas|completo|completa|entero|entera|por favor|gracias|ahora|mas)\b[\s,]*)*"
    r"|(?:(?:las|los|el|la)?\s*(?:fuentes|enlaces|textos?|p[aá]ginas?|todo|todos|"
    r"toda|todas|completo|completa|entero|entera)\b[\s,]*)+"
    r")[.!?]*\s*$", re.I)

_DEEP_NO_RE = re.compile(
    r"^\s*(?:no|nop|nah|nel|bestia|suficiente|basta|listo|gracias|"
    r"con\s+eso|con\s+eso\s+va|ya\s+(?:est[aá]|me\s+val[eé]|vale|basta))\s*"
    r"(?:por favor\s*)?(?:gracias\s*)?[.!?]*\s*$", re.I)


def _summarize_deep(topic: str, texto: str, fuentes: list) -> str:
    """Resumen más extenso cuando el señor pide las fuentes enteras."""
    try:
        r = brain.router.respond(
            [{"role": "system", "content":
              ("Resume esto en español para el señor en 6-8 frases, por partes "
               "claras, sin inventar nada que no esté en el texto. "
               "Tono de mayordomo, directo al grano.")},
             {"role": "user", "content": texto[:12000]}],
            heavy=True)
        resumen = (r.get("reply") or "").strip()
    except Exception:
        resumen = texto[:900]
    return (f"Esto es lo que dicen las fuentes sobre «{topic}», señor: {resumen}\n\n"
            f"Enlaces: {', '.join(fuentes[:3])}")


def _open_in_browser(url: str) -> str:
    """Abre una URL en el navegador del sistema sin pasar por el modelo.

    En segundo plano: el navegador puede tardar uno o dos segundos en arrancar
    con un sitio que no tenía abierto, y el señor no debe esperar por eso.
    """
    def _ir():
        try:
            import webbrowser as _wb
            _wb.open(url)
        except Exception as e:
            log.debug("open browser: %s", e)
    threading.Thread(target=_ir, name="abrir-web", daemon=True).start()
    return url


def _fast_open(what: str) -> Optional[str]:
    """Abre 'qué' (sitio, aplicación, webapp o máquina virtual).

    Devuelve la respuesta si lo resolvió, o None si no sabe qué es y le toca al
    modelo. Orden: atajo conocido (página del ciclo, webs, máquinas) ->
    dominio escrito -> aplicación instalada -> webapp de la pizarra.

    Antes las máquinas virtuales no estaban en ninguna vía rápida: "abre
    userver26" tardaba diez segundos. Y el índice de apps respondía con el
    manual en PDF de VirtualBox, porque "userver26" empieza por "user".
    """
    what = (what or "").strip(" .¿?¡!\"'")
    # Relleno: "la aplicación de virtualbox" -> "virtualbox"
    what = re.sub(r"^\s*(?:la\s+|el\s+|los\s+|las\s+|un\s+|una\s+|mis\s+)*"
                  r"(?:aplicación|programa|app|juego|herramienta|software)\s+"
                  r"(?:de\s+|del\s+|para\s+)?", "", what, flags=re.I).strip()
    if not what or len(what) < 2:
        return None

    # 0) Atajos: página del ciclo, webs por nombre y máquinas virtuales.
    # Si el señor pide la pizarra, este camino no vale: la pizarra tiene su
    # propia lista de webapps y la comprueba más abajo.
    _quiere_pizarra = bool(re.search(r"\b(pizarra|tablero|board)\b", what, re.I))
    # "abre la página del ciclo en la pizarra": el nombre de la webapp es lo
    # de antes, no todo el rabo. Si no se quita, no hay coincidencia exacta y
    # la pregunta se cae otra vez en el modelo.
    _solo_nombre = re.sub(r"\s*(?:en|para|a)\s+(?:la\s+|el\s+)?"
                          r"(?:pizarra|tablero|board)\b.*$", "", what,
                          flags=re.I).strip()
    try:
        from integrations import atajos as _at
        _destino = _at.resolver(what)
    except Exception as e:
        log.debug("atajos: %s", e)
        _destino = None
    if _destino and _quiere_pizarra and _destino.get("url"):
        # Lo ha pedido en la pizarra: dentro va, no en el navegador.
        brain.emit({"type": "open_webapp", "url": _destino["url"],
                    "label": _destino.get("nombre") or "la página"})
        return (f"Abrida {_destino.get('nombre', 'la página')} en la pizarra, "
                f"señor. Aquí la tiene.")
    if _destino:
        if _destino["tipo"] == "vm":
            try:
                from tools.virtualbox_tools import vbox_start_handler
                r = vbox_start_handler({"name": _destino["nombre"]})
                if r.get("already"):
                    return (f"«{_destino['nombre']}» ya estaba encendida, señor. "
                            f"Listo para entrar.")
                return (f"Encendiendo «{_destino['nombre']}», señor. "
                        f"En un par de minutos la tiene.")
            except Exception as e:
                return (f"No pude encender «{_destino['nombre']}», señor: "
                        f"{str(e)[:100]}")
        # campus y web ordinary: al navegador, y listo
        _url = _destino.get("url", "")
        if _url and _open_in_browser(_url):
            if _destino["tipo"] == "campus":
                return f"Abierta {_destino['nombre']}, señor. Con su sesión dentro."
            return f"Abierto, señor: {_url}"

    if _NO_ES_APP_RE.search(what):
        # Con la pizarra de por medio gana la pizarra: "abre la página del
        # ciclo en la pizarra" tiene que salir dentro de la pizarra, aunque
        # lleve la palabra "página", que aquí no es un programa.
        if _quiere_pizarra:
            wa0 = _webapp(_solo_nombre) or _webapp(what)
            if wa0:
                label, url = wa0
                brain.emit({"type": "open_webapp", "url": url, "label": label})
                return f"Abrida {label} en la pizarra, señor. Aquí la tiene."
        return None

    # 1) Un dominio escrito tal cual -> directamente al navegador.
    if _DOMAIN_RE.match(what):
        url = what if what.startswith("http") else "https://" + what
        if _open_in_browser(url):
            return f"Abierto, señor: {url}"

    # 2) Aplicación instalada -> índice, sin subprocesos ni búsqueda en disco.
    try:
        from tools.system_tools import lookup_app, _launch_native
        target = lookup_app(what)
        if target:
            _launch_native(target)
            return f"Abierto, señor: {what}."
    except Exception as e:
        log.debug("fast open: %s", e)

    # 3) Webapp conocida -> dentro de la pizarra, sin navegador.
    wa = _webapp(_solo_nombre) or _webapp(what)
    if wa:
        label, url = wa
        brain.emit({"type": "open_webapp", "url": url, "label": label})
        return f"Abrida {label} en la pizarra, señor. Aquí la tiene."

    # 4) Ruta o archivo con extensión: no es una app.
    if "\\" in what or "/" in what:
        return None
    return None


def _instalar_rapido(nombre: str) -> str:
    """Instala la aplicación que le piden, resolviendo antes cuál es.

    No se inventa: si el catálogo no lo tiene y winget devuelve varias cosas
    parecidas, se las enseña al señor para que elija. Instalar el programa
    equivocado es peor que preguntar.
    """
    nombre = (nombre or "").strip(" .¡!¿?\"'")
    if not nombre or _NO_ES_APP_RE.search(nombre):
        return ""
    try:
        from tools import install_tools
    except Exception as e:
        log.debug("install_tools: %s", e)
        return ""
    brain.emit({"type": "action_status", "label": "Buscando " + nombre, "state": "running"})
    try:
        r = install_tools.start_install(nombre)
    except Exception as e:
        log.debug("start_install: %s", e)
        return ""
    estado = r.get("status")
    if estado == "unknown":
        return ""   # deja que el llamante pruebe otras vías (p.ej. GitHub)
    if estado == "ambiguous":
        brain.emit({"type": "action_status", "label": r.get("app", nombre), "state": "done"})
        return f"Ya lo tenía instalado, señor: {r.get('app', nombre)}."
    if estado == "ambiguous":
        lineas = "\n".join(
            f"  - {c['name']} ({c['id']})" for c in (r.get("candidatos") or []))
        return (f"He encontrado varias cosas parecidas a «{nombre}», señor, y no "
                f"quiero instalar la que no es. Dígame cuál:\n{lineas}")
    app, wid = r.get("app"), r.get("id")
    brain.emit({"type": "action_status", "label": "Instalando " + app, "state": "running"})
    return (f"Entendido, señor: {app}. Lo estoy instalando ahora mismo (puede "
            "tardar unos minutos). Le aviso en cuanto esté.")


def _virustotal_rapido(cleaned: str) -> str:
    """Pide a VirusTotal un veredicto de una URL o de un fichero."""
    _u = _extract_url(cleaned)
    try:
        from tools.syshealth_tools import vt_scan_url_handler, vt_scan_file_handler
    except Exception as e:
        log.debug("vt tools: %s", e)
        return ""
    brain.emit({"type": "action_status", "label": "Consultando a VirusTotal",
                "state": "running"})
    try:
        if _u:
            r = vt_scan_url_handler({"url": _u})
        else:
            m = re.search(r"([\w]:\\[^\"']+|[\w./\\-]+\.(?:exe|msi|scr|bat|cmd|"
                          r"dll|com|jar|zip|rar|7z|iso|img|apk))", cleaned, re.I)
            if not m:
                brain.emit({"type": "action_status", "label": "VirusTotal",
                            "state": "failed"})
                return ("Dígame qué quiere que mire, señor: una dirección web o "
                        "la ruta de un fichero.")
            r = vt_scan_file_handler({"path": m.group(1).strip('"')})
    except Exception as e:
        brain.emit({"type": "action_status", "label": "VirusTotal", "state": "failed"})
        return f"VirusTotal no me ha podido contestar, señor: {str(e)[:160]}"
    brain.emit({"type": "action_status", "label": "VirusTotal", "state": "done"})
    mal = r.get("malicious")
    if mal is None:
        return (f"VirusTotal todavía no tiene veredicto para eso, señor: acaba de "
                f"enviarlo a análisis y estos tardan unos minutos. Dígamelo "
                f"otra vez en un rato y se lo repito. Informe: {r.get('link','')}")
    if mal:
        return (f"ATENCIÓN, señor: {mal} motores lo marcan como MALICIOSO. "
                f"No lo abra. Informe: {r.get('link','')}")
    total = sum(int(v or 0) for v in (r.get("stats") or {}).values()
                if str(v).isdigit())
    return (f"Limpio, señor: 0 detecciones de {total or 'los'} motores de "
            f"VirusTotal. Informe: {r.get('link','')}")


# Techo de la LECTURA de una investigación (no cuenta la búsqueda). Antes no
# había ninguno y el bucle de páginas podía con seis intentos de 15 s: minuto y
# medio sin decir nada. Con red lenta o con la IP del contenedor bloqueada,
# era lo normal y no un caso raro.
PLAZO_INVESTIGACION_S = 25.0


def _research(topic: str, deep: bool = False, quick: bool = False) -> str:
    """Investiga en internet y devuelve un resumen BREVE. Si ve que la
    información se queda corta, se ofrece a profundizar en vez de alargar
    la respuesta por su cuenta."""
    topic = (topic or "").strip(" .¡!¿?\"'")
    if not topic:
        return "Dígame qué investigo, señor."
    from tools.web_tools import web_search_results_handler, web_fetch_handler
    if quick:
        # Las consultas de información ordinarias usan una sola petición web
        # corta; investigar, averiguar y profundizar conservan el flujo completo.
        try:
            from tools.web_tools import quick_web_search_handler
            fast = quick_web_search_handler({"query": topic})
            results = fast.get("results") or []
        except Exception as e:
            log.debug("búsqueda informativa rápida: %s", e)
            results = []
        if not results:
            return (f"La búsqueda rápida de «{topic}» no respondió, señor. "
                    "Pídame que investigue el tema para contrastar varias fuentes.")
        brain._pending_deep = {
            "topic": topic,
            "urls": [r.get("url", "") for r in results[:3] if r.get("url")],
        }
        lines = []
        for result in results[:3]:
            title = (result.get("title") or "Resultado web").strip()
            snippet = (result.get("snippet") or "").strip()
            url = (result.get("url") or "").strip()
            line = f"• [{title}]({url})" if url else f"• {title}"
            if snippet:
                line += f" — {snippet[:320]}"
            lines.append(line)
        return (f"Información rápida sobre «{topic}», señor:\n" +
                "\n".join(lines) +
                "\nSi quiere, puedo contrastarlo leyendo las fuentes.")
    try:
        res = web_search_results_handler({"query": topic})
    except Exception as e:
        return f"No pude buscarlo, señor: {str(e)[:140]}"
    results = res.get("results") or []
    if not results:
        return (f"No encontré nada concluyente sobre «{topic}», señor. "
                "Pruebe con otras palabras o dígame el enlace.")
    cuantos = 3 if deep else 1
    trozos, fuentes = [], []
    # Techo de lectura. Sin esto, una pregunta podía quedarse hasta seis
    # páginas × 15 segundos = 90 s callados cuando los sitios bloquean la
    # lectura, que es justo lo que pasa con la salida a internet de un
    # contenedor. Se sigue probando más de un enlace (muchos sitios bloquean
    # la lectura), pero no indefinidamente: pasado el plazo se usa lo que haya.
    _fin = time.monotonic() + PLAZO_INVESTIGACION_S
    for r in results[:6]:
        if len(trozos) >= cuantos:
            break
        if time.monotonic() > _fin:
            log.debug("investigación: plazo de lectura agotado con %d fuente(s)",
                      len(fuentes))
            break
        u = r.get("url") or ""
        if not u.startswith("http"):
            continue
        try:
            txt = (web_fetch_handler({"url": u, "timeout": 8}).get("content") or "").strip()
        except Exception:
            txt = ""
        if len(txt) > 400:
            trozos.append(txt[:3000])
            fuentes.append(u)
    if not trozos:
        # Último recurso: Wikipedia, que siempre deja leer.
        try:
            from tools.web_tools import _search_wiki
            for r in _search_wiki(topic)[:3]:
                if time.monotonic() > _fin:
                    break
                txt = (web_fetch_handler({"url": r["url"], "timeout": 8}).get("content") or "").strip()
                if len(txt) > 400:
                    trozos.append(txt[:3000])
                    fuentes.append(r["url"])
                    break
        except Exception as e:
            log.debug("research wiki: %s", e)
    if not trozos:
        return (f"Sí, {topic} sale en varios sitios, señor, pero las páginas no me "
                "dejan leerlas. Le doy la dirección principal: "
                f"{results[0].get('url','')}")
    cuerpo = "\n\n---\n\n".join(trozos)[:9000 if deep else 5000]
    try:
        r2 = brain.router.respond(
            [{"role": "system", "content":
              ("Resume esto en español para el señor: 3 frases como máximo, "
               "solo lo esencial y sin inventar nada que no esté aquí. "
               "Tono de mayordomo, seco y directo.")},
             {"role": "user", "content": cuerpo}],
            light=not deep)
        resumen = (r2.get("reply") or "").strip()
    except Exception:
        resumen = cuerpo[:400]
    head = "Profundizando" if deep else "He investigado"
    salida = f"{head} «{topic}», señor. En corto: {resumen}"
    if not deep:
        brain._pending_deep = {"topic": topic, "urls": fuentes or [r.get("url", "") for r in results[:3]]}
        salida += (" ¿Quiere que profundice en algo concreto o que le lea las "
                   "fuentes enteras?")
    else:
        brain._pending_deep = None
        salida += f" Fuentes: {', '.join(fuentes[:3])}"
    return salida


def _aviso_instalacion(res: dict) -> None:
    """Lo llama install_tools cuando la instalación termina (o falla)."""
    app = res.get("app") or "la aplicación"
    try:
        brain.emit({"type": "action_status", "label": app,
                    "state": "done" if res.get("ok") else "failed"})
        if res.get("ok"):
            brain.emit({"type": "assistant_reply", "text":
                        f"Listo, señor: {app} ya está instalada. Ya puede "
                        f"pedirme que la abra cuando quiera."})
        else:
            det = (res.get("detalle") or "").strip().replace("\n", " ")
            det = det[-220:] if len(det) > 220 else det
            brain.emit({"type": "assistant_reply", "text":
                        f"No he podido terminar de instalar {app}, señor."
                        + (f" El instalador dijo: {det}" if det else "")})
    except Exception as e:
        log.debug("aviso de instalación: %s", e)


def _instalar_desde_github(ref: str) -> str:
    """Instala la publicación oficial de un repositorio de GitHub."""
    ref = (ref or "").strip(" .¡!¿?\"'")
    if not ref:
        return ""
    try:
        from tools import github_tools as _gh
    except Exception as e:
        log.debug("github_tools: %s", e)
        return ""
    try:
        r = _gh.consultar_github(ref)
    except Exception as e:
        log.debug("consultar_github: %s", e)
        return ""
    estado = r.get("status")
    if estado in ("no_repo", "limite"):
        return f"He mirado en GitHub, señor, pero {r.get('detalle','')}"
    if estado == "ambiguous":
        lineas = "\n".join(
            f"  - {c['name']} ({c.get('stars', 0)} estrellas)"
            + (f": {c['desc']}" if c.get("desc") else "")
            for c in (r.get("candidatos") or []))
        return (f"He encontrado varios repositorios con ese nombre, señor. "
                f"Dígame cuál quiere:\n{lineas}")
    if estado in ("sin_acceso", "sin_publicacion", "sin_paquete_windows"):
        brain.emit({"type": "action_status", "label": "GitHub", "state": "failed"})
        return (f"He mirado en GitHub, señor, pero {r.get('detalle','')}")
    if estado != "ready":
        return ""
    brain.emit({"type": "action_status",
                "label": f"Descargando {r.get('repo','')}", "state": "running"})
    try:
        _gh.start_install_github(ref)
    except Exception as e:
        log.debug("start_install_github: %s", e)
        return ""
    mb = round((r.get("tamano") or 0) / 1048576, 1)
    return (f"Entendido, señor: {r.get('repo')} versión {r.get('tag','')}. "
            f"Descargando {r.get('paquete')} ({mb} MB) e instalándolo. "
            "Le aviso en cuanto esté.")


def _aviso_instalacion_github(res: dict) -> None:
    """Lo llama github_tools cuando la descarga/instalación termina."""
    try:
        repo = res.get("repo", "")
        ok = res.get("status") == "instalado" and res.get("ok")
        brain.emit({"type": "action_status", "label": repo or "GitHub",
                    "state": "done" if ok else "failed"})
        if ok:
            det = (res.get("detalle") or "").strip()
            extra = f" {det}" if det else ""
            try:
                from tools.system_tools import refresh_index
                refresh_index()
            except Exception:
                pass
            brain.emit({"type": "assistant_reply", "text":
                        f"Listo, señor: {repo} {res.get('tag','')} instalado.{extra}"})
        else:
            det = (res.get("detalle") or "").strip().replace("\n", " ")
            det = det[-240:] if len(det) > 240 else det
            brain.emit({"type": "assistant_reply", "text":
                        f"No he podido terminar con {repo}, señor."
                        + (f" {det}" if det else "")})
    except Exception as e:
        log.debug("aviso github: %s", e)



# --- Visión de ventana concreta (segundo plano incluido) ---
_WIN_VERB_RE = re.compile(r"\b(?:mira|observa|describe|dime\s+qu[eé]\s+hay\s+en)\b", re.I)
_WIN_OF_RE = re.compile(r"\bventana\b\s*(?:de\s+|del\s+)?(\S+)?", re.I)
_WIN_APPS = {"brave": "brave", "chrome": "chrome", "edge": "edge",
             "firefox": "firefox", "vscode": "code", "visual": "code",
             "code": "code", "discord": "discord", "whatsapp": "whatsapp",
             "spotify": "spotify", "word": "winword", "excel": "excel",
             "notepad": "notepad", "bloc": "notepad", "terminal": "terminal",
             "cmd": "cmd", "powershell": "powershell", "explorador": "explorer",
             "explorer": "explorer", "steam": "steam", "juego": "", "juegos": ""}
_CAM_WORD_RE = re.compile(r"\bc[áa]mara\b|\bwebcam\b|\bcam\b", re.I)


def _window_target(cleaned: str) -> str | None:
    """Devuelve target ("" = al frente) o None si no es petición de ventana."""
    if not _WIN_VERB_RE.search(cleaned) or _CAM_WORD_RE.search(cleaned):
        return None
    m = _WIN_OF_RE.search(cleaned)
    if m:
        return (m.group(1) or "").strip()
    low = cleaned.lower()
    for word, frag in _WIN_APPS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            return frag
    return None


_CAM_FRAME_TIMEOUT = 25.0


def _request_camera_frame(prompt: str, warmup_sec: float = 4.0) -> None:
    """Pide un frame al overlay con watchdog: enciende la cámara, espera que
    arranque, registra el pendiente y si en _CAM_FRAME_TIMEOUT no llega nada,
    avisa por voz y lo mete al bus de autocorrección (antes quedaba colgado
    en 'Analizando, señor…' para siempre)."""
    token = time.time()

    def _on_frame(_b64: str, _prompt: str) -> None:
        try:
            from vision.analyzer import analyze_webcam_frame
            res = analyze_webcam_frame(_b64, _prompt)
            brain.emit({"type": "assistant_reply", "text": res})
        except Exception as e:
            log.debug("camera analyze: %s", e)
            try:
                from memory import selfheal as _sh
                _sh.report_error("camera", e, "analizando frame de webcam")
                _sh.fix_now()
            except Exception:
                pass
            brain.emit({"type": "assistant_reply",
                        "text": f"No pude analizar la cámara, señor: {e}. "
                                "Me pongo a arreglarlo ahora mismo."})

    def _run():
        try:
            brain.emit({"type": "camera_mode", "state": "on"})
        except Exception:
            pass
        time.sleep(warmup_sec)
        brain._pending_camera_analyze = (_on_frame, prompt, token)
        try:
            brain.emit({"type": "camera_analyze_request", "message": prompt})
        except Exception as e:
            log.debug("camera req: %s", e)
        time.sleep(_CAM_FRAME_TIMEOUT)
        if getattr(brain, "_pending_camera_analyze", None) \
                and brain._pending_camera_analyze[2] == token:
            brain._pending_camera_analyze = None
            # Respaldo: si la pizarra no manda nada (está cerrada, o el equipo
            # no tiene Media Foundation y Chromium no abre la cámara), el
            # cerebro hace la foto él mismo con OpenCV.
            try:
                import base64 as _b64
                from vision.webcam import obtener as _wc
                jpg = _wc().jpeg(70)
                if jpg:
                    log.info("cámara: sin frame del overlay, tiro con OpenCV")
                    _on_frame(_b64.b64encode(jpg).decode("ascii"), prompt)
                    return
            except Exception as e:
                log.debug("respaldo de cámara: %s", e)
            brain.emit({"type": "assistant_reply",
                        "text": "No pude acceder a la cámara, señor: el overlay no "
                                "me envió imagen y mi captura directa tampoco. "
                                "Lo registro y me pongo a arreglarlo ahora mismo."})
            try:
                from memory import selfheal as _sh
                _sh.report_error("camera",
                                 "Sin frame del overlay en 25s (overlay cerrado o sin permiso)",
                                 prompt[:300])
                _sh.fix_now()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True, name="cam-req").start()
# --- Biblioteca local de apuntes (carpetas con PDF/DOCX/TXT/MD) ---
_DOC_USE_RE = re.compile(r"\b(?:usa|lee|indexa|a[ñn]ade|agrega)\s+(?:la\s+)?carpeta\s+(.+)$", re.I)
_DOC_STATUS_RE = re.compile(r"\b(qu[ée]|tienes|usas|hay|dime|estado|lista)\b.{0,30}\b(carpetas?|apuntes|biblioteca)\b|"
                            r"\b(carpetas?|apuntes|biblioteca)\b.{0,30}\b(qu[ée]|tienes|hay|cu[aá]nt)", re.I)
_DOC_REINDEX_RE = re.compile(r"\breindexa\b|\bindexa de nuevo\b|\bactualiza (?:la )?biblioteca\b", re.I)
_DOC_FORGET_RE = re.compile(r"\bolvida\b.*\bcarpetas?\b|\bquita\b.*\bcarpetas?\b", re.I)
# --- Estado de VMs: preguntas de solo-lectura (siempre en fresco, sin memoria) ---
_VM_STATUS_RE = re.compile(r"\b(qu[ée]|cu[áa]les|lista|dime|estado|est[áa]n?|hay|tienes)\b.{0,40}\b(m[áa]quinas?|vms?)\b|"
                           r"\b(m[áa]quinas?|vms?)\b.{0,40}\b(encendid|apagad|estado|lista|cu[áa]nt)\b|"
                           # "dime el estado de virtualbox": el sustantivo va detrás.
                           r"\b(estado|listado|resumen)\b.{0,20}\b(virtualbox|virtual\s?box)\b", re.I)
_VM_STATE_TALK = re.compile(r"\b(encendid|apagad)[ao]s?\b", re.I)
_VM_NOUN = re.compile(r"\b(m[áa]quinas?|vms?|server|ubuntu|windows|debian|virtualbox)\b", re.I)
_VM_ACTION_RE = re.compile(r"\b(arranca|enciende|prende|apaga|para|crea|desbloquea|despierta|abre|ejecuta|instala|reinicia|borra|clona)\b", re.I)
# Ejecución directa: "ejecuta|escribe <comando> [en <destino>]" (sin LLM que invente).
_EXEC_RE = re.compile(r"^\s*(?:por favor[,.]?\s+)?(ejecuta|lanza|run|escribe|pon|corre|teclea)\b\s+(?:el\s+comando\s+)?(.+)$", re.I)
_EXEC_IN_RE = re.compile(r"\s+en\s+([\w .\-áéíóúñü]+)$", re.I)
_EXEC_CTX_RE = re.compile(r"\b(m[áa]quina|vm|server|ubuntu|windows|debian|linux|consola|terminal)\b", re.I)


_SCAN_KEYWORDS = ("problema", "error", "fallo", "vulnerabilidad", "virus", "malware",
                 "seguridad", "sistema", "ordenador", "equipo", "pc")


def _run_system_scan(cleaned: str) -> str:
    """Investiga el equipo entero y pregunta antes de tocar nada."""
    try:
        from tools.diag_tools import system_diagnose_handler
    except Exception as e:
        return f"No pude preparar la investigación, señor: {e}"
    areas = ""
    low = cleaned.lower()
    if "seguridad" in low or "virus" in low or "vulnerab" in low:
        areas = "seguridad"
    elif "rendimiento" in low or "lento" in low or "optimiz" in low:
        areas = "rendimiento"
    elif "voz" in low or "tts" in low or "stt" in low or "micrófono" in low or "micro" in low:
        areas = "jarvis"
    brain.emit({"type": "action_status", "label": "Investigando el equipo",
                "state": "running"})
    try:
        res = system_diagnose_handler({"areas": areas})
    except Exception as e:
        brain.emit({"type": "action_status", "label": "Investigación",
                    "state": "failed"})
        return f"La investigación falló, señor: {e}"
    brain.emit({"type": "action_status", "label": "Investigación terminada",
                "state": "done"})
    fallos = res.get("problems", 0)
    if not fallos:
        return (f"He revisado el equipo entero, señor: {res.get('total', 0)} "
                "comprobaciones y nada fuera de orden. Su sistema está sano.")
    det = [f"{f['area']}: {f['detail']}" for f in res.get("findings", [])
           if f.get("ok") is False]
    _lst = "; ".join(det[:4])
    brain._pending_scan = res
    return (f"Investigación terminada, señor. {fallos} problema(s) de "
            f"{res.get('total', 0)} comprobaciones: {_lst}. "
            "¿Quiere que los arregle? Diga sí o no.")


def _apply_scan_fixes(res: dict) -> str:
    """El señor autorizó: manda cada hallazgo al reparador (pide permiso por
    acción si es delicado) y responde con lo que hará."""
    from memory import selfheal as _sh
    n = 0
    for f in res.get("findings", []):
        if f.get("ok") is not False:
            continue
        _sh.report(f"scan:{f.get('area', 'sistema')}",
                   f"{f.get('area')}: {f.get('detail')}",
                   "hallazgo de la investigación del equipo")
        n += 1
    if not n:
        return "No queda nada que reparar, señor."
    return (f"Entendido, señor. He registrado {n} hallazgo(s) y le pediré "
            "permiso antes de cada reparación.")


_URL_RE = re.compile(r"((?:https?://|www\.)[^\s\"'<>]+)", re.I)


def _extract_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    u = m.group(1)
    # Quita puntuación final SOLO si no cierra un paréntesis/corchete abierto.
    while u and u[-1] in ".,;:!?":
        u = u[:-1]
    if u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1]
    if u.endswith("]") and u.count("]") > u.count("["):
        u = u[:-1]
    return u


def _last_url() -> str:
    """La última URL mencionada, por si dice 'esta página'."""
    try:
        for m in reversed(db.get_recent_messages(limit=12)):
            u = _extract_url(m.get("content", "") or "")
            if u:
                return u
    except Exception:
        pass
    return ""


def _read_page(url: str, abrir: bool = False) -> str:
    """Lee la página y la resume. Solo abre el navegador si se le pide."""
    if abrir:
        try:
            import webbrowser as _wb
            _wb.open(url)
        except Exception:
            pass
    try:
        from tools.web_tools import web_fetch_handler
        r = web_fetch_handler({"url": url, "timeout": 20})
    except Exception as e:
        return f"No pude leer esa página, señor: {str(e)[:120]}"
    txt = (r.get("content") or "").strip()
    if not txt:
        return (f"No he podido extraer el contenido de {url}, señor. "
                "Puede estar bloqueada o depender de JavaScript.")
    cuerpo = txt[:6000]
    try:
        res = brain.router.respond(
            [{"role": "system", "content":
              "Resume esta página en español, en 4 frases como máximo, sin "
              "inventar nada que no esté en el texto. Empieza por lo "
              "esencial. Tono de mayordomo, secas y al grano."},
             {"role": "user", "content": cuerpo}],
            light=True)
        resumen = (res.get("reply") or "").strip()
    except Exception:
        resumen = cuerpo[:600]
    aviso = "Página abierta y leída, señor" if abrir else "He leído"
    return f"{aviso} {url}, señor. En resumen: {resumen}"


def _vm_status_asked(cleaned: str) -> bool:
    if _VM_ACTION_RE.search(cleaned):
        return False
    if _VM_STATUS_RE.search(cleaned):
        return True
    return bool(_VM_STATE_TALK.search(cleaned) and _VM_NOUN.search(cleaned))


def _sin_tildes(texto: str) -> str:
    """Minúsculas sin tildes: para comparar lo que dice el micro con lo escrito.

    El reconocimiento de voz devuelve "la pagina del ciclo" y el patrón pedía
    "página": no encajaba y la pregunta acababa respondida con VirtualBox.
    """
    import unicodedata
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _campus_status_reply(cleaned: str) -> str:
    """Responde en fresco si la página del ciclo está dentro, y por su cuenta.

    Antes estas preguntas no las cogía nadie: llegaban al modelo, que con las
    herramientas vbox_* a la vista contestaba con el estado de VirtualBox.
    """
    try:
        from integrations import campus as _campus
        dentro = bool(_campus.login())
    except Exception as e:
        return (f"No pude comprobar la página del ciclo, señor: "
                f"{str(e)[:90]}. Si quiere, la abro.")
    if dentro:
        return ("La página del ciclo está abierta y con su sesión dentro, señor. "
                "Listo para entrar.")
    return ("La página del ciclo se ha quedado sin sesión, señor. "
            "Dígame «desbloquéala» y la entro con su usuario.")


def _about_campus(cleaned: str) -> bool:
    """¿Está hablando de la página del ciclo (o de lo último que abrió)?

    Todo se compara sin tildes, porque por voz casi nunca llegan.
    """
    t = _sin_tildes(cleaned)
    if re.search(r"\b(maquina|vm|userver|virtualbox|servidor|ubuntu|debian)\b", t):
        return False                      # si habla de VM, es una VM
    if re.search(r"\b(pagina\s+del\s+ciclo|pagina\s+de\s+aviesau|campus|"
                 r"aviesau|aula\s+virtual|moodle)\b", t):
        return True
    if re.search(r"\bciclo\b", t) and not re.search(
            r"\b(luz|electri|energia|bici|carbono|formacion)\b", t):
        return True
    # "está abierta", "funciona": el hilo reciente dice de qué va.
    if _ANTECEDENTE_RE.search(t) and _last_page_context_is_campus():
        return True
    return False


# --- Atajos del campus: se responden con el índice, sin llamar al modelo ---
_CURSOS_RE = re.compile(r"\b(qu[ée]\s+(?:materias|asignaturas|curso|asignaturas)|"
                        r"mis\s+cursos|que\s+cursos\s+tengo|lista\s+de\s+cursos|"
                        r"cursos\s+que\s+tengo|mis\s+materias|que\s+materias)\b", re.I)
_NOTAS_RE = re.compile(r"\b(mis\s+notas|las\s+notas|que\s+notas\s+tengo|"
                        r"notas\s+de\s+(?:que\s+)?curso|cuantas\s+notas)\b", re.I)
_BUSCAR_CAMPUS_RE = re.compile(
    r"\b(busca|buscar|buscame|encuentra|encuentrame|dime\s+donde|qu[ée]\s+(?:hay|tengo)\s+en)\b"
    r"[^.?!\n]{0,40}\b(campus|ciclo|aviesau|asignatura|materia|tarea|actividad)\b", re.I)
_REFRESCAR_RE = re.compile(r"\b(actualiza|actualizar|refresca|refrescar|reindexa|"
                            r"vuelve\s+a\s+mirar|revisa)\b[^.?!\n]{0,25}\b(campus|ciclo|aviesau|notas|entregas)\b",
                            re.I)
_CURSO_NOMBRADO_RE = re.compile(
    r"\b(?:que\s+hay\s+en|que\s+tiene|contenido\s+de|detalle\s+de|"
    r"de\s+que\s+va|sobre\s+que\s+es|explica(?:me)?|habla(?:me)?\s+de)\b"
    r"[^.?!\n]{0,40}?\b(?P<curso>[a-z\u00c1\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]{3,40})\s*$", re.I)


# Palabras sueltas que se parecen a un atajo pero son conversación normal.
# Sin esta lista, decir «as» abría as.com y «mas» hacía cualquier cosa.
_PARECE_UN_ATAJO = set("""
as ala ave mas mas mas son ver vez dia ano ano que cual quien como donde
cuando porque para pero con sin sobre bajo tras desde hasta cada algo
nada todo todos uno dos tres fin red red si no ya aqui alli esto esto ese
esa aquel tal cual ahora luego despues antes siempre nunca aqui
""".split())


def _atajo_directo(cleaned: str) -> str:
    """Basta decir el nombre: «marca», «userver26», «ciclo», «notion».

    Para lo que no hace falta pensar, el señor no tiene que escribir una frase.
    Si la palabra suelta no es de nada conocido, se devuelve vacío y sigue la
    conversación normal: aquí no se inventa.
    """
    t = _sin_tildes(cleaned).strip(" .?!,;:¡¿'\"")
    t = re.sub(r"^(?:por favor\s+|oye\s+|a ver\s+|venga\s+|anda\s+)", "", t).strip()
    t = re.sub(r"\s+(?:por favor|ahora|venga|vamos)$", "", t).strip()
    if not t or len(t) < 3 or len(t) > 42:
        return ""
    palabras = t.split()
    if len(palabras) > 4:
        return ""
    if palabras[0] in _PARECE_UN_ATAJO:
        return ""
    # Si trae un verbo es una frase, no un comando suelto.
    if re.search(r"\b(abre|abrir|dime|diga|busca|cuantas|cuantos|por|que|"
                 r"como|cuando|porque|quiere|sirve|haz|pon|ponme)\b", t):
        return ""

    # 1) Máquina virtual, web, o la página del ciclo.
    try:
        from integrations import atajos as _at
        destino = _at.resolver(t)
    except Exception as e:
        log.debug("atajo directo: %s", e)
        destino = None
    if destino:
        if destino["tipo"] == "vm":
            try:
                from tools.virtualbox_tools import vbox_start_handler
                r = vbox_start_handler({"name": destino["nombre"]})
                if r.get("already"):
                    return (f"«{destino['nombre']}» ya estaba encendida, señor. "
                            f"Ahí la tiene.")
                return (f"Encendiendo «{destino['nombre']}», señor. "
                        f"En un par de minutos la tiene.")
            except Exception as e:
                return (f"No pude encender «{destino['nombre']}», señor: "
                        f"{str(e)[:100]}")
        _url = destino.get("url", "")
        if _url:
            _open_in_browser(_url)
            if destino["tipo"] == "campus":
                return "Abierta la página del ciclo, señor. Con su sesión dentro."
            return f"Abierto, señor: {_url}"

    # 2) Asignatura suelta: los apuntes de esa materia, al instante.
    try:
        from integrations import campus_index as _ix
        nombre = _ix.mejor_curso(t)
        if nombre:
            # Con solo el nombre de la asignatura se enseña el temario: qué
            # secciones tiene, qué actividades y qué notas. Si además concreta
            # un tema ("dime nftables de sistemas operativos"), van los apuntes.
            return _ix.detalle(nombre)
    except Exception as e:
        log.debug("atajo directo apuntes: %s", e)

    # 3) Webapp de la pizarra (youtube, whatsapp, maps...).
    wa = _webapp(t)
    if wa:
        label, url = wa
        brain.emit({"type": "open_webapp", "url": url, "label": label})
        return f"Abrida {label} en la pizarra, señor. Aquí la tiene."

    # 4) Programa instalado.
    try:
        from tools.system_tools import lookup_app, _launch_native
        dest = lookup_app(t)
        if dest:
            _launch_native(dest)
            return f"Abierto, señor: {t}."
    except Exception as e:
        log.debug("atajo directo app: %s", e)
    return ""


def _campus_atajo(cleaned: str) -> str:
    """Preguntas del campus resueltas con el índice, sin modelo y al instante."""
    t = _sin_tildes(cleaned)
    try:
        from integrations import campus_index as ix
    except Exception as e:
        log.debug("campus index no disponible: %s", e)
        return ""
    try:
        if _REFRESCAR_RE.search(t):
            ix.refrescar()          # en segundo plano: no se para a esperar
            return ("Voy refrescando el campus, señor. En un par de minutos "
                    "lo tengo al día.\n\n" + ix.resumen())
        if _CURSOS_RE.search(t):
            return ix.resumen()
        if _NOTAS_RE.search(t):
            idx = ix.construir()
            con_notas = [(f.get("nombre", ""), n)
                         for f in idx.get("cursos", {}).values()
                         for n in f.get("notas", []) if n.get("nota")]
            if con_notas:
                lineas = [f"  · {c}: {n['item']} = {n['nota']}"
                          for c, n in con_notas[:14]]
                return ("Sus notas, señor:\n" + "\n".join(lineas))
            from tools.campus_tools import campus_grades_handler
            try:
                return campus_grades_handler({}) or ix.resumen()
            except Exception:
                return ix.resumen()
        if _BUSCAR_CAMPUS_RE.search(t):
            palabra = re.sub(r"^.*?\b(?:busca|buscar|buscame|encuentra|"
                             r"encuentrame|dime\s+donde|que\s+hay\s+en)\b", "", t,
                             flags=re.I).strip()
            palabra = re.sub(r"\b(en|de|del|la|el|los|las|un|una|algo|sobre|"
                             r"campus|ciclo|aviesau|asignatura|materia|mis|tareas?|"
                             r"actividad|actividades)\b",
                             " ", palabra).strip()
            palabra = re.sub(r"\s+", " ", palabra).strip()
            if len(palabra) < 3:
                return ix.resumen()
            hallados = ix.buscar(palabra, limite=10)
            if not hallados:
                return (f"No encuentro «{palabra}» en su campus, señor. "
                        f"\n\n{ix.resumen()}")
            lineas = [f"  · [{h['tipo']}] {h['texto']} ({h['curso']})"
                      for h in hallados[:8]]
            return (f"Encontré {len(hallados)} cosas con «{palabra}», señor:\n"
                    + "\n".join(lineas))
        m = _CURSO_NOMBRADO_RE.search(t)
        if m:
            nombre = ix.mejor_curso(m.group("curso"))
            if nombre:
                return ix.detalle(nombre)
    except Exception as e:
        log.warning("campus atajo fallo: %s", e)
    return ""


# "dime las configuraciones DHCP", "enséñame el tema de nftables"
_APUNTES_VERBOS = re.compile(
    r"\b(dime|dime\s+que|dime\s+como|dame|dame\s+los|mu[ée]strame|ens[ée][ñn]ame|"
    r"explica(me)?|explicame|cu[ée]ntame|ponme|muestrame|leeme|dime\s+como\s+se|"
    r"que\s+hay\s+en|que\s+dice|que\s+trata|de\s+que\s+va|sobre\s+que\s+es|"
    r"d[óo]cumentos?|apuntes?|documentaci[óo]n|temas?|contenido)\b", re.I)
_APUNTES_PIDE = re.compile(
    r"\b(dime|dame|mu[ée]strame|ens[ée][ñn]ame|explica(me)?|cu[ée]ntame|"
    r"ponme|leeme|ver|mirar|lee|leer|busca(me)?|quiero\s+ver|"
    r"necesito|quiero)\b", re.I)
_APUNTES_TEMAS = re.compile(
    r"\b(dhcp|nftables|iptables|samba|sssd|dns|wireshark|tcpdump|lynis|pam|"
    r"nmap|apache|nginx|postgresql|mysql|kerberos|ldap|ssl|tls|ssh|vlan|"
    r"enrutamiento|subnetting|segmentaci[óo]n|firewall|router|servidor|"
    r"seguridad\s+inform[áa]tica|sistemas\s+operativos|servicios\s+en\s+rede|"
    r"redes|criptograf[ií]a|auditor[ií]a|planificaci[óo]n|respaldo|"
    r"econom[ií]a\s+circular|aplicaciones\s+web|apuntes|notas)\b", re.I)
# Lo que pide pensar en vez de leer: eso lo responde el modelo, con los
# apuntes inyectados, que es donde se lucen.
_APUNTES_RAZONAR = re.compile(
    r"\b(explica(me)?|expl[íi]came|explicas?|por\s+qu[ée]|para\s+qu[ée]|"
    r"c[óo]mo\s+(?:se|hago|puedo)|como\s+funciona|qu[ée]\s+es\s+la\s+diferencia|"
    r"compara\w*|diferencia\w*|analiza\w*|resume\w*|hazme\s+un|ayudame\s+a|"
    r"sabes\s+alguna|recomi[ée]ndame|sugerencia)\b", re.I)


def _inyectar_apuntes(cleaned: str) -> bool:
    """¿Esta pregunta va de un tema de sus cursos? Si es así, hay que darle
    los apuntes antes de que conteste el modelo."""
    t = _sin_tildes(cleaned)
    if _APUNTES_TEMAS.search(t) or _about_campus(t):
        return len(t) > 12          # sin tonterías de dos palabras
    return False


def _tema_de_la_pregunta(cleaned: str) -> str:
    """Saca el tema de 'dime las configuraciones dhcp' -> 'configuraciones dhcp'."""
    t = re.sub(r"^\s*(?:por favor\s+|oye\s+|a ver\s+)?", "", cleaned, flags=re.I)
    t = _APUNTES_VERBOS.sub(" ", t)
    t = _APUNTES_PIDE.sub(" ", t)
    t = re.sub(r"\b(por\s+favor|ahora|mismo|mas\s+tarde|hazme|ha[z|]me)\b",
               " ", t, flags=re.I)
    t = re.sub(r"\b(el|la|los|las|un|una|de|del|en|con|sobre|para|mis|tus|"
               r"apuntes|notas|temas?|contenido|documentos?|y)\b",
               " ", t, flags=re.I)
    # "cual es el tema de X" -> "X"
    t = re.sub(r"^\s*(?:cual\s+es|que\s+es|que\s+son|hay|cuanto|cuantas|"
               r"dime|mira|ver|vamos)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ?!.")
    return t


def _campus_apuntes_atajo(cleaned: str) -> str:
    """Pregunta por un tema de sus cursos: respuesta con los apuntes, al instante."""
    t = _sin_tildes(cleaned)
    if not (_APUNTES_VERBOS.search(t) or _APUNTES_PIDE.search(t)):
        return ""
    # "explícame", "¿cómo se hace?" y "¿por qué?" piden razonamiento, no un
    # recite: eso lo hace el modelo, con los apuntes delante (ver más abajo).
    if _APUNTES_RAZONAR.search(t):
        return ""
    tema = _tema_de_la_pregunta(t)
    if len(tema) < 4:
        return ""
    # "dime cuanto queda para cenar" no tiene que buscarse en los apuntes:
    # tiene que salir un tema de verdad, no cualquier frase de dos palabras.
    if not _APUNTES_TEMAS.search(tema):
        return ""
    try:
        from integrations import campus_apuntes as ap
        if not ap.buscar(tema, limite=1):
            return ""
        return ap.respuesta(tema)
    except Exception as e:
        log.debug("apuntes atajo: %s", e)
    return ""


_RUTA_FICHERO = re.compile(
    r"[\"']?((?:[A-Za-z]:\\|\\\\)[^\"'<>|*?\r\n]{2,240}?\.[A-Za-z0-9]{1,8})[\"']?")
_ANALIZAR_RE = re.compile(
    r"\b(analiza(me|r)?|analizar|estudiar|examina(me|r)?|revisar|revisa(me|r)?|"
    r"resume(me|r)?|resumir|leer|leeme|de\s+que\s+va|de\s+que\s+trata|"
    r"que\s+tiene|que\s+hay\s+en|que\s+hay\s+dentro|informaci[óo]n\s+de|"
    r"dime\s+de\s+que\s+va|mira\s+este|mira\s+esta)\b", re.I)


def _fichero_que_se_pide(cleaned: str) -> str:
    """La ruta de un archivo dentro de lo que ha escrito el señor."""
    m = _RUTA_FICHERO.search(cleaned)
    if m and os.path.exists(os.path.expandvars(m.group(1))):
        return os.path.expandvars(m.group(1))
    return ""


def _atajo_archivo(cleaned: str) -> str:
    """«analiza C:\\...\\notas.pdf» o «de qué va este C:\\...\\notas.docx».

    Lo primero se responde sin modelo: se lee el archivo y se dice qué es.
    """
    ruta = _fichero_que_se_pide(cleaned)
    if not ruta:
        return ""
    if not (_ANALIZAR_RE.search(cleaned) or _sin_tildes(cleaned).strip() == ""):
        return ""
    try:
        import file_analysis as _fa
        if _sin_tildes(cleaned).strip() == "":
            return _fa.respuesta_rapida(ruta)
        return _fa.respuesta_rapida(ruta)
    except Exception as e:
        log.debug("atajo archivo: %s", e)
    return ""


def _corto_si_se_paso(cleaned: str, se_agot_o: bool) -> str:
    """Respuesta corta cuando no da tiempo a pensar. Útil, no un silencio.

    Si el tema es de sus cursos, se le da el pasaje de los apuntes, que está
    leído y es inmediato. Si no, se dice la verdad y se ofrece el atajo.
    """
    try:
        from intentos import clasificar
        info = clasificar(cleaned)
        if info.get("es_tema_campus"):
            from integrations import campus_apuntes as ap
            tema = _tema_de_la_pregunta(_sin_tildes(cleaned)) or cleaned
            r = ap.respuesta(tema)
            if r and "No encuentro" not in r:
                return r
    except Exception as e:
        log.debug("corto si se paso: %s", e)
    if se_agot_o:
        return ("Tardaba demasiado en darle vueltas, señor, y no le hago "
                "esperar más. Si quiere, se lo resumo en pocas líneas: "
                "dígame «rápido» y va al grano.")
    return ("Perdón, señor, no me ha salido la respuesta. ¿Me lo repite "
            "con otra forma?")


def _atajo_maquinas(cleaned: str) -> str:
    """Máquinas virtuales: las que más se usan, y por eso las más rápidas.

    Habla como hables: «abre su punto 26» es «userver 26», y JARVIS lo
    entiende por el número. Si hay dos que encajan, pregunta cuál en vez de
    encender la que no es.
    """
    t = _sin_tildes(cleaned)
    try:
        from integrations import vms as _vms
    except Exception as e:
        log.debug("vms: %s", e)
        return ""

    # 1) Listarlas.
    if re.search(r"\b(cuales|que|listar|lista|mis|ten|tienes|hay|"
                 r"dime|ensename|muestrame)\b", t) and re.search(
                     r"\b(maquinas|machine|vms|virtualbox|equipos?|"
                     r"cuales son)\b", t):
        return _vms.resumen(preguntar=True)
    if re.search(r"^\s*(maquinas|que\s+maquinas|listar\s+maquinas|"
                 r"dime\s+las\s+maquinas|que\s+tengo\s+de\s+maquinas)\s*$", t):
        return _vms.resumen()

    # 2) Encender / abrir una.
    _m_abrir = re.search(r"\b(abre|abrir|abreme|enciende|encender|prende|"
                         r"arranca|inicia|levanta|poner en marcha)\b", t)
    _m_parar = re.search(r"\b(apaga|apagar|para|parar|deten|detiene|"
                         r"apagala|sacala)\b", t)
    _nombre = _vms.buscar(t)
    if not _nombre.get("nombre") and not _nombre.get("candidatos"):
        _nombre = _vms.buscar(re.sub(r"\b(abre|abrir|abreme|enciende|encender|"
                                     r"prende|arranca|inicia|levanta|"
                                     r"la|el|los|las|de|del|por|favor)\b",
                                     " ", t))
    if _nombre.get("candidatos") and (_m_abrir or _m_parar):
        return (f"Veo dos que pueden ser, señor: "
                f"{' o '.join(_nombre['candidatos'][:3])}. ¿Cuál?")
    if not _nombre.get("nombre"):
        return ""
    if _m_parar and not _m_abrir:
        return _vms.apagar(_nombre["nombre"])
    if not _m_abrir:
        # Sin verbo: si dijo el nombre solo, se enciende. Es lo que espera
        # el señor, y si solo preguntaba por su estado, se le contesta.
        if re.search(r"\b(encendida|apagada|estado|como\s+va|que\s+tal)\b", t):
            return _vms.estado(_nombre["nombre"])
        _m_abrir = True

    # 3) Apagarla o pausarla.
    if re.search(r"\b(pausa|pausar|pausala|suspende|suspender)\b", t):
        return _vms.pausar(_nombre["nombre"])
    return _vms.encender(_nombre["nombre"])


def _reloj_de_espera(segundos: float = 0.0) -> float:
    """Cuánto puede tardar este mensaje. Lo urgentísimo, nada: eso ya se
    resolvió con un atajo. Lo que hay que pensar, con techo: 25 segundos para
    analizar y 15 para charlar. Nunca se pasa de un minuto, y de hecho no.
    """
    if segundos:
        return float(segundos)
    try:
        tipo = _intento(_ultimo_texto[0]).get("tipo", "charla")
    except Exception:
        tipo = "charla"
    if tipo == "analizar":
        return 25.0
    if tipo == "charla":
        return 15.0
    return 20.0


_ultimo_texto = [""]


def _intento(texto: str) -> dict:
    """Mira el mensaje al instante. Microsegundos, sin red."""
    try:
        from intentos import clasificar
        return clasificar(texto)
    except Exception as e:
        log.debug("intentos: %s", e)
        return {"tipo": "charla", "texto": texto, "ms": 0.0}


def _pregunta_vaga(cleaned: str) -> bool:
    """¿Es esto una pregunta que solo tiene sentido con lo que acabamos de decir?

    'y eso', 'explícamelo', '¿por qué?', 'más detalles'... son la mitad de una
    conversación normal, y son las que hacen que JARVIS se vaya de tema.

    Solo cuentan las frases hechas de relleno. 'abre marca' o 'qué máquinas
    hay' son cortas, pero tienen tema propio y no deben anclar nada.
    """
    t = _sin_tildes(cleaned).strip(" ?!.")
    if not t:
        return False
    # Si la frase EMPIEZA con una orden o una pregunta con tema propio, no es
    # vaga. Solo se mira el principio: 'y ese archivo que' habla del hilo, pero
    # 'abre el archivo' es una orden nueva.
    if re.match(r"^\s*(abre|abrir|abreme|enciende|apaga|cierra|copia|mueve|"
                r"borrar|borra|busca|buscame|encuentra|instala|descarga|"
                r"ejecuta|corre|analiza|resume|hola|gracias|"
                r"adi[óo]s|buenos\s+dias|buenas\s+noches|saluda|"
                r"pon|ponme|graba|captura|teclea|escribe)\b", t):
        return False
    if _APUNTES_TEMAS.search(t):
        return False
    # 'que maquinas hay', 'que notas tengo'...
    if re.match(r"^\s*(?:dime\s+)?(?:que|cuales|cuantas|cuantos)\s+"
                r"(?:hay|m[áa]quinas?|vms?|notas|cursos|materias|"
                r"apuntes|pendientes?|tareas?|ficheros?)\b", t):
        return False
    relleno = (
        r"^(?:y|e|mas|bueno|vale|ok|entonces|luego|despues|ahora|ya|pues)$",
        r"^(?:y\s+)?(?:que|por\s+que|para\s+que|cual\s+es|como\s+asi|"
        r"y\s+como|y\s+si|y\s+luego|y\s+ahora|y\s+al\s+reves|y\s+el|"
        r"y\s+ese|y\s+esa|y\s+esto)$",
        r"^(?:explica(me)?|explicamelo|explicameesto|cuentame|cuentame\s+eso|"
        r"mas\s+detalles|m\s+as\s+detalles|detalles|el\s+resto|lo\s+demas|"
        r"mas\s+cosas|que\s+mas|sigue|continua|ya|sabia\s+mas|"
        r"y\s+que|y\s+cual|y\s+eso|eso\s+es|esta\s+bien|me\s+explicas)$",
        r"\b(eso|ese|esta|este|eso\s+mismo|lo\s+mismo|el\s+mismo|la\s+misma|"
        r"anterior|antes|antesterior|lo\s+anterior|lo\s+de\s+antes|"
        r"lo\s+que\s+(?:has\s+dicho|dijiste|me\s+dijiste)|lo\s+que\s+hemos|"
        r"lo\s+que\s+vimos)\b",
        r"^(?:y|e)\s+(?:el|la|los|las|ese|esa|este|esta|eso)?\s*$",
    )
    for a in relleno:
        if re.search(a, t):
            return True
    # 'y luego que', 'y ese archivo que': relleno con una interrogación detrás.
    if re.match(r"^\s*(?:y|e|entonces|luego|mas|bueno|vale|pues|ok)?\s*"
                r"(?:[a-z]+\s+){0,2}?(que|si|como|donde|cuando|para\s+que|"
                r"de\s+que)\s*$", t):
        return True
    if re.match(r"^\s*(?:vale|bueno|ok)?\s*pero\s+(?:y|e)\s+"
                r"(si|que|cual|como)\s*$", t):
        return True
    if re.match(r"^\s*(?:cual\s+es|cual\s+era|que\s+es)\s+la\s+idea\s*$", t):
        return True
    return False


def _ancla_del_hilo(cleaned: str) -> str:
    """Recuerda en una línea de qué hablábamos, para las preguntas vagas."""
    if not _pregunta_vaga(cleaned):
        return ""
    try:
        recientes = db.get_recent_messages(12)
    except Exception:
        return ""
    ultimo_usuario = ""
    for m in reversed(recientes):
        if m.get("role") == "user":
            ultimo_usuario = (m.get("content") or "").strip()
            break
    if not ultimo_usuario or len(ultimo_usuario) < 3:
        return ""
    return (f"HILO DE LA CONVERSACIÓN: lo último que dijo el señor fue "
            f"«{ultimo_usuario[:220]}». Si su pregunta es vaga ('eso', 'por "
            f"qué', 'explícamelo'), es sobre eso: sigue el hilo y no cambies "
            f"de tema.\n")


def _last_page_context_is_campus() -> bool:
    """¿La 'página/sesión' a la que nos referimos es la del ciclo?

    Mira los últimos mensajes (Win+J conserva el hilo): si acabamos de hablar
    del campus / página del ciclo, los antecedentes ("la página", "esa",
    "desbloquéala", "úntrale") resuelven al campus, NO a una máquina virtual.

    Se compara sin tildes: el micrófono y el reconocimiento de voz se las
    comen, y si no, el hilo no encajaba y la pregunta se iba a VirtualBox.
    """
    try:
        recent = db.get_recent_messages(limit=6)
        joined = _sin_tildes(" ".join((m.get("content") or "") for m in recent))
    except Exception:
        return False
    if re.search(r"(pagina\s+del\s+ciclo|pagina\s+de\s+aviesau|aviesau|"
                 r"campus|aula\s+virtual|moodle|mis\s+cursos|"
                 r"mis\s+asignaturas|entregas\s+del\s+ciclo|notas\s+del\s+ciclo)",
                 joined):
        # Que sea el campus, no una VM: si además se habla de una máquina
        # virtual en el mismo tramo, es mejor no dar por bueno el antecedente.
        if re.search(r"\b(maquina|vm|userver|virtualbox)\b", joined):
            return False
        return True
    return False


_CLIMA_RE = re.compile(r"\b(clima|tiempo|temperatura|lluvia|llover|har[aá] calor|fr[ií]o|soleado)\b", re.I)
_NOTICIAS_RE = re.compile(r"\b(notici|novedad|nuev|actualidad|lanzamiento|anuncio)\w*\b", re.I)


def _inject_weather(msg: str) -> str:
    try:
        from integrations.weather import get_weather
        w = get_weather()
        unit = "°C" if str(Config.WEATHER_UNIT).lower() != "imperial" else "°F"
        txt = (f"DATO REAL DE CLIMA: {w.get('description')}, {w.get('temperature')}"
               f"{unit} (sensación {w.get('feels_like')}{unit}), humedad {w.get('humidity')}%.\n")
        return txt + msg
    except Exception as e:
        return f"(Nota: el clima no está disponible ahora: {e})\n" + msg


def _inject_news(msg: str) -> str:
    try:
        from integrations.web_intel import tech_news, search_news
        items = search_news("tecnologia IA", limit=3) or tech_news(limit=3)
        if items:
            txt = "DATOS REALES DE NOTICIAS RECIENTES:\n" + "\n".join(
                f"- {i['title']} ({i.get('url', '')})" for i in items[:3]) + "\n\n" + msg
            return txt
    except Exception as e:
        log.debug("news intent: %s", e)
    return msg


# --------------------------------------------------------------------------- #
# Mensajes entrantes
# --------------------------------------------------------------------------- #
def _respuesta_si_vacia(text: str) -> str:
    """Cuando el modelo no dice nada, se contesta algo útil y no se miente.

    Si la pregunta era vaga, se recuerda de qué hablábamos; si no, se pide
    que larepita. Callarse era lo peor: el señor ve un silencio y piensa que
    JARVIS no le escucha.
    """
    try:
        ultimo = ""
        for m in reversed(db.get_recent_messages(10)):
            if m.get("role") == "user":
                ultimo = (m.get("content") or "").strip()
                break
    except Exception:
        ultimo = ""
    if _pregunta_vaga(_sin_tildes(text)) and ultimo:
        return ("Perdón, señor, no me ha salido la respuesta. "
                f"Seguíamos con «{ultimo[:140]}»: ¿me lo repite con más "
                "detalle?")
    return ("Perdón, señor, no le he entendido bien esta vez. "
            "¿Me lo repite con otra forma?")


def _guardar_historia(user_text: str, reply: str) -> None:
    """Guarda el turno en el historial, y si no puede, NO se rompe nada.

    Antes el `db.add_message()` iba suelto. Si la base no escribía, la
    excepción subía por `handle_user_message` y se llevaba por delante la
    respuesta: el señor no veía nada y, según por dónde hubiera entrado el
    mensaje, el WebSocket, Discord, WhatsApp, la voz o las tareas
    programadas quedaban caídos.

    Es exactamente lo que pasa en Docker cuando se llena el volumen o el
    disco falla, y en un portátil casi nunca: por eso no se notaba. Perder el
    historial de una frase es molestia; perder la conversación entera es que
    JARVIS parezca muerto.

    Aparte: el mensaje del señor se guarda ANTES que la respuesta. Si el
    proceso se muere justo después, queda registrado lo que se preguntó.
    """
    try:
        db.add_message("user", user_text)
    except Exception as exc:  # noqa: BLE001
        log.error("no pude guardar tu mensaje en el historial: %s", exc)
    try:
        db.add_message("assistant", reply)
    except Exception as exc:  # noqa: BLE001
        log.error("no pude guardar mi respuesta en el historial: %s", exc)


def handle_user_message(text: str, input_mode: str = "text", ws: Optional[WebSocket] = None,
                        broadcast: bool = True) -> str:
    """Procesa un mensaje (texto o voz) y devuelve la respuesta a hablar/mostrar.

    broadcast=False → no emite assistant_reply (el llamante reparte la
    respuesta por su propio canal, p. ej. el puente de WhatsApp).
    """
    brain._conversation_count += 1
    # Si OpenCode está esperando una decisión (permiso o pregunta), el mensaje
    # puede ser la respuesta. Solo se acepta si encaja con una opción o si el
    # señor lo pide con "responde:"; si no, sigue su camino normal.
    try:
        from orchestrator import inbox as _dev_inbox
        directa = _dev_inbox.responder_desde_chat(text)
    except Exception as _e:
        log.debug("respuesta de desarrollo: %s", _e)
        directa = None
    if directa:
        _guardar_historia(text, directa)
        if broadcast:
            brain.emit({"type": "assistant_reply", "text": directa})
        return directa
    try:
        reply = _handle(text, input_mode)
    except Exception as e:
        log.exception("handle_user_message")
        try:
            from memory import selfheal as _sh
            _sh.report_error("brain", e, text)
        except Exception:
            pass
        reply = f"Ha fallado algo en mis sistemas: {e}. Sigo operativo, señor."
    # Si el modelo devuelve una respuesta vacía, el señor se queda sin ver
    # nada y parece que JARVIS ignora la pregunta. Antes pasaba.
    if not (reply or "").strip():
        reply = _respuesta_si_vacia(text)
    _guardar_historia(text, reply)
    # La respuesta sale ANTES de indexar memoria semántica (los embeddings tardan
    # varios segundos y no deben retrasar lo que el usuario ve).
    # Se emite SIEMPRE, también por voz: antes, con voz puesta, la condicion
    # Ese era el motivo. Arriba dice (no hay voz O es texto): con voz puesta
    # contestaba de palabra y en pantalla no aparecía nada. Para el señor era
    # como si JARVIS ignorase lo que preguntaba.
    # contestaba de palabra y en pantalla no aparecía nada. Para el señor era
    # como si JARVIS ignorase lo que preguntaba.
    if broadcast:
        brain.emit({"type": "assistant_reply", "text": reply})

    def _index():
        try:
            semantic_memory.add("user", text)
            semantic_memory.add("assistant", reply)
        except Exception as e:
            log.debug("semantic index: %s", e)
        # Memoria persistente. Va en el MISMO hilo y DESPUES de lo de arriba:
        # el_embedding de Qdrant tarda, y si la memoria persistente fuera
        # delante, el señor esperaria mas por algo que no ve.
        #
        # Solo se guarda el mensaje del senor, no la respuesta de JARVIS:
        # guardar los dos mete en la memoria las frases del propio asistente,
        # y luego el modelo se recuerda a si mismo. decidir() es el manager y
        # es restrictivo a proposito; remember() nunca lanza.
        if Config.MEMORY_ENABLED:
            try:
                from memory.manager import recordar
                r = recordar(text, reply)
                if r.get("guardado"):
                    log.info("[MEMORY] Recuerdo anadido tras la conversacion")
            except Exception as e:
                log.debug("memoria guardar: %s", e)

    threading.Thread(target=_index, daemon=True, name="sem-index").start()

    return reply


def _handle(text: str, input_mode: str) -> str:
    cleaned = text.strip().strip(".,!¿?")
    # Se apunta el mensaje para que el reloj del modelo sepa cuánto puede
    # permitirse, según lo que sea el mensaje.
    _ultimo_texto[0] = cleaned
    _proj_ctx = ""  # contexto de proyecto para el modelo (no retorna)

    # --- Modo Despertar bajo demanda ---
    if _WAKE_UP_RE.search(cleaned) and not re.search(r"\btraduce\b", cleaned, re.I):
        brain.emit({"type": "action_status", "label": "Protocolo matutino",
                    "state": "running"})
        if input_mode in ("discord", "discord-voice", "whatsapp"):
            # El llamante ejecuta el ritual inline (canal/voz) con su speak_fn;
            # aquí solo acuse + historial, sin runner duplicado.
            return "De inmediato, señor. Le preparo la mañana…"
        return _launch_wake_up()
    # --- Protocolos del señor (automatizaciones con disparador) ---
    try:
        from memory import protocols as _prot
        _proto = _prot.match(cleaned)
    except Exception:
        _proto = None
    if _proto:
        _outs = []
        try:
            brain.emit({"type": "action_status", "label": _proto.get("name", "Protocolo"),
                        "state": "running"})
        except Exception:
            pass
        for _a in (_proto.get("actions") or [])[:8]:
            try:
                _tr = _run_tool_sync(_a.get("tool", ""), _a.get("params", {}) or {})
                if _tr.get("status") == "success":
                    _outs.append(_tool_result_to_frase(_a.get("tool", ""), _tr))
                else:
                    _outs.append(f"{_a.get('tool')}: {_tr.get('message', 'falló')}")
            except Exception as e:
                _outs.append(f"{_a.get('tool', '?')}: {e}")
                break
        try:
            brain.emit({"type": "action_status", "label": _proto.get("name", "Protocolo"),
                        "state": "done"})
        except Exception:
            pass
        _say = (_proto.get("say") or "").strip()
        _body = " ".join(o for o in _outs if o).strip()
        _head = f"Protocolo {_proto.get('name')}, señor. " if _say or _body else ""
        return f"{_head}{_say + ' ' if _say else ''}{_body}".strip() or \
            f"Protocolo {_proto.get('name')} ejecutado, señor."
    # Wake-up con apóstrofe
    if re.search(r"\b(hola|buenas)\b", cleaned, re.I) and len(cleaned) < 20:
        return "Señor. Todos los sistemas operativos. ¿En qué puedo servirle?"

    # --- Visión de ventana concreta ---
    win_target = _window_target(cleaned)
    if win_target is not None:
        from vision.analyzer import analyze_window
        return analyze_window(win_target)

    # --- Visión de pantalla ---
    if _SCREEN_OFF_RE.search(cleaned):
        brain.proactive.pause_screen_analysis()
        if brain.screen_watcher:
            brain.screen_watcher.stop()
        return "Entendido, señor. Dejo de observar su pantalla."
    if _SCREEN_ON_RE.search(cleaned):
        brain.proactive.resume_screen_analysis()
        if brain.screen_watcher:
            brain.screen_watcher.start()
        return "De acuerdo. Vuelvo a vigilar su pantalla, señor."
    if _SCREEN_NEW_RE.search(cleaned) and not _CAM_WORD_RE.search(cleaned):
        from vision.analyzer import analyze_screen
        return analyze_screen(router=brain.router, force_capture=True)

    # --- Cámara web (solo alterna la vista en el overlay Win+J) ---
    if _CAM_OFF_RE.search(cleaned):
        brain.emit({"type": "camera_mode", "state": "off"})
        return "Cámara desactivada. Vuelvo a la pizarra completa, señor."    # --- Protocolo cámara: mirar, identificar el objeto y proponer acciones ---
    _m_camobj = re.search(
        r"\b(mira por la c[áa]mara|activa la c[áa]mara y mira|dime qu[ée] es (esto|eso|lo que ves|lo que se ve)|"
        r"qu[ée] objeto es|identifica (esto|eso|lo que ves)|qu[ée] cosa (es|tengo) +(aqu[íi]|delante|a la vista))\b",
        cleaned, re.I)
    if _m_camobj:
        _CAMOBJ_PROMPT = (
            "Analiza esta imagen de la cámara web. 1) Identifica el objeto o cosa "
            "principal a la vista y dilo claro en una frase. 2) Propón 2-3 acciones "
            "útiles con este análisis (leer su texto, buscar información, guardar "
            "nota, comparar precios, lo que encaje). En español, breve, con tu "
            "ironía habitual.")

        def _camobj_run():
            _request_camera_frame(_CAMOBJ_PROMPT, warmup_sec=5.0)

        threading.Thread(target=_camobj_run, daemon=True, name="cam-obj").start()
        return ("Abro la cámara y miro, señor. Enseguida le digo qué objeto "
                "tiene a la vista y qué hacemos con ello. Paciencia de mayordomo.")
    if _CAM_ANALYZE_RE.search(cleaned):
        # Pedirle al overlay un frame de la webcam y analizarlo con visión
        # (con watchdog: si no llega, avisa y autocorrige en vez de colgarse).
        _request_camera_frame(cleaned, warmup_sec=4.0)
        return "Analizando, señor…"
    if _CAM_ON_RE.search(cleaned):
        brain.emit({"type": "camera_mode", "state": "on"})
        return "Cámara activada, señor. Muéstreme lo que desee que analice."
    if _GEST_OFF_RE.search(cleaned):
        brain.emit({"type": "gestures", "state": "off"})
        return "Apago el control de gestos, señor."
    if _GEST_ON_RE.search(cleaned):
        brain.emit({"type": "gestures", "state": "on"})
        return "Enciendo el control de gestos, señor. Si la cámara no abre, se lo digo."

    # --- Nextcloud: indexar bajo pedido o estado del índice ---
    _m_nc_idx = re.search(
        r"\b(indexa|reindexa|indexado|indexada|actualiza el [íi]ndice)\b[^.\n]{0,25}"
        r"\b(?:el\s+|la\s+)?(?:nextcloud|nube|cloud)\b|"
        r"\b(?:nextcloud|nube|cloud)\b[^.\n]{0,25}\b(indexa|reindexa|indexado|indexada)\b",
        cleaned, re.I)
    if _m_nc_idx:
        import library as _lib
        _verb = ((_m_nc_idx.group(1) or "") or (_m_nc_idx.group(2) or "")).lower()
        if _verb in ("indexado", "indexada"):
            _ns = _lib.nc_stats()
            return (f"Del Nextcloud tengo indexados, señor: {_ns['files']} archivos "
                    f"({_ns['chunks']} fragmentos).")
        _force = bool(re.search(r"\breindexa\b", cleaned, re.I))
        if _lib.index_nextcloud_background(emit=brain.emit, force=_force):
            return ("Indexando el Nextcloud en segundo plano, señor. "
                    "La nube no corre, flota; le aviso al terminar.")
        return ("Ya hay un indexado en curso, señor. "
                "Hasta yo necesito mi tiempo. A veces.")

    # --- Biblioteca local de apuntes (salta si habla del Nextcloud: eso lo atiende su bloque) ---
    _is_nc_msg = bool(re.search(r"\b(nextcloud|nube|cloud|explod\w*|nexcl\w*|necl\w*)\b", cleaned, re.I))
    m_use = _DOC_USE_RE.search(cleaned)
    if m_use and not _is_nc_msg:
        import library as _lib
        cand = m_use.group(1).strip().strip("\"'")
        if not os.path.isdir(os.path.abspath(os.path.expandvars(cand))):
            return f"No veo esa carpeta, señor: {cand}. Dígame la ruta completa."
        roots = _lib.set_roots(_lib.get_roots() + [cand])
        _lib.index_in_background(roots, emit=brain.emit)
        return (f"De acuerdo, señor. Usaré {cand} para sus apuntes. "
                f"Indexando en segundo plano ({len(roots)} carpeta(s) en total)…")
    if _DOC_FORGET_RE.search(cleaned) and not _is_nc_msg:
        import library as _lib
        _lib.forget_all()
        return "Olvidadas las carpetas y el índice, señor."
    if _DOC_REINDEX_RE.search(cleaned) and not _is_nc_msg:
        import library as _lib
        if not _lib.get_roots():
            return "No tengo carpetas que reindexar, señor. Dígame cuál usar."
        _lib.index_in_background(force=True, emit=brain.emit)
        return "Reindexando sus apuntes en segundo plano, señor."
    if _DOC_STATUS_RE.search(cleaned) and not _is_nc_msg:
        import library as _lib
        st = _lib.stats()
        if not st["roots"]:
            return ("Sin carpetas de apuntes, señor. Dígame «usa la carpeta …» "
                    "con la ruta completa y la indexo.")
        try:
            _ns = _lib.nc_stats()
        except Exception:
            _ns = {"files": 0, "chunks": 0}
        _local_files = max(0, st["files"] - _ns["files"])
        detail = ", ".join(st["roots"][:4])
        more = "" if len(st["roots"]) <= 4 else f" (+{len(st['roots']) - 4} más)"
        return (f"Apuntes: {_local_files} archivos en: {detail}{more}. "
                f"Nextcloud: {_ns['files']} archivos ({_ns['chunks']} fragmentos).")

# --- Info de la Raspberry: IP / hostname (determinista, sin LLM) ---
    _RASPI_INFO_RE = re.compile(
        r"\b(?:la\s+)?(?:dime|cu[áa]l|cu[áa]les|qu[ée]|dame|s[áa]came)\b[^.\n]{0,35}"
        r"\b(ip|direcci[óo]n|hostname|host\s*name|ostiname|nombre|nombre\s+del\s+equipo|nombre\s+de\s+la\s+m[aá]quina)\b"
        r"[^.\n]{0,25}\b(raspberry|raspi|r[aá]sp\w*|frambuesa)\b|"
        r"\b(raspberry|raspi|r[aá]sp\w*|frambuesa)\b[^.\n]{0,25}"
        r"\b(ip|direcci[óo]n|hostname|host\s*name|ostiname|nombre|nombre\s+del\s+equipo|nombre\s+de\s+la\s+m[aá]quina)\b",
        re.I)
    m_raspi_info = _RASPI_INFO_RE.search(cleaned)
    if m_raspi_info:
        try:
            from tools.raspberry_tools import raspberry_exec_handler
        except Exception as e:
            return f"No pude consultar la Raspberry, señor: {e}. La tecnología, obediente hasta que deja de serlo."
        cmd = "hostname"
        if re.search(r"\b(ip|direcci[óo]n)\b", m_raspi_info.group(0), re.I):
            cmd = "hostname -I"
        try:
            res = raspberry_exec_handler({"command": cmd})
        except Exception as e:
            return f"No pude consultar la Raspberry, señor: {e}. La tecnología, obediente hasta que deja de serlo."
        err = (res.get("error") or "").strip()
        out = (res.get("output") or "").strip()
        if err and not out:
            return f"No pude consultar la Raspberry, señor: {err}. Misterios de la red."
        if not out:
            return "La Raspberry no reportó nada por SSH, señor. Misteriosa, la pequeña."
        if cmd == "hostname":
            return (f"El hostname de la Raspberry, señor: {out.splitlines()[0]}. "
                    "Nombre con pedigrí.")
        items = out.split()
        return (f"Las IP de la Raspberry, señor: {', '.join(items[:6])}"
                + ("…" if len(items) > 6 else "") + ". "
                "La máquina no esconde nada. Casi decepcionante.")

    # --- IP de una VM VirtualBox (determinista, sin LLM) ---
    _VM_IP_RE = re.compile(
        r"\b(?:dime|cu[áa]l|dame|s[áa]came|qu[ée])\b[^.\n]{0,20}"
        r"\b(?:la\s+)?ip\b[^.\n]{0,8}\bde\s+(?:la\s+|el\s+)?"
        r"(?!raspberry|raspi|r[aá]sp\w*|frambuesa|servidor|nube|nextcloud|cloud\b)"
        r"([\w .\-áéíóúñü]{2,40}?)(?:\?|[.,!]|$)",
        re.I)
    m_vm_ip = _VM_IP_RE.search(cleaned)
    if m_vm_ip and (m_vm_ip.group(1) or "").strip():
        _vm_name = m_vm_ip.group(1).strip().rstrip(".,!?")
        if _vm_name.lower() not in ("raspberry", "raspi", "pi", "servidor",
                                    "nube", "nextcloud", "cloud", "máquina", "maquina"):
            try:
                from tools.virtualbox_tools import vbox_ip_handler
                _vr = vbox_ip_handler({"name": _vm_name})
            except Exception as e:
                return f"No pude consultar la IP de {_vm_name}, señor: {e}"
            _vips = _vr.get("ips") or []
            if _vr.get("state") not in ("encendida", "running"):
                return (f"{_vr.get('vm', _vm_name)} está {_vr.get('state', 'apagada')}, "
                        f"señor: sin IP invitada. ¿La arranco?")
            if not _vips:
                return (f"{_vr.get('vm', _vm_name)} está encendida pero no reporta IP, "
                        f"señor (¿Guest Additions?).")
            return (f"La IP de {_vr.get('vm', _vm_name)}, señor: {', '.join(_vips)}. "
                    "Localizada.")

    # --- Árbol de archivos del PC: dónde está / indexa carpeta ---
    _m_where = re.search(
        r"(?:\b(?:dime|decime|d[áa]me|indica(?:me)?|busca(?:r|me)?|"
        r"encuentra(?:r|me)?|localiza(?:r|me)?|existe|abre(?:me)?)\s+"
        r"(?:d[óo]nde\s+(?:est[áa]|est[óo]n)\s+)?"
        r"|\bd[óo]nde\s+(?:est[áa]|est[óo]n)\s+)"
        r"(?:(?:la|el|los|las)\s+)?"
        r"(?:(?:ruta|directorio|carpeta|ubicaci[óo]n|path)\s+)?"
        r"(?:(?:del|de|para)\s+)?"
        r"(?:(?:el|la|los|las)\s+)?"
        r"(?:(?:archivo|fichero|documento)\s+)?"
        r"[\"']?([\w\u00C0-\u024F][\w\u00C0-\u024F .\-_()]*\.[A-Za-z0-9]{1,8})[\"']?",
        cleaned, re.I)
    if _m_where and (_m_where.group(1) or "").strip():
        # "marca.com" parece un fichero con extensión, pero es una web. Sin esta
        # comprobación, "abre marca.com" acababa en el índice de ficheros
        # diciendo que no lo encuentra en el equipo.
        _parece_web = False
        try:
            from integrations import atajos as _at2
            _parece_web = _at2.resolver(_m_where.group(1).strip()) is not None
        except Exception:
            pass
        if not _parece_web:
            try:
                from memory import filetree as _ft
                _hits = _ft.find_fast(_m_where.group(1).strip(), limit=4)
            except Exception as e:
                return f"No pude buscar en el índice, señor: {e}"
            if not _hits:
                return (f"No encuentro «{_m_where.group(1).strip()}» en su equipo, "
                        "señor. Dígame la carpeta o indexe la unidad completa.")
            if len(_hits) == 1:
                return f"Ahí lo tiene, señor: {_hits[0]}"
            return (f"Le he encontrado {len(_hits)} coincidencias, señor. "
                    f"La más probable: {_hits[0]}.")
    _m_idx = re.search(
        r"\b(indexa|indexar|rastrea|rastrear)\b[^.\n]{0,10}\b(?:la\s+carpeta\s+)?(.+)$",
        cleaned, re.I)
    if _m_idx and (_m_idx.group(2) or "").strip():
        try:
            from memory import filetree as _ft
            _roots = _ft.add_root(_m_idx.group(2).strip().strip("\"'"))
            _ft.reindex_background()
            return (f"Rastreando {_m_idx.group(2).strip()}, señor "
                    f"({len(_roots)} raíces en total). Enseguida lo tengo fichado.")
        except Exception as e:
            return f"No pude indexar eso, señor: {e}"

    # --- Memoria personal: recuerda / prefiere / qué recuerdas / olvida ---
    _m_mem = re.search(
        r"\brecuerda que\b\s+(.+)$|\bprefiero\b\s+(.+)$|\bmi\s+([\w .\-áéíóúñü]{2,30}?)\s+es\s+(.+)$",
        cleaned, re.I)
    if _m_mem:
        try:
            from memory import profile as _prof
            if _m_mem.group(1):
                _prof.add_note(_m_mem.group(1).strip())
                _profile_cache["t"] = 0.0
                return "Anotado, señor. Mi memoria es buena; mi discreción, mejor."
            if _m_mem.group(2):
                _prof.add_pref(_m_mem.group(2).strip())
                _profile_cache["t"] = 0.0
                return "Preferencia guardada, señor. Tomo nota de sus gustos."
            _prof.set_fact("mi " + _m_mem.group(3).strip().lower(),
                           _m_mem.group(4).strip())
            _profile_cache["t"] = 0.0
            return "Dato archivado, señor."
        except Exception as e:
            return f"No pude apuntarlo, señor: {e}"
    if re.search(r"\b(qu[ée] recuerdas|qu[ée] sabes)\b[^.\n]{0,15}\b(de m[íi])\b",
                 cleaned, re.I):
        try:
            from memory import profile as _prof
            _d = _prof._load()
            _bits = [f"{k}: {v}" for k, v in _d.get("facts", {}).items()]
            _bits += [f"prefiere {p}" for p in _d.get("prefs", [])]
            _bits += _d.get("notes", [])[-5:]
            if not _bits:
                return "Aún poco, señor: estamos empezando a conocernos."
            return "De usted sé esto, señor: " + "; ".join(_bits[:10]) + "."
        except Exception as e:
            return f"No pude mirar mi memoria, señor: {e}"
    _m_forget = re.search(r"\bolvida\b\s+(.+)$", cleaned, re.I)
    if _m_forget and "carpeta" not in cleaned.lower():
        try:
            from memory import profile as _prof
            _n = _prof.forget_match(_m_forget.group(1))
            _profile_cache["t"] = 0.0
            if _n:
                return f"Olvidados {_n} recuerdos, señor. Borrón y cuenta nueva."
            return "No encontré eso en mi memoria, señor."
        except Exception as e:
            return f"No pude olvidar, señor: {e}"

    # --- Proyectos: recordar y continuar (determinista + contexto al modelo) ---
    _m_proj_rem = re.search(
        r"\b(recuerda|apunta|guarda)\b[^.\n]{0,20}\b(el\s+proyecto|que\s+el\s+proyecto|proyecto)\s+"
        r"([\w .\-áéíóúñü]{2,40}?)\s+(est[áa] en|vive en|:)\s+(.+)$",
        cleaned, re.I)
    if _m_proj_rem:
        try:
            from memory import projects as _pj
            _saved = _pj.remember(_m_proj_rem.group(3), _m_proj_rem.group(5))
            return (f"Proyecto {_saved.get('name')} archivado, señor: {_saved.get('path', '')}. "
                    "No se me olvida. Casi nunca.")
        except Exception as e:
            return f"No pude apuntar el proyecto, señor: {e}"
    _m_proj_go = re.search(
        r"\b(contin[úu]a|retoma|sigue con|trabajemos en)\b[^.\n]{0,20}"
        r"\b(el\s+proyecto\s+|proyecto\s+|con\s+)?([\w .\-áéíóúñü]{2,40})",
        cleaned, re.I)
    if _m_proj_go and (_m_proj_go.group(3) or "").strip():
        try:
            from memory import projects as _pj
            _p = _pj.recall(_m_proj_go.group(3))
        except Exception:
            _p = None
        if _p and _p.get("path"):
            _pp = _p["path"]
            _tree, _gst = "", ""
            try:
                from tools.auto_tools import list_dir_handler as _ls
                _items = _ls({"path": _pp}).get("entries", [])[:15]
                _tree = ", ".join(_items)
            except Exception:
                pass
            try:
                from tools.git_tools import git_status_handler as _gs
                _gst = _gs({"path": _pp}).get("status", "")
            except Exception:
                pass
            _notes = (_p.get("notes", "") or "")[:400]
            _proj_ctx = (f"\n[Proyecto {_p.get('name')}: {_pp}. "
                         f"Notas: {_notes or '—'}. Archivos: {_tree or '?'}. "
                         f"Git: {_gst or '?'}]")
        else:
            return ("¿Qué proyecto, señor? Dígame «recuerda el proyecto X está en RUTA» "
                    "y lo archivamos. Mi memoria es buena, pero necesita datos.")

    # --- Tareas programadas (determinista) ---
    _m_task = re.search(
        r"\b(recu[ée]rdame|programa|crea una tarea)\b\s+(.+?)\s+"
        r"(a las\s+\d{1,2}:\d{2}|en\s+\d+\s*(?:h|m\b|minutos?|horas?)|"
        r"cada\s+\d+\s*(?:h|m\b|minutos?|horas?))\s*$",
        cleaned, re.I)
    if _m_task:
        try:
            from tools.auto_tools import schedule_task_handler as _st
            _r = _st({"name": _m_task.group(2).strip()[:50],
                      "message": _m_task.group(2).strip(),
                      "when": _m_task.group(3).strip()})
            return (f"Tarea programada para las {_r.get('at', '?')}"
                    f"{_r.get('repeat', '')}, señor. No se me olvida. Casi nunca.")
        except Exception as e:
            return f"No pude programarla, señor: {e}. Formatos: HH:MM, en 2h, cada 1h."
    if re.search(r"\b(qu[ée] tareas|tareas pendientes|mis tareas)\b", cleaned, re.I):
        try:
            from tools.auto_tools import list_tasks_handler as _lt
            _t = _lt({}).get("tasks", [])
            if not _t:
                return "Sin tareas pendientes, señor. Agenda despejada."
            return "Tareas, señor: " + "; ".join(_t[:6]) + "."
        except Exception as e:
            return f"No pude mirar la agenda, señor: {e}"

    # --- Multimedia: YouTube y playlists (determinista) ---
    _m_yt = re.search(
        r"\b(pon|reproduce|abre|busca)\b[^.\n]{0,10}(.+?)\s+en\s+youtube\s*$",
        cleaned, re.I)
    if _m_yt and (_m_yt.group(2) or "").strip():
        try:
            import webbrowser
            from urllib.parse import quote as _q
            webbrowser.open("https://www.youtube.com/results?search_query=" + _q(_m_yt.group(2).strip()))
            return (f"YouTube con {_m_yt.group(2).strip()}, señor. Buena elección. Probablemente.")
        except Exception as e:
            return f"No pude abrir YouTube, señor: {e}"
    _m_pl = re.search(
        r"\b(pon|reproduce)\b[^.\n]{0,10}\bmi\s+playlist\s+(?:de\s+)?([\w .\-áéíóúñü]{2,40})",
        cleaned, re.I)
    if _m_pl and (_m_pl.group(2) or "").strip():
        try:
            from tools.system_tools import spotify_play_name_handler as _sp
            _r = _sp({"name": _m_pl.group(2).strip(), "kind": "playlist"})
            return f"Sonando {_r.get('name', '')}, señor. Suba el volumen."
        except Exception as e:
            return (f"No pude, señor: {e} "
                    "(Spotify API sin configurar: Client ID/Secret).")

    # --- Modo juego: prepara el PC (determinista) ---
    _m_game = re.search(
        r"\b(prepara(r)? el pc para jugar|modo juego|vamos a jugar)\b(\s+a\s+([\w .\-áéíóúñü]{2,30}))?",
        cleaned, re.I)
    if _m_game:
        try:
            from tools.syshealth_tools import perf_advisor_handler as _pa
            from tools.auto_tools import gpu_status_handler as _gpu
            _p = _pa({})
            _g = _gpu({})
        except Exception as e:
            return f"No pude revisar recursos, señor: {e}"
        _gm = (_m_game.group(4) or "").strip() if _m_game.lastindex and _m_game.lastindex >= 4 else ""
        _launch = ""
        if _gm:
            try:
                from tools.auto_tools import launch_game_handler as _lg
                _ = _lg({"game": _gm})
                _launch = f" Lanzando {_gm} (Steam). Que gane el mejor (usted)."
            except Exception as e:
                _launch = f" No pude lanzar {_gm}, señor: {e}."
        return (f"PC listo para jugar, señor. CPU {_p.get('cpu_avg', '?')}%, "
                f"RAM {_p.get('ram_pct', '?')}%. GPU: {_g.get('gpu', '?')}. "
                f"Consejos: {'; '.join(_p.get('tips', []) or ['ninguno'])}.{_launch}")

    # --- Auditoría de seguridad (determinista, solo lectura) ---
    if re.search(r"\b(audita|auditor[íi]a)\b[^.\n]{0,20}\b(seguridad|sistema|red)\b|"
                 r"\bpuertos abiertos\b|\bconexiones\b[^.\n]{0,15}\b(red|sospechosas?|externas?)\b|"
                 r"\bprocesos sospechosos\b",
                 cleaned, re.I):
        try:
            from tools.syshealth_tools import sec_audit_handler as _sa
            _s = _sa({})
        except Exception as e:
            return f"No pude auditar, señor: {e}"
        _ext = "; ".join(_s.get("external_established", [])[:8]) or "ninguna externa"
        _sp2 = "; ".join(_s.get("suspicious_procs", [])[:5]) or "ninguno raro"
        _lp = len(_s.get("listening", []))
        return (f"Auditoría lista, señor. {_lp} puertos a escucha. "
                f"Conexiones externas: {_ext}. Procesos sospechosos: {_sp2}. "
                "Solo miro, no toco. De momento.")

    # --- Ficheros: descargar / mover (determinista) ---
    _m_dl = re.search(r"\bdescarga\b[^.\n]{0,10}(https?://\S+)", cleaned, re.I)
    if _m_dl:
        try:
            from tools.auto_tools import download_file_handler as _dl
            _r = _dl({"url": _m_dl.group(1).rstrip(".,!?")})
            return (f"Descargado, señor: {_r.get('path', '')} "
                    f"({_r.get('bytes', 0)} bytes). Botín asegurado.")
        except Exception as e:
            return f"No pude descargarlo, señor: {e}"
    _m_mv = re.search(
        r"\b(mueve|renombra|traslada)\b\s+(.+?)\s+\ba\s+(.+)$",
        cleaned, re.I)
    if _m_mv:
        try:
            from tools.auto_tools import move_file_handler as _mv
            _r = _mv({"src": _m_mv.group(2).strip(), "dst": _m_mv.group(3).strip()})
            return (f"Movido, señor: a {_r.get('to', '')}. Orden en el caos.")
        except Exception as e:
            return f"No pude moverlo, señor: {e}"

    # --- Traducir lo visible en pantalla/ventana (captura + visión) ---
    _m_trs = re.search(
        r"\btraduce\b[^.\n]{0,30}\b(lo que (hay|se ve|pone)|la pantalla|esta ventana|la ventana)\b[^.\n]{0,20}"
        r"(al\s+(ingl[ée]s|espa[ñn]ol|franc[ée]s|alem[áa]n|italiano|portugu[ée]s))?|"
        r"\b(qu[ée] pone|qu[ée] dice)\b[^.\n]{0,20}\b(en\s+la\s+pantalla|en\s+pantalla)\b[^.\n]{0,20}"
        r"\b(traduce|trad[úu]cemelo|traducido)\b",
        cleaned, re.I)
    if _m_trs:
        _tlang = "español"
        _m_tl = re.search(r"al\s+(ingl[ée]s|espa[ñn]ol|franc[ée]s|alem[áa]n|italiano|portugu[ée]s)\b",
                          cleaned, re.I)
        if _m_tl:
            _tlang = _m_tl.group(1).lower()
        try:
            from vision.analyzer import analyze_screen
            _res = analyze_screen(
                f"Lee TODO el texto visible en pantalla y tradúcelo al {_tlang}. "
                f"Responde solo con la traducción, y al final una línea con el contexto "
                f"(qué ventana/app es). En {_tlang}.")
        except Exception as e:
            return f"No pude mirar la pantalla, señor: {e}"
        return _res or "No veo nada legible en pantalla, señor."

    # --- Traducir al instante (offline ES-EN, online resto) ---
    _m_tr = re.search(
        r"\btraduce\b\s+(?:me\s+|esto\s+)?[\"«:]?\s*(?P<txt>.+?)\s*[\"»]?\s+"
        r"\bal\s+(?P<lang>ingl[ée]s|espa[ñn]ol|franc[ée]s|alem[áa]n|italiano|portugu[ée]s)\b|"
        r"\bc[óo]mo se dice\b\s+[\"«]?(?P<txt2>.+?)[\"»]?\s+en\s+(?P<lang2>ingl[ée]s|espa[ñn]ol)\b|"
        r"\btranslate\b\s+(?P<txt3>.+?)\s+to\s+(?P<lang3>english|spanish)\b",
        cleaned, re.I)
    if _m_tr:
        _txt = (_m_tr.group("txt") or _m_tr.group("txt2") or _m_tr.group("txt3") or "").strip().strip("\"«»").strip(": ").strip()
        _lang = ((_m_tr.group("lang") or _m_tr.group("lang2") or _m_tr.group("lang3")) or "").lower()
        _dst = {"inglés": "en", "ingles": "en", "english": "en", "español": "es",
                "espanol": "es", "spanish": "es", "francés": "fr", "frances": "fr",
                "alemán": "de", "aleman": "de", "italiano": "it",
                "portugués": "pt", "portugues": "pt"}.get(_lang, "en")
        _src = "en" if _dst == "es" else ("es" if _dst == "en" else "auto")
        if _txt:
            try:
                from tools.translate_tools import translate_text as _trx
                _r = _trx(_txt, _src, _dst)
                return f"{_r.get('text', '')} ({_r.get('via', '')}, señor.)"
            except Exception as e:
                return f"No pude traducirlo, señor: {e}"

    # --- Micrófono del PC (el audio local no llega por PortAudio: vía navegador) ---
    _m_mic = re.search(
        r"\b(esc[úu]chame|activa el micr[óo]fono|abre el micr[óo]fono|quiero hablarte|"
        r"hablamos por voz|usa el micro)\b",
        cleaned, re.I)
    if _m_mic:
        try:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8000/mic", new=2)
            return ("Le abro el micrófono en el navegador, señor: pulse Grabar, "
                    "hable y pulse Detener. Le transcribo y respondo allí mismo.")
        except Exception as e:
            return f"No pude abrir el micrófono, señor: {e}"

    # --- Aviso al móvil (push urgente que suena como teléfono) ---
    _m_mob = re.search(
        r"\b(av[íi]same|ll[áa]mame|notif[íi]came|m[áa]ndame un aviso)\b[^.\n]{0,15}"
        r"\b(al\s+m[óo]vil|al\s+tel[ée]fono)\b",
        cleaned, re.I)
    if _m_mob:
        try:
            from tools.auto_tools import notify_phone_handler as _np
            _np({"message": "JARVIS le reclama, señor. Acuda."})
            return ("Aviso enviado a su móvil, señor: sonará como un teléfono. "
                    "Si no suena, instale la app ntfy y suscríbase al tema.")
        except Exception as e:
            return f"No pude avisarle al móvil, señor: {e}"

    # --- Llámame por Discord (desde Win+J: entra donde esté el owner) ---
    if re.search(r"\b(ll[áa]mame|[úu]nete( conmigo| a la voz)?|entra conmigo|entra en voz|ven a la llamada)\b[^.\n]{0,20}\b(por\s+discord|en\s+discord|a\s+discord|a\s+la\s+llamada)?",
                 cleaned, re.I) and re.search(r"\b(discord|llamada|ll[áa]mame|[úu]nete|voz)\b", cleaned, re.I):
        _br = getattr(brain, "discord", None)
        if _br is None:
            return "Discord no está conectado, señor. Revise el puente."
        try:
            _loop = getattr(getattr(_br, "client", None), "loop", None)
            if _loop is None:
                return "El puente de Discord no tiene bucle, señor."
            _fut = asyncio.run_coroutine_threadsafe(_br.join_owner_channel(), _loop)
            _st = _fut.result(timeout=45)
        except Exception as e:
            return f"No pude unirme, señor: {e}"
        if _st == "joined":
            return "Voy para allá, señor. Ya estoy en el canal."
        if _st == "already":
            return "Ya estoy en el canal, señor."
        if _st == "no-channel":
            try:
                _fut2 = asyncio.run_coroutine_threadsafe(
                    _br.dm_owner("Señor, acuda: le reclamo. Entre a un canal de voz y me uno al instante."),
                    _loop)
                _dm = bool(_fut2.result(timeout=30))
            except Exception:
                _dm = False
            return ("No le veo en ningún canal de voz, señor: entre usted primero "
                    "y entro solo." + (" Le he mandado un toque por DM." if _dm else ""))
        return "No pude entrar al canal, señor."

    # --- Mirar la llamada de Discord (captura su ventana y la analiza) ---
    _m_callsee = re.search(
        r"\b(mira|mu[ée]strame?|describe|analiza|qu[ée] ves|qu[ée] se ve)\b[^.\n]{0,20}"
        r"\b(la\s+llamada|el\s+discord|en\s+discord|en\s+la\s+llamada)\b|"
        r"\b(la\s+llamada|mi\s+c[áa]mara|la\s+c[áa]mara)\b[^.\n]{0,20}"
        r"\b(qu[ée] ves|qu[ée] se ve|describe|analiza|mira)\b[^.\n]{0,20}"
        r"\b(llamada|discord)\b|"
        r"\b(la\s+c[áa]mara|mi\s+c[áa]mara)\b[^.\n]{0,20}"
        r"\b(llamada|discord)\b",
        cleaned, re.I)
    if _m_callsee:
        try:
            from vision.analyzer import analyze_window
            _res = analyze_window(
                "discord",
                "Mira esta ventana de Discord con una llamada en curso. Di quién está "
                "en cámara, qué se ve (personas, fondos, gestos, objetos) y el estado "
                "de la llamada. En español, con tu ironía habitual, en 2-4 frases.")
        except Exception as e:
            return f"No pude mirar la llamada, señor: {e}"
        return _res or "No veo la llamada, señor."

    # --- WhatsApp: abrir Web y leer mensajes visibles (determinista, sin LLM) ---
    _wa_open = re.search(
        r"\b(abre|abreme)\b[^.\n]{0,20}\b(whatsapp|wasap|wuasap)(\s+web)?\b",
        cleaned, re.I)
    if _wa_open:
        try:
            from integrations import whatsapp as _wapp
            _wapp.open_web()
            return ("WhatsApp Web abierto en su navegador, señor. "
                    "Dígame «lee mis whatsapp» y le cuento lo que veo. Sin cotillear… mucho.")
        except Exception as e:
            return f"No pude abrir WhatsApp Web, señor: {e}"
    _wa_read = re.search(
        r"\b(lee|leeme|dime|mu[ée]strame?|qu[ée])\b[^.\n]{0,25}"
        r"\b(mis\s+|los\s+)?(whatsapp|wasap|wuasap|mensajes de whatsapp|chats)\b|"
        r"\b(whatsapp|wasap)\b[^.\n]{0,25}\b(sin leer|nuevos|nuevas|hay|tengo)\b",
        cleaned, re.I)
    if _wa_read:
        try:
            from integrations import whatsapp as _wapp
            _r = _wapp.read_anywhere()
        except Exception as e:
            return f"No pude asomarme a WhatsApp, señor: {e}"
        _lines = [l for l in (_r.get("lines") or []) if l][:10]
        if not _lines:
            return ("No veo ningún WhatsApp abierto, señor. "
                    "Dígame «abre whatsapp web» y lo miramos. Ciego, pero solo de momento.")
        _src = "la app" if _r.get("source") == "app" else "WhatsApp Web"
        return (f"En {_src} veo, señor: " + " | ".join(_lines[:8])
                + ("…" if len(_lines) > 8 else ""))

    # --- Salud del PC: revisión, virus, updates, rendimiento (determinista) ---
    _m_syscheck = re.search(
        r"\b(revisa|revisi[óo]n|chequeo|chequea|mira si|comprueba)\b[^.\n]{0,25}"
        r"\b(mi\s+|el\s+)?(pc|equipo|ordenador|sistema|windows)\b[^.\n]{0,20}"
        r"\b(bien|errores|fallos|problemas|estado|completo|todo)\b|"
        r"\b(estado|chequeo|revisi[óo]n)\b[^.\n]{0,20}\b(del\s+)?(sistema|pc|equipo)\b",
        cleaned, re.I)
    if _m_syscheck:
        try:
            from tools.syshealth_tools import health_overview_handler as _hov
            _h = _hov({})
        except Exception as e:
            return f"No pude revisar el equipo, señor: {e}"
        _rt = "activada" if "True" in str(_h.get("defender", "")) else "a saber"
        _upd = str(_h.get("updates", "?")).strip()
        _ram = str(_h.get("ram", "?")).replace("free=", "").replace("total=", "de ")
        _errs = " ".join(str(_h.get("errors24h", "ninguno")).split())
        return (f"Revisado de arriba abajo, señor. Defender {_rt}, "
                f"updates pendientes: {_upd}. Discos: {_h.get('disks', '?')}. "
                f"RAM libre: {_ram}. "
                f"Errores 24h: {_errs}. "
                "En general, respira. Con algún que otro ronquido, pero respira.")
    _m_virus = re.search(
        r"\b(busca|hay|tienes|escanea|analiza|p[áa]same)\b[^.\n]{0,20}"
        r"\b(virus|malware|troyanos?|amenazas?|antivirus)\b|"
        r"\b(virus|malware)\b[^.\n]{0,20}\b(hay|tengo|tienes|escanea|analiza)\b",
        cleaned, re.I)
    if _m_virus:
        try:
            from tools.syshealth_tools import defender_threats_handler as _dth
            _t = _dth({})
        except Exception as e:
            return f"No pude consultar amenazas, señor: {e}"
        _th = (_t.get("threats") or "").strip()
        if _th and _th not in ("none", "") and not _th.startswith("ERR"):
            return (f"Malas noticias, señor: veo esto: {_th[:300]}. "
                    "Lo pongo en cuarentena si me lo ordena.")
        def _scan_bg():
            try:
                from tools.syshealth_tools import defender_scan_handler as _dsc
                _dsc({"type": "quick"})
                _t2 = _dth({})
                _th2 = (_t2.get("threats") or "").strip()
                if _th2 and not _th2.startswith("ERR"):
                    brain.emit({"type": "assistant_reply",
                                "text": f"Análisis completo, señor, y con hallazgos: {_th2[:300]}."})
                else:
                    brain.emit({"type": "assistant_reply",
                                "text": "Análisis completo, señor: limpio como una patena."})
            except Exception as e:
                try:
                    brain.emit({"type": "assistant_reply",
                                "text": f"El análisis tropezó, señor: {e}"})
                except Exception:
                    pass
        threading.Thread(target=_scan_bg, daemon=True, name="defender-scan").start()
        return ("Sin amenazas a la vista, señor. Lanzo un análisis rápido de "
                "Defender en segundo plano y le aviso al terminar. La paranoia, bien administrada, es una virtud.")
    _m_quar = re.search(
        r"\b(pon|mete|manda|desinfecta|elimina|limpia)\b[^.\n]{0,25}"
        r"\b(virus|malware|troyanos?|amenazas?)\b[^.\n]{0,15}"
        r"\b(cuarentena|desinfecta|elimina)\b|"
        r"\b(cuarentena)\b[^.\n]{0,25}\b(virus|malware|amenazas?)\b|"
        r"\bdesinfecta\b",
        cleaned, re.I)
    if _m_quar:
        try:
            from tools.syshealth_tools import defender_quarantine_handler as _dq
            _q = _dq({})
        except Exception as e:
            return f"No pude poner en cuarentena, señor: {e}"
        _found = _q.get("found") or []
        if not _found:
            return ("Nada que encerrar, señor: no hay amenazas detectadas. "
                    "La cárcel de virus, vacía. Como debe ser.")
        _paths = "; ".join([(f.get("paths") or f.get("id")) for f in _found[:5]])
        if _q.get("purged") and not _q.get("remaining"):
            return (f"Encerrados, señor: {_paths[:300]}. "
                    "Cuarentena completa. Que reflexionen sobre lo que han hecho.")
        return (f"Veo esto, señor: {_paths[:300]}. "
                f"Purgado: {'sí' if _q.get('purged') else 'a medias'}. "
                "Dígame si remato la faena.")
    _m_upd = re.search(
        r"\b(hay|tengo|tienes|lista|dime|mu[ée]strame?)\b[^.\n]{0,20}"
        r"\b(actualizaciones?|updates?)\b[^.\n]{0,20}\b(pendientes?|sin instalar|hay|tengo)?\b|"
        r"\b(actualizaciones?|updates?)\b[^.\n]{0,20}\b(pendientes?|sin instalar)\b",
        cleaned, re.I)
    if _m_upd:
        try:
            from tools.syshealth_tools import win_updates_handler as _wu
            _w = _wu({})
        except Exception as e:
            return f"No pude consultar updates, señor: {e}"
        _n = _w.get("count", 0)
        if not _n:
            return "Al día, señor. Ni un update pendiente. Casi sospechoso."
        _first = "; ".join((_w.get("updates", []) or [])[:4])
        return (f"{_n} pendientes, señor: {_first}"
                + ("…" if _n > 4 else "") + ". "
                "Si quiere, las instalamos (con su venia y un reinicio de por medio).")
    _m_perf = re.search(
        r"\b(rendimiento|c[óo]mo va|va lento|lento|optimiza|acelera|va mal|"
        r"qu[ée] le pasa)\b[^.\n]{0,25}\b(mi\s+|el\s+)?(pc|equipo|ordenador|windows|sistema)\b|"
        r"\b(pc|equipo)\b[^.\n]{0,20}\b(lento|va mal)\b",
        cleaned, re.I)
    if _m_perf:
        try:
            from tools.syshealth_tools import perf_advisor_handler as _pa
            _p = _pa({})
        except Exception as e:
            return f"No pude medir el rendimiento, señor: {e}"
        _tips = "; ".join(_p.get("tips", []) or ["ninguno: va fino, señor"])
        _top = "; ".join(str(_p.get("top_cpu", "?")).split())
        _dsk = "; ".join(str(_p.get("disks", "?")).split())
        return (f"CPU {_p.get('cpu_avg', '?')}%, RAM al {_p.get('ram_pct', '?')}%. "
                f"Top: {_top}. Discos: {_dsk}. "
                f"Consejos: {_tips}")

    # --- Autocorrección y auto-optimización (determinista) ---
    _m_appr = re.search(r"\b(apruebo|aprobar|aprueba|sí,? arregla|adelante|permiso concedido)\b"
                        r"(?:\s+(E\d+))?", cleaned, re.I)
    _m_deny = re.search(r"\b(deniego|denegar|denegado|no,? no lo arregles|no lo toques|permiso denegado)\b"
                        r"(?:\s+(E\d+))?", cleaned, re.I)
    if _m_appr or _m_deny:
        try:
            from memory import selfheal as _sh
            _iid = (_m_appr or _m_deny).group(2) or ""
            ok = _sh.resolve_approval(_iid, bool(_m_appr))
            if ok:
                return ("Permiso registrado, señor: procedo con el arreglo."
                        if _m_appr else "Entendido, señor: no toco nada.")
            return "No hay ningún arreglo pendiente, señor."
        except Exception as e:
            return f"No pude registrarlo, señor: {e}"
    if re.search(r"\b(arr[ée]glalo|arr[ée]glate|ponte a arreglarlo|repara eso|"
                 r"solucionalo|soluciónalo|autocorr[íi]gete|sigue intent[áa]ndolo|"
                 r"no pares hasta arreglarlo)\b", cleaned, re.I):
        try:
            from memory import selfheal as _sh
            _pend = [i for i in _sh.list_errors(50) if i.get("status") in ("pending", "escalated")]
            _sh.fix_now()
            if _pend:
                return ("Manos a la obra, señor: estoy arreglando "
                        f"{len(_pend)} asunto(s) pendiente(s) con OpenCode y no paro "
                        "hasta que funcione. Le aviso de cada avance.")
            return ("No veo nada pendiente, señor. Si algo falla, dígamelo "
                    "o pulse: todo queda registrado y me pongo a ello.")
        except Exception as e:
            return f"No pude arrancar la reparación, señor: {e}"
    if re.search(r"\b(qu[ée] errores tienes|estado de salud|auto rep[áa]rate|"
                 r"rev[íi]sate|chec?ueo de errores|informe de errores)\b", cleaned, re.I):
        try:
            from memory import selfheal as _sh
            _errs = _sh.list_errors(limit=6)
            if not _errs:
                return ("Sin errores registrados, señor. Impecable, como siempre. "
                        "Bueno, casi siempre.")
            _bits = []
            for _e in _errs[-4:]:
                _bits.append(f"{_e.get('source')}: {(_e.get('error') or '')[:80]} "
                             f"[{_e.get('status')}]")
            return ("Mis achaques, señor: " + " | ".join(_bits) + ". "
                    "El reparador ya está encima de los pendientes.")
        except Exception as e:
            return f"No pude mirar mis errores, señor: {e}"
    if re.search(r"\boptim[íi]zate\b|\boptimiza el sistema\b|\boptimizaci[óo]n completa\b", cleaned, re.I):
        try:
            from tools.syshealth_tools import auto_optimize_handler as _ao
            _r = _ao({})
            _o = _r.get("optimized", {})
            _bits = []
            if _o.get("db_mb") is not None:
                _bits.append(f"base {_o.get('db_mb')}MB")
            _rot = [k for k, v in _o.items() if v == "rotado"]
            _bits.append(f"logs {','.join(_rot) if _rot else 'en orden'}")
            if _o.get("temp"):
                _bits.append(f"temporales: {_o['temp']}")
            if _o.get("tts"):
                _bits.append("voz precalentada")
            return (f"Optimizado, señor: {'; '.join(_bits)}. "
                    "Sin tocar nada clave, como ordenó.")
        except Exception as e:
            return f"No pude optimizarme, señor: {e}"

    # --- VirusTotal explícito: va ANTES de leer la página ---
    # Antes, "analiza https://x con virustotal" acababa en la lectura de
    # páginas y contestaba un resumen del texto: la ruta web se comía la
    # petición y el análisis de verdad no llegaba a hacerse nunca.
    if re.search(r"\b(virus\s?total|virustotal)\b", cleaned, re.I):
        _r = _virustotal_rapido(cleaned)
        if _r:
            return _r

    # --- Página web: investigar NO abre nada. Solo abrir si lo pide expresamente ---
    _u = _extract_url(cleaned)
    if not _u and re.search(r"\b(esta|esa|el)\s+(p[aá]gina|web|sitio|enlace)\b", cleaned, re.I):
        _u = _last_url()
    if _u:
        _leer = re.search(r"\b(investiga\w*|lee\w*|leer|resum\w*|analiza\w*|"
                          r"explica\w*|cuenta\w*|estudia\w*|documenta\w*|"
                          r"qu[eé]\s+(dice|pone|hay|sal|tiene|explica)|"
                          r"contenido|informaci[óo]n|"
                          r"de qu[eé] (trata|habla)|busca(me)?\s+(en|dentro))\b",
                          cleaned, re.I)
        _abrir = re.search(r"\b(abre(r|me|la)?|abrir|visita(r|me)?|visitar|"
                           r"entra(r)? en|muestrame\s+en\s+el\s+navegador)\b", cleaned, re.I)
        if _leer:
            return _read_page(_u, abrir=bool(_abrir))
        if _abrir:
            try:
                import webbrowser as _wb
                _wb.open(_u)
                return f"Página abierta, señor: {_u}"
            except Exception as e:
                return f"No pude abrirla, señor: {e}"

    # --- Abrir una webapp DENTRO de la pizarra (Win+J), no en el navegador ---
    _m_app = re.match(r"^\s*(?:abre|abreme|abrir|lanza|l[aá]nzame|inicia|entra\s+en)\s+"
                      r"(?:la\s+|el\s+|una\s+|un\s+)?(.{2,40}?)\s*(?:en\s+mi\s+pizarra|"
                      r"en\s+la\s+pizarra|dentro)?\s*[.!?]?\s*$", cleaned, re.I)
    if _m_app:
        _wa = _webapp(_m_app.group(1))
        if _wa:
            _label, _url = _wa
            brain.emit({"type": "open_webapp", "url": _url, "label": _label})
            return (f"He abierto {_label} dentro de la pizarra, señor. "
                    "Si lo quiere en el navegador de verdad, me lo dice.")

    # --- Ruta de un archivo: cubierto por el bloque del árbol de archivos ---

    # --- Seguimiento de una investigación: "¿quiere que profundice?" ---
    # Solo cuentan las replies cortas y sin tema: "profundiza en la energía
    # nuclear" es una petición NUEVA, no un sí. Antes repetía la investigación
    # anterior y le contestaba sobre otro tema.
    if getattr(brain, "_pending_deep", None):
        if _DEEP_SI_RE.match(cleaned):
            _deep = brain._pending_deep
            brain._pending_deep = None
            _what_deep = _deep.get("urls") or []
            if re.search(r"\b(fuente|enlaces?|los enlaces|todo|enter[oa]s?|completo|entero)\b",
                         cleaned, re.I) and _what_deep:
                try:
                    from tools.web_tools import web_fetch_handler
                    _txt = "\n\n---\n\n".join(
                        (web_fetch_handler({"url": u, "timeout": 15}).get("content") or "")[:3000]
                        for u in _what_deep[:2])
                    return _research(_deep.get("topic", ""), deep=True) if len(_txt) < 400 \
                        else _summarize_deep(_deep.get("topic", ""), _txt, _what_deep)
                except Exception:
                    pass
            return _research(_deep.get("topic", ""), deep=True)
        if _DEEP_NO_RE.match(cleaned):
            brain._pending_deep = None
            return "Con eso me doy por servido, señor. Diga si quiere otra cosa."

    # --- Respuesta a una investigación pendiente: sí / no ---
    if brain._pending_scan and re.match(r"^\s*(s[ií]|si|claro|adelante|dale|"
                                        r"hazlo|s[ií] por favor|yes)\b", cleaned, re.I):
        _findings = brain._pending_scan
        brain._pending_scan = None
        return _apply_scan_fixes(_findings)
    if brain._pending_scan and re.match(r"^\s*(no|nop|nel|negativo|"
                                        r"no gracias|bestia|nah|"
                                        r"no lo arregles|dejalo)\b", cleaned, re.I):
        brain._pending_scan = None
        return ("Entendido, señor. No tocaré nada. El parte queda anotado "
                "por si decide más adelante.")

    # --- Barrido del equipo: "investiga/busca problemas/errores" ---
    if re.search(r"\b(investiga(r)?|busca(r)?|revisa(r)?|analiza(r)?|audita(r)?)\b",
                 cleaned, re.I) and \
            re.search(r"\b(problema(s)?|error(es)?|fallo(s)?|vulnerabilidad(es)?|"
                      r"virus|viruses|malware|seguridad|tu problema|problema de j?arvis|"
                      r"todo el (ordenador|pc|equipo|sistema)|el (ordenador|pc|equipo|sistema)|"
                      r"el sistema|este (ordenador|pc|equipo))\b", cleaned, re.I):
        return _run_system_scan(cleaned)

    # --- Búsqueda concreta: una consulta web, resultados inmediatos, sin LLM ---
    # Se distingue de "investiga/profundiza": una búsqueda simple devuelve
    # títulos, fragmentos y enlaces en una sola petición corta.
    _m_search = _SEARCH_WEB_RE.match(cleaned)
    if _m_search and not _RESEARCH_BUSCA_RE.match(cleaned) \
            and not re.search(r"\b(investiga|investigar|averigua|averiguar|documenta|documentar|"
                              r"estudia|estudiar|profundiza|profundizar|resume|resumen|explica|analiza)\b",
                              cleaned, re.I):
        _q = _m_search.group(1).strip(" .¡!¿?\"'")
        _q = re.sub(r"^(?:(?:por favor\s+)?(?:en\s+)?)?"
                    r"(?:un|el|la|los|las)\s+", "", _q, flags=re.I).strip()
        if _q and _q.lower() not in ("eso", "esto", "eso mismo", "una cosa", "algo"):
            try:
                from tools.web_tools import quick_web_search_handler
                _fast = quick_web_search_handler({"query": _q})
            except Exception as _e:
                log.debug("búsqueda rápida: %s", _e)
                _fast = {"results": [], "error": str(_e)}
            _results = _fast.get("results") or []
            if _results:
                _lines = []
                for _item in _results[:3]:
                    _title = (_item.get("title") or "Resultado web").strip()
                    _snippet = (_item.get("snippet") or "").strip()
                    _url = (_item.get("url") or "").strip()
                    _line = f"• [{_title}]({_url})" if _url else f"• {_title}"
                    if _snippet:
                        _line += f" — {_snippet[:260]}"
                    _lines.append(_line)
                return "Resultados para «" + _q + "», señor:\n" + "\n".join(_lines)
            return (f"No he conseguido resultados rápidos para «{_q}», señor. "
                    "La búsqueda ha agotado su plazo corto; pruebe otra vez o pídame que investigue el tema.")

    # --- Investigar: resumen breve y oferta de profundizar (sin modelo) ---
    # Va DESPUÉS del barrido del equipo a propósito: "investiga el equipo"
    # es el análisis del ordenador, no una búsqueda en internet.
    _m_res = _RESEARCH_RE.match(cleaned)
    if _m_res and not _NO_ES_APP_RE.search(_m_res.group(1)) \
            and not _NO_ES_EQUIPO_RE.search(_m_res.group(1)):
        return _research(_m_res.group(1))
    # Las peticiones explícitas de información/resumen siguen en la vía de investigación.
    _m_res2 = _RESEARCH_BUSCA_RE.match(cleaned)
    if _m_res2 and _m_res2.group(1).strip() and not _NO_ES_APP_RE.search(_m_res2.group(1)) \
            and not _NO_ES_EQUIPO_RE.search(_m_res2.group(1)):
        return _research(_m_res2.group(1), quick=True)

    # --- Si hay un arreglo en marcha, pregunta antes de seguir con otra cosa ---
    _m_stop = re.search(r"\b(s[ií]|claro|adelante|dale|continue|contin[uú]a|"
                        r"hazlo as[ií]|no pares)\b", cleaned, re.I)
    _m_keep = re.search(r"\b(no|dejalo|d[eé]jalo|sigue|contin[uú]a|termina|"
                        r"acaba|estoy bien as[ií])\b", cleaned, re.I)
    try:
        from memory import selfheal as _sh_busy
        if _sh_busy.busy() and (_m_stop or _m_keep):
            if _m_stop and not _m_keep:
                _n = _sh_busy.stop_active("parada a petición del señor")
                return (f"Paro lo que estaba reparando, señor ({_n} arreglo"
                        f"{'s' if _n != 1 else ''}). Diga cuándo seguimos.")
            return "Le dejo terminar la reparación, señor. No le estorba para hablar."
    except Exception:
        pass

    # --- Puesto de mando: la página de configuración ---
    if re.search(r"\b(configuraci[óo]n|puesto de mando|ajustes|preferencias|"
                 r"p[aá]gina de configuraci[óo]n|panel de control)\b", cleaned, re.I) \
            and re.search(r"\b(abre|abrir|abrela|mu[eé]strame|ens[eé]ñame|quiero|"
                          r"ll[eé]vame|vete|ir|panel|p[aá]gina)\b", cleaned, re.I):
        try:
            import webbrowser as _wb
            _wb.open("http://127.0.0.1:8000/config")
            return ("Abro el puesto de mando, señor. Lo tiene en " +
                    "http://127.0.0.1:8000/config si prefiere copiarlo.")
        except Exception as e:
            return f"No pude abrir el panel, señor: {e}"

    # --- Nextcloud real (determinista, sin LLM): raíz, subcarpeta o archivo ---
    _nc_mentioned = re.search(r"\b(nextcloud|nube|cloud|explod\w*|nexcl\w*|necl\w*)\b", cleaned, re.I)
    _nc_interest = re.search(
        r"\b(dime|cu[áa]les?|qu[ée]|dame|lista|mu[ée]strame?|abre|abrime|lee|leeme|tienes|tiene|hay|"
        r"enseña|mira|carpetas?|archivos?|archivo|documentos?|contenido|fotos?|todo)\b",
        cleaned, re.I)
    # Pregunta de CONTENIDO ("sobre X", "háblame de…"): que la responda el modelo con el RAG.
    _nc_content_q = re.search(
        r"\b(sobre|acerca de|h[áa]blame|cu[ée]ntame|resume|resumen|informaci[óo]n|"
        r"qu[ée] sabes|qu[ée] pone|qu[ée] dice|explica|expl[íi]came)\b",
        cleaned, re.I)
    if _nc_mentioned and _nc_interest and not _nc_content_q:
        try:
            from tools.raspberry_tools import nextcloud_list_handler, nextcloud_read_handler
            _nc_root = nextcloud_list_handler({"path": ""})
        except Exception as e:
            return f"No pude acceder al Nextcloud, señor: {e}"
        _nc_err = (_nc_root.get("error") or "").strip()
        _nc_items = _nc_root.get("items") or _nc_root.get("list") or []
        if _nc_err and not _nc_items:
            return f"No pude consultar el Nextcloud, señor: {_nc_err}"
        _nc_folders = [i.get("name") for i in _nc_items if i.get("type") == "folder"]
        _nc_files = [i.get("name") for i in _nc_items if i.get("type") != "folder"]
        _nc_all = [(f or "") for f in (_nc_folders + _nc_files)]
        _nc_low = {a.strip().lower(): a for a in _nc_all if a}
        from urllib.parse import unquote as _nc_unq

        def _nc_dn(n):
            try:
                return _nc_unq(n or "")
            except Exception:
                return n or ""
        # 1) ¿pide LEER un archivo? "lee/abre el archivo Y (de la carpeta Z)"
        _m_read = re.search(
            r"(?:lee|leeme|abre|abrime|mu[ée]strame? el contenido|dame el contenido)\b[^.\n]{0,10}"
            r"(?:archivo|documento|fichero|pdf|texto)\s+([\w .\-áéíóúñü%]{2,60}?)"
            r"(?:\s+de\s+(?:la\s+carpeta\s+|carpeta\s+|el\s+)?([\w .\-áéíóúñü%]{2,40}?))?"
            r"(?:\s+de\s+(?:la\s+)?(?:nube|nextcloud|cloud)\b|[.,!?]+(?:\s|$)|$)",
            cleaned, re.I)
        if _m_read and (_m_read.group(1) or "").strip():
            _fname = _m_read.group(1).strip().rstrip(".,!?")
            _fdir = (_m_read.group(2) or "").strip().rstrip(".,!?")
            if _fdir.lower() in ("nube", "nextcloud", "cloud", "la nube"):
                _fdir = ""
            _rpath = f"{_fdir}/{_fname}" if _fdir else _fname
            try:
                _rr = nextcloud_read_handler({"path": _rpath})
            except Exception as e:
                return f"No pude leer {_rpath}, señor: {e}"
            _rerr = (_rr.get("error") or "").strip()
            _rcontent = (_rr.get("content") or _rr.get("text") or "").strip()
            if _rerr and not _rcontent:
                return f"No pude leer {_rpath}, señor: {_rerr}"
            if not _rcontent:
                return f"El archivo {_rpath} está vacío, señor."
            _rb = _rcontent.encode("latin-1", errors="ignore")
            _printable = sum(1 for _ch in _rcontent if _ch.isprintable() or _ch in "\n\r\t")
            _ratio = _printable / max(1, len(_rcontent))
            if _rcontent.lstrip().startswith("%PDF") or _ratio < 0.70:
                _sz = _rr.get("size") or len(_rb)
                return (f"El archivo {_rpath} es binario ({_sz} bytes), señor: no puedo "
                        f"mostrarlo como texto. Dígame si quiere que lo descargue o lo abra.")
            if len(_rcontent) > 1600:
                _rcontent = _rcontent[:1600].rstrip() + "\n…(truncado)"
            return f"Del Nextcloud ({_rpath}), señor:\n{_rcontent}"
        # 2) ¿pide ENTRAR en una carpeta? "qué hay en (la carpeta) X" | "abre la carpeta X"
        _m_fold = re.search(
            r"(?:qu[ée] hay|qu[ée] tienes|dime|mu[ée]stra|lista|abre|abrime|entra en)\b[^.\n]{0,12}"
            r"(?:en\s+|dentro de\s+|dentro\s+)?(?:la\s+)?carpeta\s+"
            r"([\w .\-áéíóúñü%]{2,40}?)(?:\s+de\b|\s+del\b|[.,!?]|$)|"
            r"\bcarpeta\s+([\w .\-áéíóúñü%]{2,40}?)\b[^.\n]{0,15}"
            r"\b(?:de\s+(?:la\s+)?)?(?:nube|nextcloud|cloud)\b",
            cleaned, re.I)
        _asked = ((_m_fold.group(1) or _m_fold.group(2)) if _m_fold else "")
        _asked = (_asked or "").strip().rstrip(".,!?")
        if _asked and _asked.lower() not in ("nube", "nextcloud", "cloud", "la nube"):
            _real = _nc_low.get(_asked.lower())
            if not _real:
                for _k, _v in _nc_low.items():
                    if _asked.lower() in _k or _k in _asked.lower():
                        _real = _v
                        break
            _target = _real or _asked
            try:
                _sr = nextcloud_list_handler({"path": _target})
            except Exception as e:
                return f"No pude abrir la carpeta {_target}, señor: {e}"
            _serr = (_sr.get("error") or "").strip()
            _sitems = _sr.get("items") or _sr.get("list") or []
            if _serr and not _sitems:
                _listed2 = ", ".join([_nc_dn(x) for x in (_nc_folders + _nc_files)[:10]])
                return (f"No veo la carpeta {_target} en el Nextcloud, señor. "
                        f"En la raíz tengo: {_listed2}.")
            _sf = [_nc_dn(i.get("name")) for i in _sitems if i.get("type") == "folder"
                   and (i.get("name") or "").strip().lower() != _target.strip().lower()]
            _sfl = [_nc_dn(i.get("name")) for i in _sitems if i.get("type") != "folder"]
            _sparts = []
            if _sf:
                _sparts.append("carpetas " + ", ".join(_sf[:8]) + ("…" if len(_sf) > 8 else ""))
            if _sfl:
                _sparts.append("archivos " + ", ".join(_sfl[:8]) + ("…" if len(_sfl) > 8 else ""))
            if not _sparts:
                return f"La carpeta {_nc_dn(_target)} está vacía, señor. Ni ecos."
            return f"En {_nc_dn(_target)} del Nextcloud, señor: {'; '.join(_sparts)}. Servido."
        # 3) ¿pregunta si EXISTE algo? "¿tienes la carpeta X?" -> buscar en la raíz
        _m_have = re.search(
            r"(?:tienes|tiene|existe)\b[^.\n]{0,10}(?:la\s+|el\s+|una\s+|un\s+)?"
            r"(?:carpeta|archivo|documento|fichero)\s+([\w .\-áéíóúñü%]{2,40}?)(?:\?|[.,!]|$)",
            cleaned, re.I)
        if _m_have and (_m_have.group(1) or "").strip():
            _want = _m_have.group(1).strip().rstrip(".,!?").lower()
            _hit = _nc_low.get(_want)
            if not _hit:
                for _k, _v in _nc_low.items():
                    if _want in _k or _k in _want:
                        _hit = _v
                        break
            if _hit:
                return f"Sí, señor: {_nc_dn(_hit)} está en el Nextcloud. La tengo fichada."
            _listed = ", ".join([_nc_dn(x) for x in (_nc_folders + _nc_files)[:10]])
            return (f"No veo eso en la raíz del Nextcloud, señor. Tengo: {_listed}. "
                    "Mis ojos, sus órdenes.")
        # 4) listar raíz; si dice "todo", bajar un nivel a cada carpeta
        if re.search(r"\b(todo|todas|completo|entero)\b", cleaned, re.I):
            _lines = []
            for _fd in _nc_folders[:8]:
                try:
                    _cr = nextcloud_list_handler({"path": _fd})
                except Exception:
                    continue
                _ci = _cr.get("items") or _cr.get("list") or []
                _cn = [(_nc_dn(i.get("name")) or "") for i in _ci
                       if (i.get("name") or "").strip().lower() != _fd.strip().lower()][:8]
                _lines.append(f"{_nc_dn(_fd)}: {', '.join(_cn) if _cn else '(vacía)'}")
            _head = "carpetas " + ", ".join([_nc_dn(x) for x in _nc_folders[:8]])
            if _nc_files:
                _head += "; archivos " + ", ".join([_nc_dn(x) for x in _nc_files[:6]])
            return f"En el Nextcloud, señor: {_head}." + (" " + " ".join(_lines) if _lines else "")
        _parts = []
        if _nc_folders:
            _parts.append("carpetas " + ", ".join([_nc_dn(x) for x in _nc_folders[:8]])
                          + ("…" if len(_nc_folders) > 8 else ""))
        if _nc_files:
            _parts.append("archivos " + ", ".join([_nc_dn(x) for x in _nc_files[:6]])
                          + ("…" if len(_nc_files) > 6 else ""))
        if not _parts:
            return "El Nextcloud está vacío en la raíz, señor. Ni polvo."
        return f"Tengo en el Nextcloud, señor: {'; '.join(_parts)}. Ordenadito y a su servicio."

    # --- Estado de la página del ciclo: SIEMPRE antes del estado de las VMs ---
    # Si el hilo va del campus, "está abierta?" es del campus. Al revés se
    #respondía con VirtualBox, que es lo que el señor odiaba.
    if _about_campus(cleaned) and _CAMPUS_ASK_RE.search(cleaned):
        return _campus_status_reply(cleaned)

    # --- Índice del campus: al instante, sin parar a pensar ---
    _atajo_campus = _campus_atajo(cleaned)
    if _atajo_campus:
        return _atajo_campus

    # --- Máquinas virtuales: va antes que el estado genérico de VMs, que va
    # más abajo. El atajo es más rápido y además entiende lo que ha dicho ---
    _atajo_vm = _atajo_maquinas(cleaned)
    if _atajo_vm:
        return _atajo_vm

    # --- Apuntes de los cursos: el pasaje exacto, también al instante ---
    _atajo_apuntes = _campus_apuntes_atajo(cleaned)
    if _atajo_apuntes:
        return _atajo_apuntes

    # --- Estado de VMs en fresco (si hay verbo de acción, lo gestiona el modelo) ---
    # Va DESPUÉS del atajo de máquinas, que es más rápido y más claro: el
    # atajo usa la lista ya leída y además entiende al señor cuando habla.
    if _vm_status_asked(cleaned):
        try:
            from tools.virtualbox_tools import vbox_list_handler
            res = vbox_list_handler({})
            fr = _tool_result_to_frase("vbox_list", {"status": "success", "result": res})
            return f"Consultado ahora mismo, señor. {fr}"
        except Exception as e:
            log.debug("vm status: %s", e)

    # --- Ejecutar comando directo (determinista, sin LLM que invente) ---
    m_exec = _EXEC_RE.search(cleaned)
    if m_exec:
        verb = (m_exec.group(1) or "").strip().lower()
        rest = (m_exec.group(2) or "").strip()
        m_in = _EXEC_IN_RE.search(rest)
        target = m_in.group(1).strip().lower() if m_in else ""
        command = rest[:m_in.start()].strip() if m_in else rest
        vm_cand = (target or "").strip() or None
        _RASPI_TARGETS = ("raspberry", "raspberry pi", "raspi", "pi",
                          "raspberrypi", "lucillas.ddns.net", "la raspberry",
                          "el servidor", "servidor", "la raspi")
        if any(t in target for t in _RASPI_TARGETS):
            from tools.raspberry_tools import raspberry_exec_handler
            if not command:
                return ("Dígame el comando, señor. Sea específico; "
                    "mi bola de cristal está en el taller.")
            try:
                res = raspberry_exec_handler({"command": command})
            except Exception as e:
                return f"No pude ejecutar en la Raspberry, señor: {e}"
            err = (res.get("error") or "").strip()
            out = (res.get("output") or "").strip()
            if err and not out:
                return f"No pude ejecutar en la Raspberry, señor: {err}"
            if not out:
                return ("Ejecutado en la Raspberry sin salida, señor. "
                    "Obediente y silenciosa, como debe ser.")
            if len(out) > 1200:
                out = out[:1200].rstrip() + "\n…(salida truncada)"
            return f"En la Raspberry, señor:\n{out}\n(Obedecida al momento.)"
        # "escribe|pon|corre <comando>" sin diana: a la Raspberry solo si parece shell;
        # si parece prosa (correo, carta, mensaje…), sigue al flujo normal (LLM).
        if verb in ("escribe", "pon", "corre", "teclea") \
                and not any(t in target for t in _RASPI_TARGETS) and command:
            _prose = re.match(
                r"^(un|una|el|la|los|las|mi|tu|su|este|esta|ese|esa|para|por|que|del|al|a|e|y|o|"
                r"correo|carta|mensaje|texto|documento|nota|informe|resumen|art[íi]culo|ensayo)\b",
                command, re.I)
            _shell = re.match(
                r"^(sudo|systemctl|systemd|service|apt|apt-get|ls|cd|cat|tac|head|tail|nano|vim|vi|"
                r"ssh|scp|rsync|docker|nmap|hostname|hostnamectl|timedatectl|ip|ping|curl|wget|git|"
                r"python3?|pip3?|node|npm|journalctl|dmesg|top|htop|ps|kill|reboot|shutdown|poweroff|"
                r"df|du|free|uname|whoami|id|env|echo|mkdir|rm|cp|mv|touch|chmod|chown|tar|zip|unzip|"
                r"grep|awk|sed|find|mount|umount|crontab|useradd|passwd|ss|netstat|iptables|ufw|"
                r"vcgencmd|raspi-config)\b",
                command, re.I)
            if _shell and not _prose:
                from tools.raspberry_tools import raspberry_exec_handler as _rx
                try:
                    res = _rx({"command": command})
                except Exception as e:
                    return f"No pude ejecutar en la Raspberry, señor: {e}"
                err = (res.get("error") or "").strip()
                out = (res.get("output") or "").strip()
                if err and not out:
                    return f"No pude ejecutar en la Raspberry, señor: {err}"
                if not out:
                    return ("Ejecutado en la Raspberry sin salida, señor. "
                    "Obediente y silenciosa, como debe ser.")
                if len(out) > 1200:
                    out = out[:1200].rstrip() + "\n…(salida truncada)"
                return f"En la Raspberry, señor:\n{out}\n(Obedecida al momento.)"
        if not command:
            return ("Dígame el comando, señor. Sea específico; "
                    "mi bola de cristal está en el taller. Y sin pilas.")
        if vm_cand:
            from tools.virtualbox_tools import vbox_exec_handler, vbox_list_handler, _resolve
            try:
                vm = _resolve(vm_cand)
            except Exception as e:
                return f"{e}"
        elif _EXEC_CTX_RE.search(cleaned):
            from tools.virtualbox_tools import vbox_exec_handler, vbox_list_handler
            try:
                running = [v["name"] for v in vbox_list_handler({}).get("vms", [])
                           if v.get("state") == "encendida"]
            except Exception:
                running = []
            if len(running) == 1:
                vm = running[0]
            elif not running:
                return ("Ninguna máquina encendida, señor. Todas dormidas. "
                        "Dígame cuál arranco.")
            else:
                return ("Hay varias encendidas (%s). Dígame en cuál, señor."
                        % ", ".join(running))
        else:
            vm = None
        if vm is not None:
            from tools.virtualbox_tools import vbox_exec_handler
            try:
                res = vbox_exec_handler({"name": vm, "command": command})
            except Exception as e:
                return f"No pude ejecutar en {vm}, señor: {e}"
            out = (res.get("output") or "").strip()
            if not out:
                return (f"Ejecutado en {vm} sin salida, señor. "
                        "Silencio sepulcral, misión cumplida.")
            if len(out) > 1200:
                out = out[:1200].rstrip() + "\n…(salida truncada)"
            return f"En {vm}, señor:\n{out}\n(Obedecida al momento.)"
        if not command:
            return ("Dígame el comando, señor. Sea específico; "
                    "mi bola de cristal está en el taller. Y sin pilas.")
        vm = None
        if vm_cand:
            try:
                from tools.virtualbox_tools import _resolve
                vm = _resolve(vm_cand)
            except Exception as e:
                return f"{e}"
        elif _EXEC_CTX_RE.search(cleaned):
            try:
                running = [v["name"] for v in vbox_list_handler({}).get("vms", [])
                           if v.get("state") == "encendida"]
            except Exception:
                running = []
            if len(running) == 1:
                vm = running[0]
            elif not running:
                return ("Ninguna máquina encendida, señor. Todas dormidas. "
                        "Dígame cuál arranco.")
            else:
                return ("Hay varias encendidas (%s). Dígame en cuál, señor."
                        % ", ".join(running))
        if vm is None:
            pass  # sin objetivo claro: sigue el flujo normal (LLM + tools)
        else:
            try:
                res = vbox_exec_handler({"name": vm, "command": command})
            except Exception as e:
                return f"No pude ejecutar en {vm}, señor: {e}"
            out = (res.get("output") or "").strip()
            if not out:
                return (f"Ejecutado en {vm} sin salida, señor. "
                        "Silencio sepulcral, misión cumplida.")
            if len(out) > 1200:
                out = out[:1200].rstrip() + "\n…(salida truncada)"
            return f"En {vm}, señor:\n{out}\n(Obedecida al momento.)"

    # --- Mirar el mensaje al instante y decidir (microsegundos) ---
    # Antes esto no existía: o el mensaje encajaba en un atajo o se iba al
    # modelo. Ahora se clasifica siempre primero y así se sabe cuánto puede
    # permitirse el señor antes de recibir respuesta.
    _intento_info = _intento(cleaned)
    _RELOJ = {"segundos": 0.0}
    if _intento_info.get("tipo") in ("analizar", "charla"):
        _RELOJ["segundos"] = 25.0 if _intento_info["tipo"] == "analizar" else 15.0

    # --- Un archivo: se lee y se dice qué es, al instante ---
    _atajo_fich = _atajo_archivo(cleaned)
    if _atajo_fich:
        return _atajo_fich

    # --- Palabra suelta: «marca», «userver26», «ciclo». Sin frase y sin modelo.
    # Va el primero de todo: para lo local no hace falta redactar nada.
    _directo = _atajo_directo(cleaned)
    if _directo:
        return _directo

    # --- Portada de diario: fuente exacta, sin improvisar ---
    if _NEWS_TRIG_RE.search(cleaned):
        low = cleaned.lower()
        site = next((s for s in _NEWS_SITES if s in low), None)
        if site:
            try:
                from tools.web_tools import web_frontpage_handler
                res = web_frontpage_handler({"site": site})
                return _format_frontpage(site, res.get("headlines", []))
            except Exception as e:
                log.debug("frontpage: %s", e)
                return f"No pude leer la portada de {site}, señor."

    # --- Abrir el campus con login solo ---
    # Con "en la pizarra" no es aquí: la pizarra tiene su propia lista de
    # webapps y se resuelve más abajo.
    if _CAMPUS_OPEN_RE.search(cleaned) and not re.search(
            r"\b(pizarra|tablero|board)\b", cleaned, re.I):
        # La sesión se procura en segundo plano: la automatización de ventanas
        # tarda seis segundos y el señor solo quería la página abierta.
        from memory import sqlite_store as _db_c
        from integrations import campus as _campus_bg
        from tools.campus_tools import campus_open_handler as _co

        def _campus_login_bg():
            try:
                if _campus_bg.login():
                    _db_c.set_setting("campus_sesion_ok", "1")
                else:
                    _db_c.set_setting("campus_sesion_ok", "0")
            except Exception:
                pass
        threading.Thread(target=_campus_login_bg, name="campus-login",
                         daemon=True).start()
        try:
            res = _co({"login": False})
            if res.get("logged_in"):
                return "Abierto y dentro del campus, señor. Con toga y todo."
            note = res.get("note", "")
            return f"Abierta la página del ciclo, señor. {note}".strip()
        except Exception as e:
            log.debug("campus open: %s", e)
            return f"No pude abrir el campus, señor: {e}"

    # --- Desbloquear/entrar al campus (login). Si "página" no trae nombre,
    # se usa el contexto: lo último que "abrimos" era la página del ciclo. ---
    if _CAMPUS_UNLOCK_RE.search(cleaned) and (
            "página del ciclo" in cleaned or "campus" in cleaned
            or "ciclo" in cleaned or "página" in cleaned
            or "la sesión" in cleaned
            or _last_page_context_is_campus()):
        # Nunca lo cojas como desbloqueo del campus si se habla de una VM.
        if not re.search(r"\b(m[áa]quina|vm|userver|virtualbox|servidor)\b", cleaned, re.I):
            try:
                from tools.campus_tools import campus_open_handler
                res = campus_open_handler({})
                if res.get("logged_in"):
                    return "Desbloqueada y dentro, señor. Ya estoy dentro del campus."
                note = res.get("note", "")
                return f"Desbloqueada la página del ciclo, señor. {note}".strip()
            except Exception as e:
                log.debug("campus unlock: %s", e)
                return f"No pude desbloquear la página, señor: {e}"

    # --- Abrir sin preguntar al modelo: app instalada, webapp, sitio o máquina ---
    _m_open = _OPEN_CMD_RE.match(cleaned)
    if _m_open:
        _captura = (_m_open.group(1) or "").strip()
        # El patrón se come el artículo ("abre el pais" -> "pais") y entonces no
        # encontraba la web. Se prueba con la captura y, si no sale, con todo lo
        # que venía detrás del verbo, para no perder ni un atajo.
        _cola = re.sub(r"^\s*(?:por favor\s+|oye\s+)?"
                       r"(?:abre|abreme|abrir|inicia|iniciar|lanza|lanzame|"
                       r"ejecuta|arranca|abre\s+el\s+programa|abre\s+la\s+app|"
                       r"abre\s+la\s+aplicaci[óo]n)\s+", "", cleaned, flags=re.I).strip()
        _candidatos = [_captura]
        if _cola and _cola != _captura:
            _candidatos.append(_cola)
        for _candidato in _candidatos:
            _rapido = _fast_open(_candidato)
            if _rapido:
                return _rapido

    # --- Instalar desde GitHub ---
    # Va por su cuenta, con sus propios verbos, sin depender de que la frase
    # también case como "instalar": así "descarga bat de github" y
    # "baja https://github.com/..." funcionan igual de bien.
    _m_gh = _GITHUB_RE.match(cleaned)
    if _m_gh:
        _r = _instalar_desde_github(_m_gh.group(1))
        if _r:
            return _r
    _m_url = _GITHUB_URL_RE.match(cleaned)
    if _m_url:
        _r = _instalar_desde_github(_m_url.group("ref"))
        if _r:
            return _r

    # --- Instalar un programa: catálogo verificado, sin modelo ni dudas ---
    _m_inst = _INSTALL_RE.match(cleaned)
    if _m_inst:
        _resp = _instalar_rapido(_m_inst.group(1))
        if _resp:
            return _resp
        return (f"No encuentro «{_m_inst.group(1).strip()}», señor. Dígamelo "
                "con el nombre exacto, o si lo ha visto en GitHub, pásamelo "
                "y lo instalo desde allí.")

    # --- Asistentes de datos reales ---
    prompt_for_model = cleaned
    if _proj_ctx:
        prompt_for_model += _proj_ctx
    if input_mode in ("discord-voice", "voice", "whatsapp"):
        # Para voz: breve y hablable (acelera LLM y TTS).
        prompt_for_model += ("\n[Responde en 1-2 frases cortas, con el dato primero: "
                             "es para leer en voz alta.]")
    if _CLIMA_RE.search(cleaned):
        prompt_for_model = _inject_weather(prompt_for_model)
    elif _NOTICIAS_RE.search(cleaned):
        prompt_for_model = _inject_news(prompt_for_model)

    # --- Apuntes de los cursos: si la pregunta va de un tema de sus
    # asignaturas, se le dan los pasajes reales para que responda con lo suyo
    # y no con lo que se invente. Los recoge aqui y los pasa al Context Engine
    # como pieza con nombre, en vez de pegarlos al prompt a pelo. ---
    _ctx_apuntes = ""
    if _inyectar_apuntes(cleaned):
        try:
            from integrations import campus_apuntes as _ap_ctx
            _tema_ctx = _tema_de_la_pregunta(_sin_tildes(cleaned)) or cleaned
            _ctx_apuntes = _ap_ctx.contexto_para_modelo(_tema_ctx, limite=5)
            if not _ctx_apuntes:
                _ctx_apuntes = _ap_ctx.contexto_para_modelo(cleaned, limite=5)
        except Exception as e:
            log.debug("contexto de apuntes: %s", e)

    # --- Contexto + memoria semántica + apuntes locales ---
    _voice_mode = input_mode in ("discord-voice", "voice", "whatsapp")
    _fast = bool(re.search(
        r"\b(r[áa]pido|date prisa|corre|modo r[áa]pido|responde r[áa]pido|"
        r"sin rodeos|al grano|breve)\b", cleaned, re.I))
    _slim = _voice_mode or _fast
    # Por voz se reducia a 5 mensajes y por ahi se perdia el hilo: son 2
    # intercambios y da igual. Ahora caben 10, que entran de sobra en el
    # limite del proveedor y llegan a unos cuantos intercambios seguidos.
    # Cuatro intercambios recientes bastan para charla normal; evitar volcar
    # todo el historial en cada petición hace más rápida la inferencia y reduce
    # que el modelo se distraiga con temas antiguos.
    _ctx_n = 10 if _voice_mode else (7 if _fast else min(Config.CONTEXT_WINDOW_MESSAGES, 8))
    recent = db.get_recent_messages(_ctx_n)

    # El Context Engine decide que entra: memoria, documentos, apuntes, hilo,
    # mision activa, decisiones y estado del sistema, con presupuesto. El motor
    # llama a los mismos sistemas de siempre (memoria semantica, Mem0,
    # biblioteca, apuntes, orquestador); aqui solo se le pasa lo que este camino
    # ya sabe.
    _ancla = _ancla_del_hilo(cleaned)
    _ctx_motor = context_engine.construir(
        cleaned,
        prompt_sistema=_build_system_prompt(prompt_for_model),
        prompt_usuario=prompt_for_model,
        input_mode=input_mode,
        recent_messages=recent,
        ancla=_ancla,
        apuntes_texto=_ctx_apuntes,
        emit=brain.emit,
    )
    messages = _ctx_motor.mensajes
    # Lo que se ha dejado fuera se avisa por el log, pero no se le da al modelo:
    # el modelo no necesita saber lo que no ha recibido.
    if _ctx_motor.descartado:
        log.debug("contexto recortado: %s", "; ".join(_ctx_motor.descartado))

    # --- Model Router ---
    _heavy_verbs = ("plan", "pasos", "investiga", "analiza", "compara",
                    "averigua", "profundiza", "documenta")
    lowered = cleaned.lower()
    is_heavy = len(cleaned) > 160 or any(v in lowered for v in _heavy_verbs)
    is_light = (not is_heavy) and len(cleaned) <= 80
    if _fast:
        # Modo rápido: modelo ligero, contexto ya adelgazado, respuesta breve.
        prompt_for_model += "\n[Responde en 1-2 frases, directo al dato.]"
    timing = time.time()
    _reloj = _reloj_de_espera()
    # Por texto la respuesta se pide en streaming: cada trozo sale por el bus y
    # JARVIS Live la escribe mientras se genera. Por voz NO: ahi hace falta el
    # texto entero para hablarlo, y el TTS solo recibe la respuesta final.
    _en_vivo = (input_mode in ("text", "live", "web", "") and not _voice_mode
                and bool(Config.LLM_STREAM_ENABLED))
    result = brain.router.respond(messages, heavy=is_heavy and not _fast,
                                  light=is_light or _fast, segundos=_reloj,
                                  stream=_en_vivo)
    reply = result.get("reply", "")
    rounds = _parse_tool_plan(reply) if (reply or "").strip() else None
    if not rounds:
        # Red de seguridad para la autonomia (tambien si el modelo fallo o
        # vino vacio): si el señor pide DESARROLLAR algo, se propone
        # opencode_build igual que si lo hubiera pedido el modelo. Solo
        # cuando no hay plan: nunca pisa su decision.
        _dev = _intencion_desarrollo(cleaned)
        if _dev:
            # Antes esta idea iba directa a `opencode_build`, que le pasa el
            # texto tal cual a OpenCode con `--auto`: sin analizar, sin riesgos
            # y sin preguntar nada. El orquestador (`brain.dev`) sí hace todo
            # eso —analiza el proyecto, predice riesgos, escribe el prompt y
            # pide permiso— pero solo se llegaba a él por API, no desde una
            # conversación.
            #
            # Ahora la idea entra por ahí y sale con el trabajo en el panel.
            # Si el orquestador no está (equipo sin el módulo, o import
            # fallido), se cae al camino viejo: mejor|OpenCode sin pensar que
            # no hacer nada.
            _encargo = _encargar_al_taller(cleaned, _dev)
            if _encargo is not None:
                return _encargo
            rounds = [{"reply": "De inmediato, señor. Lo encargo al taller.",
                       "tool": "opencode_build",
                       "parameters": {"objetivo": cleaned[:1500],
                                      "proyecto": _dev}}]
    if rounds:
        _auto = bool(re.search(
            r"\b(modo aut[óo]nomo|aut[óo]nomo|encadena|en cadena|hazlo todo|"
            r"prepara el pc|modo auto)\b", cleaned, re.I))
        _es_investigacion = bool(re.search(
            r"\binvestiga(r)?\b|\bbusca(r)?\b[^.\n]{0,30}\b(en (internet|la web)|informaci[óo]n|sobre|acerca de)\b|"
            r"\baverigua\b[^.\n]{0,30}\b(sobre|acerca de|en)\b",
            cleaned, re.I))
        final = _run_tool_loop(messages, rounds, is_heavy=is_heavy,
                               max_rounds=6 if (_auto or _es_investigacion) else 3)
        if final is not None:
            return final

    if not (reply or "").strip():
        # Se acabo el reloj o el modelo no dijo nada, y no era un desarrollo
        # (eso ya salio por el bucle de arriba con su confirmacion).
        # Respuesta corta y atajo rapido, como antes.
        reply = _corto_si_se_paso(cleaned, bool(result.get("agotado")))
        return reply
    log.info("reset a %.2fs vía %s (fallback=%s, reloj=%.0fs)",
             time.time() - timing, result.get("provider"),
             result.get("fallback"), _reloj)

    # Si no hubo plan de herramienta pero la respuesta venía en formato JSON
    # ("tool" vacio), al señor le llega el JSON entero. Se desenvuelve aquí.
    reply = _texto_del_plan(reply) or reply

    if result.get("fallback") and result.get("provider") != "none":
        # Solo decimos "sin conexión" si de verdad no hay red; si el modelo
        # local respondió con internet presente, no es una desconexión.
        try:
            from model_router import _has_internet
            _net = _has_internet()
        except Exception:
            _net = True
        # Y se dice QUIÉN contestó, que antes no se decía y por eso era
        # falso: ponía «(Modo local.)» en respuestas que había dado Groq, que
        # está en la nube. Saltaba porque `fallback` también se activa cuando
        # el proveedor contesto tras un reintento (por ejemplo, cuando el
        # modelo principal se quedó sin cuota y contestó otro de Groq), y en
        # ese caso el proveedor sigue siendo Groq.
        proveedor = str(result.get("provider") or "").lower()
        if not _net:
            reply = f"(Modo limitado, sin conexión.) {reply}"
        elif proveedor == "ollama":
            reply = f"(Modo local.) {reply}"
        elif proveedor:
            reply = f"(Contestó {proveedor}.) {reply}"
    return reply


def _encargar_al_taller(idea: str, proyecto: str) -> Optional[str]:
    """Manda la idea al orquestador: la analiza y la deja en el panel.

    Devuelve lo que se le dice al señor, o None si aquí no se pudo (y entonces
    quien llama usa el camino viejo, que mete la idea sin más en OpenCode).

    El orquestador es quien de verdad piensa la idea: lee el proyecto, pide un
    diseño al modelo, predice riesgos, estima y escribe el prompt para OpenCode
    con todo eso dentro. Luego deja la tarea esperando aprobación, así que el
    señor ve el plan en JARVIS Live antes de que se toque un solo fichero.
    """
    gestor = getattr(brain, "dev", None)
    if gestor is None:
        return None
    try:
        crear = gestor.crear(idea=idea, project_path="")
        if not crear.get("ok"):
            log.info("el orquestador no acepto la idea: %s", crear.get("error"))
            return None
        tarea = crear.get("task") or {}
        task_id = tarea.get("id") or ""
        # El análisis va en un hilo (tarda: lee el proyecto y pregunta al
        # modelo). Se lanza en segundo plano porque `_handle` está en el hilo
        # del mensaje y no puede quedarse esperando.
        gestor.analizar(task_id)
    except Exception as exc:  # noqa: BLE001
        log.info("no se pudo encargar al orquestador (%s); va por el camino viejo",
                 exc)
        return None
    return (f"Lo he encargado, señor. Estoy pensando la idea y preparando el "
            f"plan; en cuanto esté le digo qué he pensado y lo apruebo usted "
            f"antes de tocar nada. Lo verá en JARVIS Live.")


def _intencion_desarrollo(texto: str) -> str:
    """¿Pide el señor DESARROLLAR algo? Devuelve un nombre de proyecto o "".

    Solo salta con verbo de construccion + objeto de software a la vez
    ("desarrollame una web", "creame una app", "hazme una tienda"). Jamas con
    recordatorios, avisos o notas, que son del planificador, ni con preguntas
    ("que es", "como funciona"), que son conversacion.
    """
    import re as _re
    import unicodedata as _ud
    t = _ud.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not _ud.combining(c))
    if _re.search(r"\b(recordatorio|alarma|aviso|nota|apunta|recuerda|despertador|"
                  r"cada \d|a las \d|calendario)\b", t):
        return ""
    if _re.search(r"^(que es|qué es|como funciona|cómo funciona|que significa|"
                  r"expl[ii]came|cuentame|háblame)\b", t.strip()):
        return ""
    # «quiero que abras el bot» es una ORDEN, no una petición de software: el
    # "que" introduce un subjuntivo y no hay sustantivo de por medio.
    # «quiero una app que me avise» sí lo es: el "que" es relativo y describe
    # la app. La diferencia no es el "que", es si entre el verbo y el "que"
    # hay un sustantivo de software. Por eso no vale un patrón suelto.
    if _re.search(r"\b(quiero|necesito|monta|saca)\b([^.\n]{0,30}?)\bque\b", t):
        _entre = _re.search(r"\b(quiero|necesito|monta|saca)\b([^.\n]{0,30}?)\bque\b",
                            t).group(2)
        _obj = (r"(proyecto|web|app|aplicacion|programa|script|tienda|pagina|"
                r"software|juego|plugin|extension|calculadora|formulario|"
                r"panel|bot|dashboard|cron[óo]metro)")
        if not _re.search(_obj, _entre):
            return ""
    objeto = (r"(proyecto|web|app|aplicacion|programa|script|tienda|pagina|"
              r"software|juego|plugin|extension|calculadora|formulario|"
              r"panel|bot|dashboard|cron[óo]metro)")
    # Ojo con la lista: más abajo, al limpiar el nombre del proyecto, se quitan
    # `quiero` y `necesito`. Si aquí no estaban, «quiero una app que me avise del
    # tiempo» no se detectaba como desarrollo: se limpiaba el verbo del nombre
    # pero no se buscaba. Son las dos listas del mismo verbo y tienen que
    # coincidir.
    verbo = (r"(desarrolla(?:me|r)?|programa(?:me)?|crea(?:me)?|hazme|"
             r"construye(?:me)?|disena(?:me)?|quiero|necesito|monta(?:me)?|"
             r"arme|saca(?:me)?|re[ae]liza(?:me)?|implemen(?:ta|te)(?:me)?)")
    if not _re.search(verbo + r"\b.{0,40}\b" + objeto, t):
        return ""
    nombre = _re.sub(r"^(por favor |oye |jarvis[, ]*)*", "", t.strip())
    nombre = _re.sub(r"\b(desarrollame|desarrolla|programame|programa|creame|crea|"
                     r"hazme|construyeme|construye|disename|disena|quiero|necesito|"
                     r"montame|monta|arme|arma|sacame|saca|realizame|realiza|"
                     r"realeizame|implementame|implementa|que|un|una|"
                     r"nuevo|nueva|para|mi|el|la|de|del)\b", " ", nombre)
    nombre = _re.sub(r"[^a-z0-9]+", "-", nombre).strip("-")[:30].strip("-")
    return nombre or "proyecto"


def _parse_tool_plan(reply: str):
    """Extrae el primer {"reply","tool","parameters"} o None (aunque el
    modelo pegue varios JSON seguidos)."""
    try:
        stripped = reply.strip().strip("```json").strip("```").strip()
        start = stripped.find("{")
        if start < 0:
            return None
        depth, instr, esc = 0, False, False
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            else:
                if ch == '"':
                    instr = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        parsed = json.loads(stripped[start:i + 1])
                        if parsed.get("tool"):
                            return parsed
                        break
    except Exception:
        pass
    return None


# Tools que devuelven DATOS (el modelo razona sobre ellos y puede encadenar).
#
# Aquí NO se puede dejar ninguna fuera por descuido. Una tool que no está en
# esta lista se ejecuta, se traga el resultado y vuelve con una frase genérica
# sin enseñarle nada al modelo: el modelo contesta sin ver el dato y se lo
# inventa. Pasó con analizar_archivo y list_dir, que son justo las dos que
# hacen falta en un NAS: el señor pedía leer un fichero del share, JARVIS lo
# leía bien, y contestaba «No tengo permisos para acceder a ese archivo» porque
# el texto nunca llegó al modelo. La herramienta funcionaba; lo que no funcionaba
# era enseñarle el resultado.
_DATA_TOOLS = {"vbox_exec", "vbox_read_file", "vbox_info", "vbox_list",
               "web_fetch", "web_search_results", "web_frontpage",
               "write_script", "run_script",
               # Ficheros: el contenido, el listado y el análisis son el dato.
               "analizar_archivo", "list_dir", "download_file", "move_file",
               # Estado real del sistema: si el modelo no lo ve, lo inventa.
               "system_diagnose", "health_overview", "git_status",
               "git_log", "git_diff", "gpu_status", "camara_estado",
               "perf_advisor", "sec_audit", "cleanup_disk", "list_tasks",
               # Integraciones que devuelven datos del-campus y del móvil.
               "campus_courses", "campus_grades", "campus_search",
               "campus_notas", "campus_deadlines",
               "mobile_dispositivos", "mobile_apps",
               # Análisis de seguridad de ficheros.
               "vt_scan_file", "vt_scan_url",
               # Plugins de custom_tools. Sin esto, el plugin se ejecuta, se
               # traga el resultado y el modelo contesta sin verlo, que es
               # justo el fallo que se corrigio arriba para analisar_archivo.
               "agenda_de_hoy", "correo_sin_leer",
               "nas_espacio", "nas_salud", "nas_contenedores", "nas_registro",
               # Domotica: leer el estado de un aparato es el dato; accionarlo
               # tambien, porque si no el modelo contesta "hecho" sin saber si
               # se ha encendido o no.
               "domotica_estado", "domotica_lista", "domotica_accion"}


def _compact_result(tool_name: str, result: dict, limit: int = 1500) -> str:
    try:
        res = result.get("result", {}) or {}
        if tool_name == "vbox_exec":
            return str(res.get("output", "") or res.get("error", ""))[:limit]
        if tool_name == "vbox_read_file":
            return str(res.get("content", ""))[:limit]
        # Un analisis de fichero trae su "resumen" (los trozos que de verdad
        # importan) mas un monton de estadisticas. Al modelo le sirve el
        # resumen; las estadisticas solo le hacen gastar contexto.
        if tool_name == "analizar_archivo":
            resumen = res.get("resumen")
            if resumen:
                partes = [f"{res.get('nombre')} ({res.get('tipo')}, {res.get('tamano')})"]
                for r in (resumen if isinstance(resumen, list) else [resumen]):
                    partes.append(f"- {r}")
                if res.get("aviso"):
                    partes.append(f"(aviso: {res['aviso']})")
                return "\n".join(partes)[:limit]
        return json.dumps(res, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return ""


def _texto_del_plan(reply: str) -> str:
    """Saca la frase de una respuesta que viene en formato de plan de tool.

    El modelo contesta con {"reply": ..., "tool": ..., "parameters": ...}
    incluso cuando ya no quiere usar ninguna herramienta: pone "tool": "" y
    sigue. _parse_tool_plan lo descarta (no hay tool) y entonces el JSON
    entero se iba al chat del señor, llaves incluidas.

    Aquí se desenvuelve: si hay un "reply" dentro, se devuelve solo eso. Si no
    es JSON, se devuelve tal cual.
    """
    try:
        s = (reply or "").strip()
        if not s.startswith("{"):
            return s
        datos = json.loads(s)
        if isinstance(datos, dict):
            return str(datos.get("reply") or "").strip()
    except Exception:
        pass
    return (reply or "").strip()


def _run_tool_loop(messages: list, first_plan: dict, is_heavy: bool = False,
                    max_rounds: int = 3):
    """Ejecuta herramientas encadenando con el modelo (3 rondas; 6 en modo
    autónomo).

    Ronda 1 como hasta ahora (frase directa). Si la tool es de DATOS y va
    bien, el modelo recibe el resultado y puede pedir OTRA herramienta o
    responder. Devuelve el texto final o None si no hubo plan.
    """
    tool_plan = first_plan
    _stop_at = max(1, min(8, max_rounds)) - 1
    for rnd in range(max(1, min(8, max_rounds))):
        tool_name = tool_plan["tool"]

        # opencode_build tiene DOS puertas de entrada, y hay que cerrar las
        # dos: (1) la red de seguridad de arriba, cuando el modelo NO devuelve
        # un plan; y (2) esta, que es la que se usa casi siempre, porque el
        # prompt del sistema le dice al modelo que llame a esa herramienta y
        # el modelo hace caso.
        #
        # Antes de cerrarlo solo por la primera, la idea seguía yéndose por
        # aquí: OpenCode recibía el texto tal cual con `--auto`, sin análisis
        # ni riesgos ni permiso. Se vio preguntando en el contenedor y
        # buscando la tarea en /api/dev/tasks: no había ninguna.
        if tool_name in ("opencode_build", "opencode_build_handler"):
            _idea = str((tool_plan.get("parameters") or {}).get("objetivo")
                        or (tool_plan.get("parameters") or {}).get("task")
                        or (tool_plan.get("reply") or "")).strip()
            _proy = _intencion_desarrollo(_idea) or "proyecto"
            _enc = _encargar_al_taller(_idea, _proy)
            if _enc is not None:
                return _enc
            # Sin orquestador (o si falla) se sigue por aquí, que es la red.
        params = tool_plan.get("parameters", {}) or {}
        try:
            brain.emit({"type": "action_status", "label": _tool_label(tool_name),
                        "state": "running"})
        except Exception:
            pass
        tool_result = _run_tool_sync(tool_name, params)
        status = tool_result.get("status")
        if status != "success":
            frase = _tool_result_to_frase(tool_name, tool_result)
            return frase
        if tool_name not in _DATA_TOOLS or rnd >= _stop_at:
            out_msg = tool_plan.get("reply", "").strip()
            frase = _tool_result_to_frase(tool_name, tool_result)
            final = f"{out_msg} {frase}".strip()
            return final
        # Ronda de razonamiento: el modelo ve el dato y sigue o responde.
        data = _compact_result(tool_name, tool_result)
        messages = messages + [
            {"role": "assistant",
             "content": json.dumps(tool_plan, ensure_ascii=False)},
            {"role": "user",
             "content": f"[resultado {tool_name}]: {data}\n"
                        "Con este dato, responde al señor o pide OTRA herramienta "
                        "con el mismo formato JSON si necesitas más datos."},
        ]
        nxt, nxt_plan = {}, None
        try:
            nxt = brain.router.respond(messages, heavy=is_heavy, light=False)
            nxt_plan = _parse_tool_plan(nxt.get("reply", ""))
        except Exception as e:
            log.debug("tool loop: %s", e)
        if not nxt_plan:
            # El modelo contesta con el formato JSON pero sin herramienta, que
            # es como dice "ya no necesito mas datos, esto es la respuesta".
            # _parse_tool_plan lo rechaza porque "tool" va vacio, y antes se
            # devolvia el JSON entero al señor, que leia en el chat:
            #   {"reply": "El archivo contiene...", "tool": "", ...}
            # Ahora se desenvuelve y solo sale la frase.
            return _texto_del_plan(nxt.get("reply", "")) or \
                _tool_result_to_frase(tool_name, tool_result)
        # La siguiente tool requiere confirmación: se para y se pregunta.
        try:
            tool_def = (brain.tools.list_tools() if brain.tools else [])
            need = next((t for t in tool_def
                         if t.get("name") == nxt_plan.get("tool")), {})
            if need.get("requires_confirmation"):
                say = ""
                try:
                    inner = _parse_tool_plan(nxt.get("reply", "") or "")
                    if isinstance(inner, dict):
                        say = inner.get("reply", "")
                except Exception:
                    pass
                say = (say or nxt_plan.get("reply") or "Esa acción").strip()
                return f"{say} (Requiere su confirmación, señor.)"
        except Exception:
            pass
        tool_plan = nxt_plan
    out_msg = tool_plan.get("reply", "").strip()
    return f"{out_msg}".strip() or "Hecho, señor."


def _frase_frontpage(res: dict) -> str:
    heads = res.get("headlines", []) if isinstance(res, dict) else []
    if not heads:
        return "Portada vacía, señor."
    lines = [f"{i + 1}. {h.get('title', '')}" for i, h in enumerate(heads[:8])]
    return f"Portada de {(res.get('site', '') or '').capitalize()}, señor:\n" + "\n".join(lines)


def _frase_exec(res: dict) -> str:
    out = (res.get("output") or "").strip()
    err = (res.get("error") or "").strip()
    if out:
        if len(out) > 1200:
            out = out[:1200].rstrip() + "\n…(salida truncada)"
        return f"Ejecutado, señor:\n{out}"
    if err:
        return f"Ejecutado con avisos, señor: {err[:300]}"
    return "Ejecutado sin salida, señor."


def _format_frontpage(site: str, headlines: list) -> str:
    if not headlines:
        return f"La portada de {site} viene vacía, señor."
    lines = [f"{i + 1}. {h.get('title', '')}" for i, h in enumerate(headlines[:8])]
    return f"Portada de {site.capitalize()}, señor:\n" + "\n".join(lines)


def _frase_read(res: dict) -> str:
    content = (res.get("content") or "")
    if not content.strip():
        return f"El fichero {res.get('path', '?')} está vacío, señor."
    if len(content) > 2000:
        content = content[:2000].rstrip() + "\n…(contenido truncado)"
    return f"Contenido de {res.get('path', '?')}, señor:\n{content}"


def _tool_label(tool_name: str) -> str:
    """Etiqueta humana de la tool para avisos de progreso."""
    try:
        t = (brain.tools._registry or {}).get(tool_name, {})
        return t.get("label", tool_name) or tool_name
    except Exception:
        return tool_name


def _tool_result_to_frase(tool_name: str, r: dict) -> str:
    if r.get("status") == "success":
        res = r.get("result", {}) or {}
        # Un handler puede devolver {"ok": false, "error": ...} en vez de
        # lanzar. El ToolManager lo envuelve igual como success, y aqui se
        # acababa en el "Hecho, señor." de debajo, tragandose el error: el
        # señor oia que todo iba bien cuando no se habia hecho nada. Pasa con
        # los plugins de custom_tools, que devuelven dicts y no recetas.
        if isinstance(res, dict) and res.get("ok") is False:
            motivo = str(res.get("error") or res.get("motivo")
                         or "no se por que").strip()
            return f"No he podido, señor: {motivo[:220]}"
        if tool_name == "vbox_list":
            vms = res.get("vms", []) if isinstance(res, dict) else []
            if not vms:
                return "No veo ninguna máquina virtual, señor."
            items = ", ".join(f"{v.get('name')} ({v.get('state', '?')})" for v in vms[:12])
            more = "" if len(vms) <= 12 else f" (+{len(vms) - 12} más)"
            return (f"Estas son sus máquinas, señor: {items}{more}. "
                "Todas formadas y en orden.")
        if tool_name == "vbox_info":
            info = res.get("info", {}) if isinstance(res, dict) else {}
            if not info:
                return "Sin datos de esa máquina, señor."
            parts = ", ".join(f"{k}: {v}" for k, v in list(info.items())[:8])
            return f"{res.get('vm', 'La máquina')}, señor: {parts}."
        if tool_name == "campus_deadlines":
            items = res.get("deadlines", []) if isinstance(res, dict) else []
            if not items:
                return "Sin entregas próximas, señor."
            lines = [f"{d.get('date', '?')} — {d.get('title', '?')}"
                     + (f" ({d.get('course', '')})" if d.get("course") else "")
                     for d in items[:8]]
            return "Próximas entregas, señor:\n" + "\n".join(lines)
        if tool_name == "campus_grades":
            items = res.get("grades", []) if isinstance(res, dict) else []
            if not items:
                return "Sin notas publicadas, señor."
            lines = [f"{g.get('course', '?')}: {g.get('grade', '?')}"
                     for g in items[:12]]
            return "Sus notas, señor:\n" + "\n".join(lines)
        if tool_name == "campus_open":
            if res.get("logged_in"):
                return "Abierto y dentro del campus, señor. Con toga y todo."
            note = res.get("note", "")
            return f"Abierta la página del ciclo, señor. {note}".strip()
        return {
            "open_app": f"Abierto, señor: {(res.get('opened') or res.get('target') or '')}. A mandar.".rstrip(" :."),
            "open_file": f"Ahí lo tiene, señor: {res.get('opened', '')}. Servido.",
            "open_browser": f"Navegador abierto, señor: {res.get('opened', '')}. Buena caza.",
            "web_search": f"Búsqueda abierta, señor: {res.get('opened', '')}. Buena caza.",
            "volume_set": f"Volumen al {res.get('volume', '?')}, señor. Ni sordo ni cotilla: justo.",
            "spotify_play": "Música en marcha. Que suene.",
            "spotify_pause": "Música en pausa, señor. Silencio sepulcral.",
            "spotify_next": "Siguiente. A la siguiente.",
            "spotify_prev": "Anterior. De vuelta atrás.",
            "delete_file": "Archivo eliminado, señor. Sin funeral.",
            "minimize_window": "Ventana minimizada. Fuera de la vista, no de la mente.",
            "maximize_window": "Ventana maximizada, señor. A lo grande.",
            "switch_window": "Ventana cambiada, señor. Multitarea, mi segundo nombre.",
            "open_url": f"Abierto en {res.get('browser', 'el navegador')}, señor: {res.get('opened', '')}. Buena caza.",
            "write_script": f"Guardado, señor: {res.get('name', '')} ({res.get('bytes', 0)} bytes). Programado con elegancia.",
            "run_script": f"Ejecutado, señor: {(res.get('output') or 'sin salida').strip()[:800]} Código obediente.",
            "defender_scan": f"Análisis {res.get('scan', '')} en marcha, señor: {res.get('status', '')}. A cazar.",
            "defender_threats": f"Amenazas a la vista, señor: {res.get('threats', 'ninguna')}. Preparo la red.",
            "win_updates": f"Pendientes: {res.get('count', 0)}, señor. Actualizar es vivir.",
            "perf_advisor": f"CPU {res.get('cpu_avg', '?')}%, RAM {res.get('ram_pct', '?')}%. Consejos: {'; '.join(res.get('tips', []) or ['ninguno, va fino'])}.",
            "vt_scan_file": f"VirusTotal: {res.get('malicious', '?')} detecciones, señor. Veredicto servido. {res.get('link', '')}",
            "vt_scan_url": f"VirusTotal URL: {res.get('malicious', '?')} detecciones, señor. Veredicto servido.",
            "mbam_open": "Malwarebytes abierto, señor: un clic en Analizar y a cazar.",
            "health_overview": "Revisado de arriba abajo, señor.",
            "download_file": f"Descargado, señor: {res.get('path', '')} ({res.get('bytes', 0)} bytes). Botín asegurado.",
            "move_file": f"Movido, señor: de {res.get('from', '?')} a {res.get('to', '?')}. Orden en el caos.",
            "list_dir": f"En {res.get('path', '')}, señor: {', '.join(res.get('entries', []) or ['vacío'])[:400]}.",
            "gpu_status": f"GPU, señor: {res.get('gpu', '?')}.",
            "launch_game": f"Lanzando {res.get('game', '')}, señor. Que gane el mejor (usted).",
            "schedule_task": f"Tarea «{res.get('name', '')}» programada para las {res.get('at', '?')}{res.get('repeat', '')}, señor. No se me olvida. Casi nunca.",
            "list_tasks": f"Tareas, señor: {'; '.join(res.get('tasks', []) or ['ninguna'])}.",
            "cancel_task": f"Cancelada «{res.get('cancelled', '')}», señor. Quedan {res.get('remaining', 0)}.",
            "notify_phone": "Aviso enviado al móvil, señor. Que suene.",
            "opencode_fix": "OpenCode manos a la obra, señor.",
            "opencode_ask": "Consultado con OpenCode, señor.",
            "auto_optimize": "Optimizado sin tocar nada clave, señor.",
            "translate": f"{res.get('text', '')} ({res.get('via', '')}, señor.)",
            "spotify_search": "Esto suena, señor.",
            "spotify_playlists": "Sus playlists, señor.",
            "spotify_play_name": f"Sonando: {res.get('name', '')}, señor. Suba el volumen.",
            "sec_audit": "Auditoría lista, señor. Solo miro, no toco.",
            "git_status": f"Git en {res.get('repo', '')}, señor: {res.get('status', '')}.",
            "git_log": f"Historial, señor: {res.get('log', '')}.",
            "git_diff": f"Cambios, señor: {res.get('stat', '')}.",
            "git_commit": f"Commit hecho, señor: {res.get('commit', '')}. Para la posteridad.",
            "vbox_list": "Estas son sus máquinas, señor.",
            "vbox_info": "Ahí tiene los datos, señor. Ficha completa.",
            "vbox_start": ("Ya estaba en marcha, señor. Ni la toco." if (r.get("result") or {}).get("already")
                             else "Arrancando, señor. Despegamos."),
            "vbox_stop": ("Ya estaba apagada, señor. Ni la toco." if (r.get("result") or {}).get("already")
                          else "Apagada, señor. A dormir."),
            "vbox_create": f"Máquina creada, señor: {res.get('vm', '')}. Bienvenida a la familia.",
            "vbox_exec": _frase_exec(res),
            "vbox_read_file": _frase_read(res),
            "vbox_write_file": f"Escrito en {res.get('path', '?')}, señor ({res.get('bytes', 0)} bytes). Tinta digital.",
            "vbox_unlock": "Desbloqueada, señor. Sésamo abierto.",
            "vbox_console": "Consola en pantalla, señor. Palomitas opcionales.",
            "vbox_type": "Tecleado, señor. Sin faltas. Casi.",
            "type_text": f"Escrito en {res.get('window', 'la ventana')}, señor ({res.get('typed_chars', 0)} caracteres).",
            "press_keys": f"Pulsado {res.get('pressed', '')}, señor.",
            "web_fetch": "Página leída.",
            "web_search_results": "Resultados listos, señor.",
            "web_frontpage": _frase_frontpage(res),
            # Plugins de custom_tools. Aquí no hace falta una frase por
            # herramienta: son herramientas de DATOS, van por la ronda de
            # razonamiento y el modelo ya cuenta lo que ha visto.
            "agenda_de_hoy": "",
            "correo_sin_leer": "",
            "nas_espacio": "",
            "nas_salud": "",
            "nas_contenedores": "",
            "nas_registro": "",
        }.get(tool_name, "Hecho, señor.")
    if r.get("status") == "cancelled":
        return "Acción cancelada, señor."
    return f"No he podido, señor: {r.get('message', 'error desconocido')}"


def _launch_wake_up() -> str:
    def runner():
        try:
            from routines import wake_up
            spoken = wake_up.run(
                emit=brain.emit,
                speak_fn=lambda t: _speak_both(t),
                engine=brain.voice.engine if brain.voice else None,
            )
            if spoken:
                db.add_message("assistant", spoken)
        except Exception:
            log.exception("wake-up runner")
        finally:
            # Cierra el panel de pasos (PENSAR→ACTUAR→HABLAR): el ritual
            # narra con texto/voz, no es un comando atascado.
            brain.emit({"type": "action_status", "label": "Protocolo matutino",
                        "state": "done"})
    threading.Thread(target=runner, daemon=True, name="wake-up").start()
    return "De inmediato, señor. Le preparo la mañana… El sol puede esperar; yo no."


def _speak_both(text: str) -> None:
    """Narra el ritual: suena por voz si hay salida Y deja constancia escrita."""
    if brain.voice is not None:
        try:
            brain.voice.speak_out(text)
        except Exception as e:
            log.debug("wake speak: %s", e)
    brain.emit({"type": "assistant_reply", "text": text})


# --------------------------------------------------------------------------- #
# Quién puede hablar con este cerebro
# --------------------------------------------------------------------------- #
# El token vive en el directorio de datos y se inyecta en el HTML que sirve el
# propio JARVIS, de modo que las paginas de siempre no tienen que hacer nada
# raro. Lo que se cierra con esto es el ataque de "tengo una pestana abierta".
def _ws_autorizado(ws: WebSocket) -> bool:
    """Token + origen. Ambos, y en ese orden: el origen solo se mira si el
    cliente lo manda, y un cliente que no lo manda puede ser Electron."""
    import auth as _auth
    origen = ws.headers.get("origin") if ws.headers else None
    if not _auth.origen_permitido(origen):
        return False
    candidato = ws.query_params.get("token") or _auth.token_por_cabecera(ws.headers)
    return _auth.coincide(candidato)


def _exige_token(req) -> bool:
    """True si la peticion viene con el token bueno.

    El request se pasa como parametro y no se coge de un global: en FastAPI
    solo existe si el endpoint lo declara, asi que leerlo de fuera es un
    NameError esperando a ocurrir.
    """
    import auth as _auth
    if req is None:
        return False
    return _auth.coincide(_auth.token_por_cabecera(req.headers))


def _exige_token_o_url(req) -> bool:
    """Como _exige_token, pero además acepta el token en la query.

    Existe solo para las descargas. Un <a download> o un window.open() no
    mandan cabeceras, asi que no hay otra forma de que el token llegue; y sin
    esto, pinchar en un fichero del NAS daria 401 siempre.

    No se aplica al resto de endpoints a proposito: un token en la URL se queda
    en el historial del navegador y en los logs del servidor, mientras que en
    una cabecera no. Aqui se acepta porque es la unica via que tiene, y porque
    el token ya viaja en la URL del WebSocket desde antes.
    """
    import auth as _auth
    if req is None:
        return False
    if _auth.coincide(_auth.token_por_cabecera(req.headers)):
        return True
    try:
        return _auth.coincide(req.query_params.get("token"))
    except Exception:
        return False


# Rutas que se sirven SIN token, y por que:
#   - paginas HTML: no devuelven datos, y llevan el token inyectado para que
#     sus propios fetch si lo manden;
#   - /health: la usan Docker y el orquestador para saber si el proceso vive;
#   - assets estaticos.
# Todo lo demas que empieza por /api/ o /automatizaciones/ PIDE token.
RUTAS_SIN_TOKEN = {
    "/", "/health", "/healthz", "/live", "/ui", "/board", "/config", "/setup",
    "/modo", "/mic", "/mic_audio", "/wa", "/docs", "/redoc", "/openapi.json",
    "/favicon.ico",
}
PREFIJOS_SIN_TOKEN = ("/live/", "/ui/", "/board/", "/assets", "/static",
                      "/favicon")


@app.middleware("http")
async def _token_en_las_api(request: Request, call_next):
    """Ninguna ruta de la API se sirve sin token. Ni una.

    Antes cada endpoint se protegia por su cuenta y se passo de largo media
    docena: `/api/board/terminal` abria una shell de verdad, `/api/dev/tasks/.../
    approve` lanzaba la mision y `/api/config` escribia la configuracion, todo
    eso sin pedir nada. Con el puerto publicado en la LAN (docker-compose) eso
    era reachable por cualquiera de la red.

    Aqui se cierra de una vez y en un solo sitio. Se acepta el token en la
    cabecera o en la query, porque hay descargas y <img> que no pueden mandar
    cabeceras; y OPTIONS pasa siempre, que es el previo de CORS y no lleva
    token. Un 401 claro tambien es mejor que un fallo raro dentro del endpoint.
    """
    ruta = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if ruta in RUTAS_SIN_TOKEN or any(ruta.startswith(p) for p in PREFIJOS_SIN_TOKEN):
        return await call_next(request)
    if not (ruta.startswith("/api/") or ruta.startswith("/automatizaciones/")):
        return await call_next(request)
    if _exige_token(request) or _exige_token_o_url(request):
        return await call_next(request)
    return JSONResponse({"ok": False, "error": "Falta el token de sesion."},
                        status_code=401)


def _inyecta_token(html: str) -> str:
    """Mete el token en la pagina para que sus fetch y sus WebSocket lo lleven.

    Va en una variable de window y no en el HTML visible, y solo se sirve a
    quien ya puede ver la pagina.

    Ademas envuelve `window.fetch` para anadir la cabecera SOLO en lo que va a
    las rutas propias del cerebro (/api/, /automatizaciones/). Estas paginas
    tienen el JS escrito en linea y llamaban a la API sin cabeceras; cuando
    paso a pedir token, sin esto se rompian el guardado de ajustes, la
    importacion de claves y el cambio de modo. Ponerlo aqui y no en cada
    llamada es lo que evita que la proxima pagina nueva vuelva a olvidarlo.
    """
    import auth as _auth
    try:
        t = _auth.token()
    except Exception:
        return html
    envuelve = (
        "(function(f){if(!f)return;window.fetch=function(u,o){try{"
        "var s=(typeof u==='string')?u:(u&&u.url)||'';"
        "if(s.indexOf('/api/')>=0||s.indexOf('/automatizaciones/')>=0){"
        "o=o||{};o.headers=new Headers(o.headers||{});"
        "if(!o.headers.has('X-Jarvis-Token')){"
        "o.headers.set('X-Jarvis-Token',window.JARVIS_TOKEN||'');}}"
        "catch(e){}return f.call(window,u,o);};})(window.fetch);"
    )
    frag = ('<script>window.JARVIS_TOKEN=%s;window.JARVIS_TOKEN_URL=%s;%s</script>' % (
        json.dumps(t),
        json.dumps("?token=" + t),
        envuelve,
    ))
    # Justo despues de <head>, para que este disponible antes que el resto.
    for ancla in ("</head>", "<body"):
        i = html.find(ancla)
        if i >= 0:
            return html[:i] + frag + html[i:]
    return frag + html


def _token_en_url(url: str) -> str:
    """Anade el token a una URL de WebSocket que venga del renderer."""
    import auth as _auth
    try:
        u = str(url or "")
        if not u or "token=" in u:
            return u
        sep = "&" if "?" in u else "?"
        return u + sep + "token=" + _auth.token()
    except Exception:
        return url


def _speak_text(text: str, text_fallback: bool = True) -> None:
    """Habla text con el VoiceManager (o sin él, solo envía reply).

    Si hay manager pero SIN salida de audio real y text_fallback es True, el
    texto se emite para que se lea en el HUD/pizarra: la narración nunca se
    pierde en el vacío. Con text_fallback=False solo suena (evita duplicar
    un texto que ya se mostró, p. ej. Win+J).
    """
    spoken = False
    try:
        from memory import sqlite_store as _db
        import json as _js
        _vo = _js.loads(_db.get_setting("jarvis_config", "") or "{}").get("voice", {}) or {}
        _loud = _vo.get("read_aloud", True) is not False
    except Exception:
        _loud = True
    if brain.voice is not None and _loud:
        try:
            spoken = bool(brain.voice.speak_out(text))
        except Exception as e:
            log.debug("speak_out: %s", e)
    if not spoken and text_fallback and _loud:
        brain.emit({"type": "assistant_reply", "text": text})


# --------------------------------------------------------------------------- #
# FastAPI: websocket
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def startup():
    brain.loop = asyncio.get_running_loop()
    _tapar_token_en_logs()
    _init_services()
    # El índice del campus se levanta en segundo plano: así la primera pregunta
    # sobre sus cursos no espera a que se lea el Moodle entero.
    def _campus_bg():
        try:
            from integrations import vms as _vms_boot
            _vms_boot.al_arrancar()
        except Exception as e:
            log.debug("vms boot: %s", e)
        try:
            from integrations import campus_index as _ix
            _ix.indexar_al_arrancar()
            # Los apuntes van después: primero el índice de cursos, que es lo
            # que dice qué actividades hay, y luego se leen.
            from integrations import campus_apuntes as _ap
            _ap.indexar_al_arrancar()
        except Exception as e:
            log.debug("campus index: %s", e)
    threading.Thread(target=_campus_bg, name="campus-boot",
                     daemon=True).start()

    # Memoria persistente: se toca una vez para saber si Mem0 esta, y para que
    # la PRIMERA pregunta no pague el timeout de conexion. Con el servicio
    # parado, este probe marca el cliente como caido y las siguientes llamadas
    # responden al momento, en vez de esperar cada vez.
    def _memoria_bg():
        try:
            from memory.manager import manager
            m = manager()
            if not m.activo():
                log.info("[MEMORY] Memoria persistente desactivada "
                         "(MEMORY_ENABLED=0). JARVIS sigue con la local.")
                return
            r = m.health()
            if r.get("alcanzable"):
                log.info("[MEMORY] Servicio disponible en %s",
                         m.estado().get("url"))
            else:
                log.warning("[MEMORY] Mem0 no responde (%s). JARVIS contesta "
                            "igual; no habrá recuerdos nuevos hasta que vuelva.",
                            r.get("motivo", "sin respuesta")[:100])
        except Exception as e:
            log.debug("memoria boot: %s", e)

    threading.Thread(target=_memoria_bg, name="memory-boot",
                     daemon=True).start()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    _wst = time.time()
    # Comprobacion ANTES de aceptar. Un WebSocket no esta sujeto a la
    # Same-Origin Policy, asi que cualquier pagina que visitara el usuario
    # podia hablar con este cerebro: mandarle preguntas, pedirle ficheros o
    # responder por el a una peticion de confirmacion del Risk Engine. Se exige
    # el token y un origen de los nuestros.
    if not _ws_autorizado(ws):
        try:
            await ws.close(code=4401, reason="token o origen no valido")
        except Exception:
            pass
        log.warning("WS rechazado: token u origen no valido")
        return
    await ws.accept()
    brain.clients.add(ws)
    # Solo se recuperan los permisos pendientes existentes; los eventos históricos
    # tienen un endpoint propio y no alteran la semántica del chat existente.
    v = brain.voice
    if v is None:
        _voice_txt = "sin hardware"
    elif v.engine.has_input:
        _voice_txt = "voz completa"
    elif v.engine.is_active:
        _voice_txt = "solo salida (sin micro)"
    else:
        _voice_txt = "fallo de audio"
    await brain._broadcast({
        "type": "hello",
        "message": f"Sistemas en línea, señor. Voz {_voice_txt}, "
                   f"proveedores: {_providers_str()}.",
    })
    # Si hay un permiso de arreglo pendiente o un arreglo en curso, la pizarra
    # recién abierta lo muestra de inmediato (no solo quien estaba conectado).
    try:
        from memory import selfheal as _sh
        for _p in _sh.list_errors(50, only_pending=True)[-3:]:
            await ws.send_text(json.dumps({
                "type": "selfheal_update", "id": _p.get("id"),
                "source": _p.get("source", ""), "error": (_p.get("error", "") or "")[:140],
                "stage": "fixing", "note": (_p.get("note", "") or "")[:300],
                "attempt": int(_p.get("attempts", 0) or 0)}, ensure_ascii=False))
        _ap = _sh.pending_approval_id()
        if _ap:
            _items = {i.get("id"): i for i in _sh.list_errors(50)}
            _ai = _items.get(_ap)
            if _ai:
                await ws.send_text(json.dumps({
                    "type": "heal_approval_request", "id": _ap,
                    "source": _ai.get("source", ""),
                    "error": (_ai.get("error", "") or "")[:140],
                    "plan": _sh._plan_for(_ai),
                    "timeout_seconds": 90}, ensure_ascii=False))
    except Exception as e:
        log.debug("heal resend: %s", e)
    try:
        v = getattr(brain, "voice", None)
        await ws.send_text(json.dumps({
            "type": "mic_state",
            "muted": bool(v.mic_muted) if v else False,
            "has_mic": bool(v and v.engine.has_input)}, ensure_ascii=False))
    except Exception as e:
        log.debug("mic resend: %s", e)
    log.debug("WS accept+broadcast en %.3fs", time.time() - _wst)
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            mtype = data.get("type", "user_message")

            if mtype == "user_message":
                message = data.get("message", "")
                if not message.strip():
                    continue
                # El cliente ve siempre lo que dijo el señor, venga de voz,
                # de la pizarra, de Discord o de WhatsApp.
                await brain.broadcast({"type": "user_heard",
                                       "text": message[:400],
                                       "mode": data.get("input_mode", "text")})
                # Feedback inmediato para el HUD/orbe (antes de bloquearse en el LLM).
                await brain._broadcast({"type": "conversation_state", "state": "thinking"})
                # El LLM es una llamada lenta: se procesa en tarea aparte para que
                # ESTE socket siga leyendo (pings, más mensajes, otros eventos) y el
                # listener no se serialice mientras responde.
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or len(request_id) > 100:
                    request_id = None
                task_id = "live-" + uuid4().hex[:16]
                brain.emit({"type": "live_task_start", "task_id": task_id,
                            "title": str(message).strip()[:120],
                            "message": str(message)[:2000],
                            "description": "Consulta recibida por JARVIS"})
                asyncio.create_task(_process_user_message(
                    message, data.get("input_mode", "text"), bool(data.get("speak", False)),
                    request_id=request_id, live_task_id=task_id))

            elif mtype == "confirmation_response":
                ok = brain.risk.resolve(data.get("action_id", ""),
                                        bool(data.get("approved", False)))
                brain.emit({"type": "confirmation_resolved",
                            "action_id": data.get("action_id"),
                            "approved": bool(data.get("approved", False)), "ok": ok})

            elif mtype == "voice_activate":
                if brain.voice:
                    brain.voice.wake_now()
                    await brain._broadcast({"type": "conversation_state", "state": "listening"})

            elif mtype == "mic_mute":
                muted = bool(data.get("muted", False))
                if brain.voice:
                    brain.voice.set_mic_muted(muted)
                else:
                    await brain._broadcast({"type": "mic_state", "muted": muted})

            elif mtype == "file_preview":
                fpath = data.get("path", "")
                from file_reader import preview as _file_preview
                res = await asyncio.to_thread(_file_preview, fpath)
                await brain._broadcast({"type": "file_preview_result",
                                        "path": fpath, **res})
                # Si el señor suelta el archivo sin decir nada, se presenta solo.
                # Antes se limitara a enseñarlo y habría que preguntar.
                if fpath and data.get("auto") and not data.get("silent"):
                    def _presentar(p=fpath):
                        try:
                            import file_analysis as _fa
                            return _fa.respuesta_rapida(p)
                        except Exception as e:
                            log.debug("presentar archivo: %s", e)
                            return ""
                    texto = await asyncio.to_thread(_presentar)
                    if texto:
                        await brain._broadcast({
                            "type": "assistant_reply", "text": texto,
                            "input_mode": "text"})

            elif mtype == "camera_frame":
                image = data.get("image", "")
                fprompt = data.get("message", "Describe qué ves en esta cámara web.")
                pending = getattr(brain, "_pending_camera_analyze", None)
                if not image:
                    # El overlay no tiene imagen (pizarra cerrada, o el equipo
                    # no tiene Media Foundation). Entonces la saca el cerebro
                    # con OpenCV, que sí funciona.
                    try:
                        import base64 as _b64
                        from vision.webcam import obtener as _wc
                        _jpg = _wc().jpeg(70)
                        if _jpg:
                            log.info("overlay sin frame: tiro con OpenCV")
                            image = _b64.b64encode(_jpg).decode("ascii")
                            fprompt = fprompt or (pending[1] if pending else fprompt)
                    except Exception as e:
                        log.debug("respaldo OpenCV en camera_frame: %s", e)
                if pending and image:
                    _cb, _p = pending[0], pending[1]
                    brain._pending_camera_analyze = None
                    asyncio.create_task(asyncio.to_thread(_cb, image, fprompt or _p))
                elif image:
                    from vision.analyzer import analyze_webcam_frame
                    asyncio.create_task(asyncio.to_thread(
                        lambda: brain.emit({"type": "assistant_reply",
                                            "text": analyze_webcam_frame(image, fprompt)})))

            elif mtype == "ping":
                await brain._broadcast({"type": "pong"})

            elif mtype == "gestures_result":
                # El HUD ha intentado abrir la cámara para el control por gestos.
                if data.get("ok"):
                    await brain._broadcast({
                        "type": "assistant_reply",
                        "text": "Listo, señor: la cámara está abierta y ya le "
                                "estoy leyendo la mano."})
                else:
                    det = str(data.get("error", ""))[:300]
                    log.warning("gestos de cámara: %s", det)
                    await brain._broadcast({
                        "type": "assistant_reply",
                        "text": "No he podido abrir la cámara, señor: " + det})

            elif mtype == "error_report":
                # El overlay (u otro cliente) reporta un error: al bus, y que la
                # reparación siga su curso normal. ANTES se llamaba fix_now() aquí
                # y eso reiniciaba el cerebro entero, tumbando el WebSocket del
                # cliente que acababa de avisar ("cerebro no conectado").
                try:
                    from memory import selfheal as _sh
                    _src = str(data.get("source", "overlay") or "overlay")
                    _sh.report_error(_src,
                                     str(data.get("error", "") or "error sin detalle"),
                                     str(data.get("context", "") or ""))
                    await brain._broadcast({"type": "assistant_reply",
                                            "text": "Recibido, señor. Lo anoto para arreglarlo."})
                    if _src not in ("overlay", "hud", "ui"):
                        _sh.fix_now()
                except Exception as e:
                    log.debug("error_report: %s", e)

            elif mtype == "heal_approval":
                # El señor responde desde el popup Win+J: ¿procedo con el arreglo?
                try:
                    from memory import selfheal as _sh
                    ok = _sh.resolve_approval(str(data.get("id", "") or ""),
                                              bool(data.get("approved", False)))
                    await brain._broadcast({"type": "assistant_reply",
                                            "text": ("Permiso registrado, señor: procedo."
                                                     if data.get("approved") else
                                                     "Entendido, señor: no toco nada.") if ok
                                            else "No hay ningún arreglo pendiente, señor."})
                except Exception as e:
                    log.debug("heal_approval: %s", e)

            elif mtype == "mute_notifications":
                if brain.tools:
                    res = await brain.tools.execute("mute_notifications", {})
                    await brain._broadcast({"type": "assistant_reply",
                                            "text": _tool_result_to_frase("mute_notifications", res)})
    except WebSocketDisconnect:
        pass
    finally:
        brain.clients.discard(ws)


async def _process_user_message(message: str, input_mode: str = "text", speak: bool = False,
                                request_id: Optional[str] = None,
                                live_task_id: Optional[str] = None) -> None:
    """Procesa un mensaje en segundo plano (hilo) y devuelve el orbe a idle al acabar.

    Si speak=True (p. ej. desde la pizarra Win+J), además de emitir la respuesta
    por texto se sintetiza y se dice en alto por voz.

    Barómetro: si tarda <4s, respuesta directa sin avisos. Si necesita pensar,
    avisa ("voy a consultarlo/investigarlo") y cada 25s informa de que sigue
    en proceso, hasta entregar el resultado.
    """
    result = {}
    done = asyncio.Event()

    async def _run():
        try:
            result["reply"] = await asyncio.to_thread(
                handle_user_message, message, input_mode, None, not bool(request_id))
        except Exception as exc:
            log.exception("_process_user_message")
            # Antes se dejaba en "" y el señor se quedaba mirando una
            # conversación sin respuesta. `handle_user_message` ya tiene su
            # propio aviso; esto es el último foso, para lo que se escape.
            # No se enseña el texto crudo de la excepción al usuario.
            log.error("la conversación reventó por una razón inesperada: %s", exc)
            result["reply"] = ("He tropezado contestando, señor. Lo he "
                               "anotado para arreglarlo; vuelva a probarlo.")
        finally:
            done.set()

    async def _say(text, voice_text=None):
        await brain.broadcast({"type": "assistant_reply", "text": text,
                               "request_id": request_id, "persisted": False})
        if speak and brain.voice is not None:
            await asyncio.to_thread(_speak_text, voice_text or text, input_mode != "text")

    async def _progress():
        try:
            await asyncio.wait_for(done.wait(), timeout=ACK_GRACE_SEC)
            return
        except asyncio.TimeoutError:
            pass
        await _say(TEXT_ACK, VOICE_ACK)
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=PROGRESS_EVERY_SEC)
            except asyncio.TimeoutError:
                await _say(PROGRESS_MSG)

    runner = asyncio.create_task(_run())
    try:
        await _progress()
        await runner
        reply = result.get("reply", "")
        if request_id:
            await brain.broadcast({"type": "assistant_reply", "text": reply,
                                  "request_id": request_id, "persisted": True})
        if speak and reply and brain.voice is not None:
            # El texto ya se mostró (input text); solo sonar, sin duplicar.
            await asyncio.to_thread(_speak_text, reply, input_mode != "text")
    finally:
        if live_task_id:
            brain.emit({"type": "live_task_end", "task_id": live_task_id,
                        "title": "Tarea completada" if result.get("reply") else
                                 "La tarea terminó sin respuesta. "
                                 "Consulta los registros para ver el error.",
                        "failed": not bool(result.get("reply"))})
        await brain._broadcast({"type": "conversation_state", "state": "idle"})

def _providers_str() -> str:
    got = []
    if Config.GROQ_API_KEY:
        got.append("Groq")
    if Config.GEMINI_API_KEY:
        got.append("Gemini")
    if Config.OPENROUTER_API_KEY:
        got.append("OpenRouter")
    # Lectura NO bloqueante: el probe de Ollama ocurre en el thread del LLM.
    if brain.router.ollama_cached():
        got.append("Ollama(local)")
    return ", ".join(got) if got else "ninguno"


# --- Interfaz del servidor ---------------------------------------------------
# brain/ui/index.html es la interfaz principal de JARVIS cuando corre como
# servidor (Docker, NAS, sin escritorio). Se sirve en /ui y sus estaticos en
# /ui/<fichero>, como hace /board con la pizarra.
def _ui_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")




@app.get("/")
async def raiz():
    """La raiz manda a la interfaz.

    Antes / devolvia 404 y en un NAS abriste http://nas:8000 y te comias un
    error en blanco, con la interfaz escondida en /ui. Ahora la raiz lleva
    alli, que es lo unico que se abre en el navegador.

    Si no hay ninguna clave de modelo, va antes a /setup. Es el caso normal de
    un contenedor recien creado: el proyecto de Container Manager arranca sin
    .env, y lo unico que hay que hacer a mano es pegar la clave. Que la pagina
    que se abre al poner la IP sea esa, y no un orbe que no contesta nada.
    """
    from fastapi.responses import RedirectResponse
    try:
        if not Config.has_any_key():
            return RedirectResponse("/setup")
    except Exception:
        pass
    return RedirectResponse("/ui")


@app.get("/ui")
@app.get("/ui/")
async def ui_page():
    """La interfaz del orbe, conectada al WebSocket /ws del cerebro."""
    idx = os.path.join(_ui_dir(), "index.html")
    if not os.path.isfile(idx):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h1>La interfaz no esta instalada</h1>"
            "<p>Copia brain/ui dentro del servidor, o reinstala JARVIS.</p>",
            status_code=503)
    from fastapi.responses import HTMLResponse
    with open(idx, "r", encoding="utf-8") as f:
        html = f.read()
    # three.js vive junto a la pagina. Al abrir '/ui' sin barra final, el
    # importmap pediria "three" y no lo resolveria: se apunta al suyo.
    html = html.replace('"three": "./three.module.js"', '"three": "/ui/three.module.js"')
    # El token va dentro: el WebSocket ya no se acepta sin el.
    html = _inyecta_token(html)
    return HTMLResponse(html)


@app.get("/ui/{ruta:path}")
async def ui_static(ruta: str):
    """three.js y demas estaticos de la interfaz, sin salir de brain/ui."""
    # HTTPException vive en fastapi, no en fastapi.responses.
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    base = os.path.realpath(_ui_dir())
    destino = os.path.realpath(os.path.join(base, ruta))
    # Nada fuera de la carpeta: evita el ../../ clasico.
    if not destino.startswith(base + os.sep) and destino != base:
        raise HTTPException(status_code=403, detail="fuera de la interfaz")
    if not os.path.isfile(destino):
        raise HTTPException(status_code=404, detail="no encontrado")
    return FileResponse(destino)


@app.get("/health")
async def health():
    v = brain.voice
    try:
        import sys as _sys
        _me = {"pid": os.getpid(), "exe": _sys.executable,
               "path": [p for p in _sys.path if p][:12]}
    except Exception:
        _me = {"pid": -1, "exe": "?"}
    try:
        _b = brain.discord
        _c = getattr(_b, "client", None) if _b else None
        _discord = {"started": bool(_b),
                    "connected": bool(_c) and not _c.is_closed(),
                    "tries": getattr(_b, "tries", 0) if _b else 0,
                    "token_sha": getattr(_b, "token_sha", None) if _b else None,
                    "last_error": (getattr(_b, "last_error", None) or brain.discord_error)}
        _call = getattr(_b, "voice_call", None) if _b else None
        try:
            _vcc = getattr(_call, "vc", None) if _call else None
            _sink = getattr(_call, "sink", None) if _call else None
            _uids = {}
            try:
                _uids = dict(getattr(_sink, "uids_seen", {}) or {})
            except Exception:
                pass
            try:
                _rec = bool(_vcc.is_recording()) if _vcc else False
            except Exception:
                _rec = None
            try:
                _conn = getattr(_vcc, "_connection", None) if _vcc else None
                _dave = getattr(_conn, "dave_session", None) if _conn else None
                _dave_ready = bool(getattr(_dave, "ready", False)) if _dave else None
                _ssrc_n = len(getattr(_conn, "ssrc_user_map", {}) or {})
            except Exception:
                _dave_ready, _ssrc_n = None, None
            try:
                _rdr = getattr(_vcc, "_reader", None) if _vcc else None
                _r_alive = bool(getattr(getattr(_rdr, "_thread", None), "is_alive", lambda: False)())
            except Exception:
                _r_alive = None
            _discord["call"] = {
                "in_call": bool(_call) and bool(_call.active),
                "utterances": getattr(_call, "utterances", 0) if _call else 0,
                "speaking": bool(getattr(_call, "speaking", False)),
                "frames_total": getattr(_sink, "frames_total", 0) if _sink else 0,
                "frames_owner": getattr(_sink, "frames_owner", 0) if _sink else 0,
                "rms_max": getattr(_sink, "rms_max", 0) if _sink else 0,
                "rms_over": getattr(_sink, "rms_over", 0) if _sink else 0,
                "vad_thr": round(float(getattr(_sink, "vad_thr", 0) or 0)) if _sink else 0,
                "noise_floor": round(float(getattr(_sink, "noise_floor", 0) or 0)) if _sink else 0,
                "uids_seen": {str(k): v for k, v in list(_uids.items())[:6]},
                "is_recording": _rec,
                "dave_ready": _dave_ready,
                "ssrc_map_n": _ssrc_n,
                "reader_alive": _r_alive,
                "last_transcript": (getattr(_call, "last_transcript", "") or "")[:120],
                "last_reply": (getattr(_call, "last_reply", "") or "")[:120],
                "call_error": (getattr(_call, "last_error", None) or "")[:200],
                "vc_connected": bool(_vcc) and _vcc.is_connected() if _vcc else False}
        except Exception as e:
            _discord["call"] = {"error": str(e)[:150]}
    except Exception as e:
        _discord = {"started": False, "connected": False, "error": str(e)[:200]}
    # Que parte de las herramientas sirve aqui. En un NAS es util verlo de un
    # vistazo: si "disponibles" cae, es que algo no se ha montado o falta una
    # clave, y no que JARVIS este roto.
    try:
        _disp = len(brain.tools.disponibles())
        _total = len(brain.tools._registry or {})
    except Exception:
        _disp = _total = 0
    # Memoria persistente: si Mem0 esta caido, JARVIS sigue contestando igual,
    # asi que /health lo dice pero no se pone a "malo". Un healthcheck que
    # fallara por la memoria declararia inservible un cerebro que funciona.
    try:
        _mm = _mem_manager()
        _mem = _mm.estado()
        _mem["alcanzable"] = _mm.health().get("alcanzable", True)
    except Exception as e:
        _mem = {"activado": False, "error": str(e)[:80]}
    return {"status": "ok",
            "voice": bool(v) and v.engine.is_active,
            "voice_input": bool(v) and v.engine.has_input,
            "voice_output": bool(v) and v.engine.has_output,
            "providers": _providers_str(),
            "screen_mode": Config.SCREEN_ANALYSIS_MODE,
            "herramientas": {"total": _total, "disponibles": _disp,
                             "no_disponibles": _total - _disp},
            "memoria": _mem,
            "discord": _discord,
            "me": _me}


@app.post("/wa")
async def wa_endpoint(payload: dict):
    """Puente de WhatsApp (self-chat): procesa un mensaje y devuelve la reply
    en la respuesta HTTP, SIN difundirla al HUD/orbe. Solo localhost.

    Entrada: {message?, sender, audio_b64? (ogg/opus nota de voz),
              image_b64? (foto), voice_reply? bool}
    Salida: {ok, reply, audio_b64? (ogg/opus o None)}
    """
    import base64 as _b64
    text = str(payload.get("message", "") or "").strip()
    sender = str(payload.get("sender", "") or "").strip()
    audio_b64 = str(payload.get("audio_b64", "") or "")
    image_b64 = str(payload.get("image_b64", "") or "")
    voice_reply = bool(payload.get("voice_reply", False))
    if not sender or (not text and not audio_b64 and not image_b64):
        return {"ok": False, "reason": "sender y (message|audio_b64|image_b64) requeridos"}

    heard_from_voice = False
    if audio_b64 and not text:
        try:
            from voice.wa_media import transcribe_ogg, b64_to_bytes
            text = transcribe_ogg(b64_to_bytes(audio_b64)) or ""
            heard_from_voice = True
        except Exception as e:
            log.debug("wa audio: %s", e)
        if not text:
            return {"ok": True, "reply": "Le oí, señor, pero no entendí el audio. Repítamelo.",
                    "audio_b64": None, "heard": "voice"}

    # Ritual Despertar inline: narración completa como texto (sin runner).
    if text and _WAKE_UP_RE.search(text):
        from routines import wake_up
        lines: list[str] = []

        def _collect(t: str):
            lines.append(t)

        await asyncio.to_thread(wake_up.run, lambda m: None, _collect,
                                None, False)
        brain.emit({"type": "action_status", "label": "Protocolo matutino",
                    "state": "done"})
        reply = "De inmediato, señor. Le preparo la mañana…\n" + "\n".join(lines)
        out_audio = None
        if voice_reply and reply:
            try:
                from voice.wa_media import synthesize_ogg
                out_audio = _b64.b64encode(synthesize_ogg(reply)).decode()
            except Exception as e:
                log.debug("wa tts: %s", e)
        return {"ok": True, "reply": reply, "audio_b64": out_audio,
                "heard": "voice" if heard_from_voice else "text"}

    if image_b64:
        try:
            from vision.analyzer import analyze_webcam_frame
            prompt = text or "Describe qué se ve en esta foto."
            reply = analyze_webcam_frame(image_b64, prompt)
            out_audio = None
            if voice_reply and reply:
                try:
                    from voice.wa_media import synthesize_ogg
                    out_audio = _b64.b64encode(synthesize_ogg(reply)).decode()
                except Exception as e:
                    log.debug("wa tts: %s", e)
            return {"ok": True, "reply": reply, "audio_b64": out_audio,
                    "heard": "image"}
        except Exception as e:
            log.debug("wa image: %s", e)
            return {"ok": True, "reply": "No he podido ver la foto, señor. Envíemela de nuevo.",
                    "audio_b64": None, "heard": "image"}

    reply = await asyncio.to_thread(handle_user_message, text, "whatsapp",
                                    None, False)
    out_audio = None
    if (voice_reply or heard_from_voice) and reply:
        try:
            from voice.wa_media import synthesize_ogg
            out_audio = _b64.b64encode(synthesize_ogg(reply)).decode()
        except Exception as e:
            log.debug("wa tts: %s", e)
    return {"ok": True, "reply": reply, "audio_b64": out_audio,
            "heard": "voice" if heard_from_voice else "text"}


_MIC_PAGE = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>JARVIS · Micrófono</title>
<style>body{background:#0b1020;color:#e8ecf8;font-family:Segoe UI,Arial;text-align:center;padding:40px}
button{font-size:22px;padding:14px 34px;border-radius:12px;border:0;cursor:pointer;background:#2f6fed;color:#fff}
button.rec{background:#c0392b}#out{margin:24px auto;max-width:640px;text-align:left;white-space:pre-wrap}</style>
</head><body><h2>JARVIS · Hablar por micrófono</h2>
<button id="b">🎙 Grabar</button><div id="out">Pulse Grabar, hable y pulse Detener.</div>
<script>
let rec=null,chunks=[],stream=null;const b=document.getElementById('b'),out=document.getElementById('out');
b.onclick=async()=>{
 if(rec&&rec.state==='recording'){rec.stop();b.textContent='🎙 Grabar';b.classList.remove('rec');return;}
 try{stream=await navigator.mediaDevices.getUserMedia({audio:true});}catch(e){out.textContent='Sin acceso al micro: '+e;return;}
 chunks=[];rec=new MediaRecorder(stream);rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
 rec.onstop=async()=>{
  stream.getTracks().forEach(t=>t.stop());
  const blob=new Blob(chunks,{type:rec.mimeType||'audio/webm'});
  const buf=await blob.arrayBuffer();
  let bin='';const bytes=new Uint8Array(buf);
  for(let i=0;i<bytes.length;i+=8192){bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+8192));}
  out.textContent='Transcribiendo…';
  try{
   const r=await fetch('/mic_audio',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({audio_b64:btoa(bin)})});
   const j=await r.json();
   out.textContent='Usted: '+(j.text||'(nada)')+'\n\nJARVIS: '+(j.reply||'(sin respuesta)');
   if(j.reply&&'speechSynthesis' in window){speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(j.reply));}
  }catch(e){out.textContent='Error: '+e;}
 };
 rec.start();b.textContent='⏹ Detener';b.classList.add('rec');
 setTimeout(()=>{if(rec&&rec.state==='recording')rec.stop();},15000);
};
</script></body></html>"""


@app.get("/mic")
async def mic_page():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_MIC_PAGE)


@app.post("/mic_audio")
async def mic_audio(payload: dict):
    """Audio del navegador (webm/opus base64) -> texto -> respuesta.
    Entrada: {audio_b64}. Salida: {ok, text, reply}."""
    import base64 as _b64
    raw = str(payload.get("audio_b64", "") or "")
    if not raw:
        return {"ok": False, "reason": "audio_b64 requerido"}
    try:
        data = _b64.b64decode(raw)
    except Exception:
        return {"ok": False, "reason": "base64 inválido"}
    try:
        from voice.stt import transcribe_bytes
        text = await asyncio.to_thread(transcribe_bytes, data)
    except Exception as e:
        log.debug("mic stt: %s", e)
        text = ""
    if not (text or "").strip():
        return {"ok": True, "text": "", "reply": "No le oí, señor. Repítamelo."}
    reply = await asyncio.to_thread(handle_user_message, text, "voice",
                                    None, False)
    try:
        brain.emit({"type": "assistant_reply", "text": reply})
    except Exception:
        pass
    return {"ok": True, "text": text, "reply": reply}


_CONFIG_DEFAULTS = {
    "theme": {"accent": "#2f6fed", "bg": "#0b1020", "ink": "#e8ecf8",
              "orb1": "#38e1ff", "orb2": "#7a5cff"},
    "messages": {"user_bg": "#2f6fed", "jarvis_bg": "#1a2340", "sys_color": "#8b93ad",
                 "radius": 12, "font": 15},
    "orb": {"enabled": True, "size": 1.0, "speed": 1.0},
    "anims": {"enabled": True, "intensity": 70, "msg_anim": "fade",
              "orb_anim": "pulse"},
    "perf": {"context_window": 15, "recall": True, "screen_mode": "continuous",
             "screen_interval": 5},
    "humor": 65,
    "speech": {"style": "mayordomo"},
    "voice": {"tts_speed": 1.12, "read_aloud": True, "clap_enabled": False},
}

_CFG_SECTIONS = ("theme", "messages", "orb", "anims", "perf",
                 "speech", "voice")

_API_KEYS = ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
             "WEATHER_API_KEY", "DISCORD_BOT_TOKEN", "DISCORD_OWNER_ID",
             "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
             "VIRUSTOTAL_API_KEY", "VBOX_GUEST_USER", "VBOX_GUEST_PASSWORD",
             "CAMPUS_USER", "CAMPUS_PASSWORD", "NTIFY_TOPIC"]


def _cfg_get_all() -> dict:
    from memory import sqlite_store as _db
    out = {"theme": dict(_CONFIG_DEFAULTS["theme"]),
           "messages": dict(_CONFIG_DEFAULTS["messages"]),
           "orb": dict(_CONFIG_DEFAULTS["orb"]),
           "anims": dict(_CONFIG_DEFAULTS["anims"]),
           "perf": dict(_CONFIG_DEFAULTS["perf"]),
           "humor": _CONFIG_DEFAULTS["humor"],
           "speech": dict(_CONFIG_DEFAULTS["speech"]),
           "voice": dict(_CONFIG_DEFAULTS["voice"]),
           "apis": {}}
    try:
        import json as _js
        raw = _db.get_setting("jarvis_config", "")
        if raw:
            saved = _js.loads(raw)
            if isinstance(saved, dict):
                for k in _CFG_SECTIONS:
                    if isinstance(saved.get(k), dict):
                        out[k].update(saved[k])
                if isinstance(saved.get("humor"), int):
                    out["humor"] = max(0, min(100, saved["humor"]))
    except Exception as e:
        log.debug("cfg load: %s", e)
    try:
        import os as _os
        for k in _API_KEYS:
            v = (_os.environ.get(k, "") or "").strip()
            out["apis"][k] = {"set": bool(v), "len": len(v)}
    except Exception:
        pass
    return out


def _apply_perf(saved: dict) -> None:
    """Aplica ajustes de rendimiento en caliente (sin reinicio)."""
    try:
        p = saved.get("perf", {}) or {}
        if "context_window" in p:
            Config.CONTEXT_WINDOW_MESSAGES = max(3, min(40, int(p["context_window"])))
        if "recall" in p:
            Config.RECALL_ENABLED = bool(p["recall"])
    except Exception as e:
        log.debug("perf apply: %s", e)


def _cfg_save(payload: dict) -> dict:
    import json as _js
    import os as _os
    from memory import sqlite_store as _db
    saved = {}
    try:
        raw = _db.get_setting("jarvis_config", "")
        if raw:
            saved = _js.loads(raw) or {}
    except Exception:
        saved = {}
    for k in _CFG_SECTIONS:
        if isinstance(payload.get(k), dict):
            saved.setdefault(k, {}).update(payload[k])
    if isinstance(payload.get("humor"), int):
        saved["humor"] = max(0, min(100, payload["humor"]))
        _humor_cache["t"] = 0.0
    _apply_perf(saved)
    try:
        _ts = float((payload.get("voice") or {}).get("tts_speed") or 0)
        if 0.7 <= _ts <= 1.5:
            Config.TTS_SPEED = _ts
    except Exception:
        pass
    # Interruptor de palmadas: se aplica en caliente para que el dashboard
    # sirva al momento, sin reiniciar. Solo toca el detector, nada mas.
    # Si falla se avisa en warning, no en debug: un ajuste que el señor acaba
    # de tocar y no se aplica no puede fallar en silencio. Asi se encontro un
    # NameError que habria quedado escondido una semana.
    try:
        _v = payload.get("voice") or {}
        if isinstance(_v, dict) and "clap_enabled" in _v:
            from voice import clap as _clap
            _on = bool(_v.get("clap_enabled"))
            _clap.aplicar_preferencia(_on)
            log.info("Activacion por palmadas %s.",
                     "activada" if _on else "desactivada")
    except Exception as e:
        log.warning("No se pudo aplicar la opcion de palmadas: %s: %s",
                    type(e).__name__, e)
    try:
        _db.set_setting("jarvis_config", _js.dumps(saved, ensure_ascii=False))
    except Exception as e:
        return {"ok": False, "reason": f"prefs: {e}"}
    apis = payload.get("apis") or {}
    if isinstance(apis, dict) and apis:
        for path in (_keys_env_path(), str(BRAIN_DIR / ".env")):
            try:
                lines = []
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8-sig") as f:
                        lines = f.read().splitlines()
                # Una clave repetida se queda con su primera linea y las
                # demas se borran al guardar. Antes solo se actualizaba la
                # ultima y el duplicado sobrevivia para siempre.
                have = {}
                sobrantes = set()
                for i, ln in enumerate(lines):
                    if "=" in ln and not ln.strip().startswith("#"):
                        k = ln.split("=", 1)[0].strip()
                        if k in have:
                            sobrantes.add(i)
                        else:
                            have[k] = i
                for k, v in apis.items():
                    if k not in _API_KEYS:
                        continue
                    v = (str(v or "")).strip()
                    if not v:
                        continue
                    if k in have:
                        lines[have[k]] = f"{k}={v}"
                    else:
                        lines.append(f"{k}={v}")
                        have[k] = len(lines) - 1
                    _os.environ[k] = v
                    # Config se evalua al importar el modulo, asi que cambiar
                    # solo os.environ no hace nada hasta reiniciar. En un NAS
                    # eso significa reiniciar el contenedor entero para poder
                    # usar la clave que acabas de pegar, asi que se actualiza
                    # la clase tambien y funciona al instante.
                    setattr(Config, k, v)
                if sobrantes:
                    lines = [ln for i, ln in enumerate(lines) if i not in sobrantes]
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
            except Exception as e:
                log.debug("cfg keys %s: %s", path, e)
    return {"ok": True}


def _keys_env_path() -> str:
    """Donde se guardan las claves que se ponen desde /setup y /config.

    En Windows es %APPDATA%/JARVIS/keys.env, fuera de la instalacion para que
    sobrevivan a reinstalar. En Linux y en Docker no hay APPDATA, asi que antes
    esta funcion devolvia "" y las claves se guardaban solo en brain/.env,
    DENTRO del contenedor: se perdian al recrearlo y habia que volver a ponerlas
    en cada actualizacion. Ahora caen en DATA_DIR, que es el volumen.
    """
    try:
        base = os.environ.get("APPDATA", "")
        if base:
            return os.path.join(base, "JARVIS", "keys.env")
        return str(DATA_DIR / "keys.env")
    except Exception:
        return ""


@app.get("/api/config")
async def api_config_get():
    return {"ok": True, "config": _cfg_get_all()}


@app.post("/api/config")
async def api_config_post(payload: dict):
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "JSON requerido"}
    return _cfg_save(payload)


def _parsear_env(texto: str) -> dict:
    """Lee un keys.env: lineas CLAVE=valor, ignorando comentarios y vacías.
    Se parte solo por la primera '=' porque los valores pueden llevarla dentro
    (una contraseña, un token con padding)."""
    salida = {}
    for ln in str(texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k = k.strip()
        v = v.strip()
        # Las comillas sonenvuelven el valor, no son parte de él.
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k and v:
            salida[k] = v
    return salida


@app.post("/api/config/importar-claves")
async def api_config_importar(payload: dict):
    """Trae un keys.env de otro equipo. Se ignoran las claves que JARVIS no
    conoce, para no escribir basura en el fichero."""
    texto = (payload or {}).get("texto", "")
    if not texto:
        return {"ok": False, "reason": "No llegó el contenido del fichero"}
    par = _parsear_env(texto)
    conocidas = {k: v for k, v in par.items() if k in _API_KEYS}
    desconocidas = sorted(set(par) - set(conocidas))
    if not conocidas:
        return {"ok": False, "reason": "El fichero no contenía ninguna clave de JARVIS",
                "desconocidas": desconocidas}
    r = _cfg_save({"apis": conocidas})
    if not r.get("ok"):
        return r
    log.info("claves importadas: %s", ", ".join(sorted(conocidas)))
    return {"ok": True, "importadas": sorted(conocidas),
            "ignoradas": desconocidas, "total": len(conocidas)}


@app.post("/api/config/exportar-claves")
async def api_config_exportar(request: Request):
    """Devuelve las claves en formato keys.env para llevarlas a otro equipo.

    A POST y no a GET a propósito: así no se puede '--' filtrar por un enlace
    ni por una precarga, y solo se pide de forma explicita.

    Y ahora con el token, que antes no lo llevaba. Esta ruta devolvia TODAS las
    claves de API en claro a cualquiera que llegara al puerto 8000: bastaba un
    POST. El token lo manda la pagina de configuracion, que es la unica que
    deberia llamarla.
    """
    if not _exige_token(request):
        return JSONResponse(
            content={"ok": False, "reason": "Falta el token. Recarga la pagina "
                                           "de configuracion."},
            status_code=401)
    import os as _os
    lineas = []
    for k in _API_KEYS:
        v = (_os.environ.get(k, "") or "").strip()
        if v:
            lineas.append(f"{k}={v}")
    if not lineas:
        return {"ok": False, "reason": "No hay ninguna clave puesta todavia"}
    return {"ok": True, "texto": "\n".join(lineas) + "\n",
            "total": len(lineas)}


@app.get("/api/camara/estado")
async def api_camara_estado(request: Request):
    try:
        from vision.webcam import obtener as _wc
        estado = _wc().estado()
        return JSONResponse(content={"ok": True, "estado": estado}, headers={
            "Access-Control-Allow-Origin": _origen_camara(request),
        })
    except Exception as e:
        return JSONResponse(content={"ok": False, "reason": str(e)[:200]}, headers={
            "Access-Control-Allow-Origin": _origen_camara(request),
        })


@app.options("/api/camara/estado")
@app.options("/api/camara/mjpeg")
async def api_camara_cors_preflight(request: Request):
    return Response(status_code=204, headers={
        "Access-Control-Allow-Origin": _origen_camara(request),
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Max-Age": "600",
    })


@app.post("/api/camara/cerrar")
async def api_camara_cerrar(request: Request):
    """Suelta la cámara del cerebro.

    El tracker la deja cogida 10 s tras el último uso, y mientras la tenga nadie
    más puede abrirla (las cámaras USB de este equipo no se comparten): Zoom,
    Meet o el propio navegador se quedan sin cámara por culpa de JARVIS. Con
    esto se puede liberar al momento.
    """
    try:
        from vision.webcam import obtener as _wc
        wc = _wc()
        wc.cerrar()
        return {"ok": True, "estado": wc.estado()}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


@app.get("/api/camara/foto.jpg")
async def api_camara_foto(calidad: int = 60, request: Request = None):
    """Foto de la cámara capturada por el cerebro (OpenCV), no por el navegador.

    Es la vía que funciona en equipos donde Chromium no abre la cámara, porque
    no tiene Media Foundation. La consume el tracker de gestos.
    """
    import asyncio
    try:
        from vision.webcam import obtener as _wc
        jpg = await asyncio.to_thread(_wc().jpeg, max(30, min(90, int(calidad))))
    except Exception as e:
        return JSONResponse(status_code=503,
                            content={"ok": False, "reason": str(e)[:200]},
                            headers={"Access-Control-Allow-Origin": _origen_camara(request)})
    # OJO: aquí NO se cierra la cámara. Se llegó a cerrar tras cada foto para no
    # secuestrársela al navegador, pero abrir y cerrarla sin parar deja el
    # dispositivo delivering un fotograma y luego ceros, que es peor: mataba
    # la cámara entera. Ahora la suelta el hilo de reposo tras 10 s sin uso.
    if not jpg:
        return JSONResponse(status_code=503,
                            content={"ok": False, "reason": "la cámara no entrega imagen"},
                            headers={"Access-Control-Allow-Origin": _origen_camara(request)})
    return Response(content=jpg, media_type="image/jpeg", headers={
        "Access-Control-Allow-Origin": _origen_camara(request),
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    })


# Vídeo en directo de la cámara, servido por el cerebro (MJPEG).
#
# Existe por un motivo concreto: en este equipo la cámara va bien por DirectShow
# (verificada), pero si el navegador la pide él mismo, el servicio de cámara de
# Windows revienta y se lleva por delante el dispositivo. Con esta vía la cámara
# solo la toca el cerebro, que usa la ruta que sí funciona, y el renderer se
# limita a mirar este vídeo. Además va a 15 fps de verdad, frente a las 3-5 fps
# que daba antes pedir fotos una a una por HTTP (y por eso no cogía las manos).
@app.get("/api/camara/mjpeg")
async def api_camara_mjpeg(request: Request):
    from fastapi.responses import StreamingResponse

    async def generar():
        import asyncio
        try:
            import cv2
            from vision.webcam import obtener as _wc
        except Exception:
            return
        wc = _wc()
        borde = "marcojarvis"
        try:
            # Bucle de fotogramas a ~15 fps mientras haya alguien mirando. Margen amplio:
            # el modelo de manos tarda en descargarse la primera vez, y si el
            # vídeo se acabara a los 5 minutos el panel se quedaría en negro
            # justo mientras el señor está poniendo la mano.
            for _ in range(15 * 3600):  # hasta 1 hora
                jpg = await asyncio.to_thread(wc.jpeg, 70)
                if not jpg:
                    await asyncio.sleep(0.25)
                    continue
                yield (f"--{borde}\r\nContent-Type: image/jpeg\r\n"
                       f"Content-Length: {len(jpg)}\r\n\r\n").encode() + jpg + b"\r\n"
                await asyncio.sleep(1 / 15)
        except Exception:
            pass
        finally:
            try:
                wc.cerrar()
            except Exception:
                pass

    return StreamingResponse(generar(), media_type="multipart/x-mixed-replace; boundary=marcojarvis",
                             headers={"Access-Control-Allow-Origin": _origen_camara(request),
                                      "Access-Control-Allow-Methods": "GET, OPTIONS",
                                      "Access-Control-Allow-Headers": "*",
                                      "Access-Control-Allow-Private-Network": "true",
                                      "Cache-Control": "no-store, no-revalidate",
                                      "Pragma": "no-cache",
                                      "Connection": "close"})


@app.get("/api/tools")
async def api_tools():
    """Todas las herramientas, incluidas las que no pueden funcionar aqui.

    Se listan todas para que la forma de la respuesta sea la misma en el
    escritorio y en un NAS, pero cada una lleva "disponible" y, si no lo es,
    el motivo. Asi se puede comprobar de un vistazo que funciona el 95% y que
    lo que falta es por plataforma, no por instalacion.
    """
    try:
        motivos = {}
        try:
            for t in brain.tools.no_disponibles():
                motivos[t["name"]] = t.get("motivo", "")
        except Exception:
            pass
        names = []
        for t in (brain.tools._registry or {}).values():
            n = t.get("name")
            fila = {"name": n, "description": t.get("description", ""),
                    "parameters": t.get("parameters", {}),
                    "disponible": n not in motivos}
            if n in motivos:
                fila["motivo"] = motivos[n]
            names.append(fila)
        names.sort(key=lambda x: x["name"] or "")
        return {"ok": True, "tools": names,
                "total": len(names),
                "disponibles": sum(1 for x in names if x["disponible"]),
                "no_disponibles": sorted(motivos.keys())}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


@app.get("/api/protocols")
async def api_protocols():
    try:
        from memory import protocols as _prot
        return {"ok": True, "protocols": _prot.list_all()}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


@app.post("/api/protocols")
async def api_protocols_add(payload: dict):
    try:
        from memory import protocols as _prot
        return {"ok": True, "protocol": _prot.add(
            payload.get("name", ""), payload.get("triggers", []),
            payload.get("actions", []), payload.get("say", ""))}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


@app.delete("/api/protocols")
async def api_protocols_del(name: str = ""):
    try:
        from memory import protocols as _prot
        ok = _prot.remove(name)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


_CONFIG_PAGE = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS · Configuración</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
 --void:#04070d;--hull:#080e1a;--glass:rgba(15,29,49,.58);--glass-2:rgba(11,22,38,.72);
 --edge:rgba(95,212,255,.16);--edge-2:rgba(95,212,255,.34);
 --cyan:#5fd4ff;--cyan-deep:#1b6c8f;--amber:#ffb454;--rose:#ff6b6b;--jade:#57d9a3;
 --ink:#dcebf7;--mut:#7d95ab;
 --disp:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;
 --mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
}
body{background:var(--void);color:var(--ink);font-family:var(--disp);min-height:100vh;
 background-image:radial-gradient(1100px 620px at 18% -12%,rgba(27,108,159,.30),transparent 62%),
 radial-gradient(900px 520px at 92% 8%,rgba(255,180,84,.10),transparent 60%),
 linear-gradient(180deg,#060b14,var(--void) 46%)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
 background-image:linear-gradient(rgba(95,212,255,.045) 1px,transparent 1px),
 linear-gradient(90deg,rgba(95,212,255,.045) 1px,transparent 1px);background-size:46px 46px;
 mask-image:radial-gradient(1200px 700px at 50% 0%,#000 20%,transparent 78%)}
.shell{position:relative;z-index:1;max-width:1180px;margin:0 auto;padding:30px 22px 70px}

.hero{display:flex;align-items:center;gap:34px;flex-wrap:wrap;padding:6px 4px 30px;
 border-bottom:1px solid var(--edge);margin-bottom:26px}
.mark{font-family:var(--mono);font-size:12px;letter-spacing:.42em;color:var(--cyan-deep)}
.hero h1{font-size:clamp(28px,4.4vw,44px);font-weight:600;letter-spacing:-.02em;line-height:1.05;margin:8px 0 10px}
.hero p{color:var(--mut);max-width:46ch;font-size:15px;line-height:1.55}
.live{display:inline-flex;align-items:center;gap:9px;margin-top:16px;padding:6px 14px 6px 10px;
 border:1px solid var(--edge);border-radius:999px;background:var(--glass);font-family:var(--mono);font-size:12px;color:var(--mut)}
.pip{width:7px;height:7px;border-radius:50%;background:var(--jade);box-shadow:0 0 10px var(--jade);
 animation:beat 2.4s ease-in-out infinite}
@keyframes beat{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.72)}}

.core{position:relative;width:232px;height:232px;flex:0 0 auto;perspective:1000px;transform-style:preserve-3d}
.core-halo{position:absolute;inset:-26%;border-radius:50%;
 background:radial-gradient(circle,rgba(95,212,255,.20),transparent 66%);filter:blur(6px)}
.plane{position:absolute;inset:0;transform-style:preserve-3d}
.ring{position:absolute;inset:0;border-radius:50%;border:1px solid var(--edge-2)}
.ring::after{content:"";position:absolute;inset:9px;border-radius:50%;border:1px dashed rgba(95,212,255,.16)}
.p1{transform:rotateX(76deg)}
.p2{transform:rotateY(74deg) rotateZ(14deg)}
.p3{transform:rotateX(26deg) rotateY(38deg)}
.p3 .ring{border-color:rgba(255,180,84,.30)}
.p3 .ring::after{border-color:rgba(255,180,84,.14)}
.sa{animation:gyro 15s linear infinite}
.sb{animation:gyro 9s linear infinite reverse}
.sc{animation:gyro 21s linear infinite}
@keyframes gyro{to{transform:rotate(360deg)}}
.sphere{position:absolute;left:50%;top:50%;width:74px;height:74px;margin:-37px 0 0 -37px;border-radius:50%;
 transform:translateZ(26px);box-shadow:0 0 46px rgba(95,212,255,.34),inset 0 0 22px rgba(255,255,255,.12);
 transition:opacity .25s ease}
.sphere.off{opacity:0}

.body{display:grid;grid-template-columns:216px 1fr;gap:26px;align-items:start}
.spine{display:flex;flex-direction:column;gap:7px;position:sticky;top:22px}
.tab{position:relative;text-align:left;padding:11px 15px 11px 22px;font-family:var(--disp);font-size:14.5px;
 color:var(--mut);background:transparent;border:0;cursor:pointer;transition:color .18s ease,transform .18s ease;
 clip-path:polygon(0 0,100% 0,calc(100% - 13px) 100%,0 100%)}
.tab::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:transparent;transition:background .18s ease}
.tab:hover{color:var(--ink);transform:translateX(3px)}
.tab.on{color:var(--cyan);background:linear-gradient(90deg,rgba(95,212,255,.13),transparent)}
.tab.on::before{background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.tab:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}

.panels{position:relative;min-height:440px}
.panel{display:none}
.panel.on{display:block;animation:rise .32s cubic-bezier(.22,.8,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(11px)}to{opacity:1;transform:none}}

.block{background:var(--glass);border:1px solid var(--edge);border-radius:16px;padding:20px 22px;margin-bottom:16px;
 backdrop-filter:blur(9px);-webkit-backdrop-filter:blur(9px);box-shadow:0 18px 44px rgba(0,0,0,.34)}
.block h2{font-size:17px;font-weight:600;letter-spacing:-.01em;margin-bottom:5px}
.block .d{color:var(--mut);font-size:13.5px;line-height:1.5;margin-bottom:16px}
.row{display:flex;gap:12px;align-items:center;padding:8px 0;flex-wrap:wrap}
.row+.row{border-top:1px solid rgba(95,212,255,.08)}
.row>label{flex:0 0 210px;color:var(--mut);font-size:13.5px}
.row .grow{flex:1;min-width:190px}
input[type=text],input[type=password],input[type=number],select,textarea{
 background:rgba(4,9,16,.72);color:var(--ink);border:1px solid var(--edge);border-radius:9px;
 padding:9px 11px;font-family:var(--disp);font-size:14px;width:100%}
textarea{min-height:104px;font-family:var(--mono);font-size:12.5px;line-height:1.5;resize:vertical}
input[type=color]{width:54px;height:36px;padding:2px;border:1px solid var(--edge);border-radius:9px;background:rgba(4,9,16,.72);cursor:pointer}
input[type=range]{flex:1;min-width:150px;accent-color:var(--cyan)}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--cyan);cursor:pointer}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--cyan);box-shadow:0 0 0 3px rgba(95,212,255,.14)}
.hint{font-family:var(--mono);font-size:12px;color:var(--cyan-deep);min-width:52px}
button{font-family:var(--disp);cursor:pointer;border:0;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;
 color:#041018;background:linear-gradient(180deg,#7fdcff,#3ba7cf);transition:transform .15s ease,box-shadow .15s ease}
button:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(95,212,255,.28)}
button.ghost{background:rgba(95,212,255,.08);color:var(--ink);border:1px solid var(--edge);font-weight:500;font-size:13px;padding:8px 15px}
button.ghost:hover{background:rgba(95,212,255,.15)}
button.danger{background:rgba(255,107,107,.10);color:#ffb3b3;border:1px solid rgba(255,107,107,.34);font-weight:500;font-size:13px;padding:7px 14px}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid rgba(95,212,255,.1)}
.badge{font-family:var(--mono);font-size:11.5px;padding:3px 10px;border-radius:999px;background:rgba(87,217,163,.14);color:var(--jade);border:1px solid rgba(87,217,163,.3)}
.badge.off{background:rgba(255,107,107,.10);color:#ffb3b3;border-color:rgba(255,107,107,.3)}

#demo{border:1px solid var(--edge);border-radius:13px;padding:18px;background:rgba(3,7,13,.6);min-height:210px}
#demo .orbwrap{display:flex;justify-content:center;padding:6px 0 16px}
#demoOrb{width:74px;height:74px;border-radius:50%;box-shadow:0 0 30px rgba(95,212,255,.34)}
.bub{max-width:80%;padding:9px 13px;border-radius:13px;margin:7px 0;font-size:15px;line-height:1.45;border:1px solid transparent}
.bub.u{margin-left:auto}
.bub.j{margin-right:auto}
.bub.s{margin:10px auto 0;text-align:center;font-size:12.5px;background:none!important;border:0;padding:0}

.proto{display:flex;align-items:center;gap:12px;justify-content:space-between;padding:11px 13px;margin:8px 0;
 border:1px solid var(--edge);border-radius:11px;background:rgba(4,9,16,.5);font-size:13.5px}
.proto b{color:var(--cyan);font-weight:600}
.proto .meta{color:var(--mut);font-family:var(--mono);font-size:12px}
.empty{color:var(--mut);font-size:13.5px;padding:14px 0}
#toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);z-index:50;padding:12px 24px;border-radius:11px;
 background:rgba(6,14,24,.95);border:1px solid var(--edge-2);color:var(--ink);font-size:14px;opacity:0;
 pointer-events:none;transition:opacity .25s ease,transform .25s ease;box-shadow:0 14px 34px rgba(0,0,0,.5)}
#toast.on{opacity:1;transform:translate(-50%,0)}
#toast.bad{border-color:rgba(255,107,107,.5);color:#ffc9c9}

@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes slideIn{from{opacity:0;transform:translateX(-18px)}to{opacity:1;transform:none}}
@keyframes pulseOrb{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
@keyframes spinOrb{to{transform:rotate(360deg)}}
@keyframes glowOrb{0%,100%{filter:brightness(1)}50%{filter:brightness(1.5)}}
@media(max-width:860px){
 .body{grid-template-columns:1fr}
 .spine{position:static;flex-direction:row;overflow-x:auto;padding-bottom:6px}
 .tab{clip-path:none;border:1px solid var(--edge);border-radius:9px;white-space:nowrap;padding:9px 14px}
 .tab::before{display:none}
 .row>label{flex:0 0 100%}
 .core{width:180px;height:180px;margin:0 auto}
}
@media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
</style></head><body><div class="shell">
<header class="hero">
  <div class="core" aria-hidden="true">
    <div class="core-halo"></div>
    <div class="plane p1"><div class="ring sa"></div></div>
    <div class="plane p2"><div class="ring sb"></div></div>
    <div class="plane p3"><div class="ring sc"></div></div>
    <div class="sphere" id="demoOrb"></div>
  </div>
  <div class="hero-txt">
    <div class="mark">J.A.R.V.I.S</div>
    <h1>Puesto de mando</h1>
    <p>Ajuste su identidad, su voz y lo que hace por su cuenta sin tocar un archivo.</p>
    <div class="live"><span class="pip"></span><span id="liveTxt">cerebro en línea</span></div>
  </div>
</header>

<div class="body">
<nav class="spine" role="tablist" aria-label="Secciones">
  <button class="tab on" role="tab" data-tab="identidad">Identidad</button>
  <button class="tab" role="tab" data-tab="voz">Voz</button>
  <button class="tab" role="tab" data-tab="orbe">Orbe</button>
  <button class="tab" role="tab" data-tab="movimiento">Movimiento</button>
  <button class="tab" role="tab" data-tab="automatizaciones">Automatizaciones</button>
  <button class="tab" role="tab" data-tab="claves">Claves</button>
  <button class="tab" role="tab" data-tab="memoria">Memoria</button>
  <button class="tab" role="tab" data-tab="sistema">Sistema</button>
</nav>

<div class="panels">

<section class="panel on" id="p-identidad">
  <div class="block">
    <h2>Vista previa</h2>
    <p class="d">Así se verá en la pizarra Win+J con lo que hay guardado ahora mismo.</p>
    <div id="demo">
      <div class="orbwrap"><div id="demoOrb2"></div></div>
      <div class="bub u" data-anim>¿qué hora es en la raspberry?</div>
      <div class="bub j" data-anim>Las 14:32, señor. El reloj ya va sincronizado.</div>
      <div class="bub s" data-anim>Sistemas al 100%.</div>
    </div>
    <div class="actions"><button class="ghost" onclick="replayDemo()">Reproducir</button></div>
  </div>
  <div class="block">
    <h2>Colores del holograma</h2>
    <p class="d">El turquesa del orbe y de los paneles. Se ve al instante en la vista previa.</p>
    <div class="row"><label>Acento</label><input type="color" id="c_accent"></div>
    <div class="row"><label>Fondo</label><input type="color" id="c_bg"></div>
    <div class="row"><label>Texto</label><input type="color" id="c_ink"></div>
    <div class="row"><label>Núcleo del orbe</label><input type="color" id="c_orb1"></div>
    <div class="row"><label>Borde del orbe</label><input type="color" id="c_orb2"></div>
    <div class="actions"><button onclick="saveTheme()">Guardar colores</button></div>
  </div>
  <div class="block">
    <h2>Burbujas del chat</h2>
    <p class="d">Fondo, borde y tamaño de lo que dice usted y lo que responde él.</p>
    <div class="row"><label>Su mensaje</label><input type="color" id="m_user"></div>
    <div class="row"><label>Respuesta de JARVIS</label><input type="color" id="m_jarvis"></div>
    <div class="row"><label>Avisos del sistema</label><input type="color" id="m_sys"></div>
    <div class="row"><label>Redondeo</label><input type="number" id="m_radius" min="0" max="24" value="12"> <span class="hint">px</span></div>
    <div class="row"><label>Tamaño de letra</label><input type="number" id="m_font" min="11" max="22" value="15"> <span class="hint">px</span></div>
    <div class="actions"><button onclick="saveMsgs()">Guardar burbujas</button></div>
  </div>
</section>

<section class="panel" id="p-voz">
  <div class="block">
    <h2>Carácter</h2>
    <p class="d">Dry British a 65. Más bajo, menos chistes. Más alto, más descaro.</p>
    <div class="row"><label>Humor: <b id="hval">65</b></label><input type="range" id="h_humor" min="0" max="100" value="65" oninput="document.getElementById('hval').textContent=this.value"></div>
    <div class="row"><label>Registro</label><select id="h_style"><option value="mayordomo">Mayordomo británico</option><option value="stark">Técnico Stark</option><option value="seco">Seco militar</option></select></div>
    <div class="actions"><button onclick="saveTalk()">Guardar carácter</button></div>
  </div>
  <div class="block">
    <h2>Voz</h2>
    <p class="d">Piper local, en su PC. Sin subir su voz a ningún sitio.</p>
    <div class="row"><label>Velocidad: <b id="vval">1.12</b></label><input type="range" id="v_tts" min="0.8" max="1.4" step="0.01" value="1.12" oninput="document.getElementById('vval').textContent=this.value"></div>
    <div class="row"><label>Leer en alto en la pizarra</label><input type="checkbox" id="v_read" checked></div>
    <div class="row"><label>Activación mediante palmadas</label><input type="checkbox" id="v_clap"></div>
    <p class="d">Permite activar JARVIS mediante dos palmadas consecutivas.</p>
    <div class="actions"><button onclick="saveVoice()">Guardar voz</button></div>
  </div>
</section>

<section class="panel" id="p-orbe">
  <div class="block">
    <h2>Orbe</h2>
    <p class="d">Desactívelo si nota la GPU ocupada: el resto sigue igual.</p>
    <div class="row"><label>Encendido</label><input type="checkbox" id="o_en" checked></div>
    <div class="row"><label>Tamaño: <b id="oval">1.0</b></label><input type="range" id="o_size" min="0.5" max="1.5" step="0.1" value="1" oninput="document.getElementById('oval').textContent=(+this.value).toFixed(1)"></div>
    <div class="row"><label>Ritmo: <b id="osval">1.0</b></label><input type="range" id="o_speed" min="0.2" max="3" step="0.1" value="1" oninput="document.getElementById('osval').textContent=(+this.value).toFixed(1)"></div>
    <div class="actions"><button onclick="saveOrb()">Guardar orbe</button></div>
  </div>
</section>

<section class="panel" id="p-movimiento">
  <div class="block">
    <h2>Animaciones</h2>
    <p class="d">Cuánto se mueve la interfaz. A 0 queda todo quieto.</p>
    <div class="row"><label>Encendidas</label><input type="checkbox" id="a_en" checked></div>
    <div class="row"><label>Intensidad: <b id="aval">70</b></label><input type="range" id="a_int" min="0" max="100" value="70" oninput="document.getElementById('aval').textContent=this.value"></div>
    <div class="row"><label>Entrada de mensajes</label><select id="a_msg"><option value="fade">Fundido</option><option value="slide">Deslizamiento</option><option value="none">Ninguna</option></select></div>
    <div class="row"><label>Núcleo del orbe</label><select id="a_orb"><option value="pulse">Latido</option><option value="glow">Brillo</option><option value="spin">Giro</option><option value="none">Ninguna</option></select></div>
    <div class="actions"><button onclick="saveAnims()">Guardar animaciones</button> <button class="ghost" onclick="replayDemo()">Ver ejemplo</button></div>
  </div>
</section>

<section class="panel" id="p-automatizaciones">
  <div class="block">
    <h2>Sus atajos</h2>
    <p class="d">Una frase y JARVIS ejecuta la acción. Usted también puede decírsela por voz.</p>
    <div id="protos"></div>
  </div>
  <div class="block">
    <h2>Crear un atajo</h2>
    <p class="d">Nombre, frase que lo dispara, y qué hace después.</p>
    <div class="row"><label>Nombre</label><input type="text" class="grow" id="pr_name" placeholder="Buenos días"></div>
    <div class="row"><label>Se dispara con</label><input type="text" class="grow" id="pr_trig" placeholder="buenos días|despiértame"></div>
    <div class="row"><label>Acción</label><select class="grow" id="pr_tool"></select> <button class="ghost" onclick="addAction()">Añadir</button></div>
    <div class="row"><label>Acciones (JSON)</label><textarea id="pr_actions" placeholder='[{"tool":"health_overview","params":{}}]'></textarea></div>
    <div class="row"><label>Lo que dice al terminar</label><input type="text" class="grow" id="pr_say" placeholder="Listo, señor."></div>
    <div class="actions"><button onclick="saveProto()">Guardar atajo</button></div>
  </div>
</section>

<section class="panel" id="p-claves">
  <div class="block">
    <h2>Claves de servicios</h2>
    <p class="d">Se guardan en su PC, en claro. Déjelo vacío lo que no use: JARVIS sigue funcionando con lo demás.</p>
    <div class="row" style="border:0;padding:4px 0 12px"><label style="flex:0 0 210px">Traer de otro equipo</label><input type="file" id="importFile" accept=".env,.txt,text/plain" class="grow" style="padding:7px 11px" /><button class="ghost" onclick="exportKeys()">Exportar mis claves</button></div><div id="importMsg" class="d" style="margin:0 0 12px;min-height:18px"></div>
    <div id="apis"></div>
    <div class="actions"><button onclick="saveApis()">Guardar claves</button></div>
  </div>
</section>

<section class="panel" id="p-memoria">
  <div class="block">
    <h2>Memoria persistente</h2>
    <p class="d">Lo que JARVIS recuerda entre conversaciones. Vive en Mem0, en su
    propio servicio. Si el servicio está caído, JARVIS sigue contestando: la
    memoria es una ayuda, no un requisito.</p>
    <div id="memEstado" class="d" style="margin:0 0 14px;min-height:20px">Consultando…</div>
    <div class="row"><label>Buscar en la memoria</label><input type="text" id="memQ" class="grow" placeholder="p. ej. NAS, español, proyecto" oninput="memBuscar()"></div>
    <div id="memMsg" class="d" style="margin:6px 0 12px;min-height:18px"></div>
    <div class="row" style="border:0;padding:0 0 12px"><label style="flex:0 0 210px">Añadir un recuerdo</label><input type="text" id="memNuevo" class="grow" placeholder="Lo que debe recordar"><button class="ghost" onclick="memAnadir()">Guardar</button></div>
    <div id="memLista" style="max-height:380px;overflow:auto"></div>
    <div class="actions" style="margin-top:14px">
      <button class="ghost" onclick="memRecargar()">Recargar</button>
      <button class="ghost" onclick="memVaciar()" style="border-color:#7a3040;color:#ffb4bf">Vaciar toda la memoria</button>
    </div>
  </div>
</section>

<section class="panel" id="p-sistema">
  <div class="block">
    <h2>Memoria y pantalla</h2>
    <p class="d">Cuánta conversación retiene y si puede mirar su pantalla.</p>
    <div class="row"><label>Mensajes en contexto</label><input type="number" id="p_ctx" min="3" max="40" value="15"></div>
    <div class="row"><label>Memoria semántica</label><input type="checkbox" id="p_recall" checked></div>
    <div class="row"><label>Mirar la pantalla</label><select id="p_screen"><option value="continuous">Siempre</option><option value="on_demand">Solo si se lo pide</option><option value="off">Nunca</option></select></div>
    <div class="row"><label>Cada cuántos segundos</label><input type="number" id="p_sint" min="2" max="60" value="5"> <span class="hint">s</span></div>
    <div class="actions"><button onclick="savePerf()">Guardar sistema</button></div>
  </div>
</section>

</div>
</div>
</div><div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const API_NAMES=['GROQ_API_KEY','GEMINI_API_KEY','OPENROUTER_API_KEY','WEATHER_API_KEY','DISCORD_BOT_TOKEN','DISCORD_OWNER_ID','SPOTIFY_CLIENT_ID','SPOTIFY_CLIENT_SECRET','VIRUSTOTAL_API_KEY','VBOX_GUEST_USER','VBOX_GUEST_PASSWORD','CAMPUS_USER','CAMPUS_PASSWORD','NTIFY_TOPIC'];
let CFG=null;
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===b));
 document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id==='p-'+b.dataset.tab));
  // La memoria se pide al abrir su pestana, no al cargar la pagina: son
  // peticiones a otro servicio y no tiene sentido pagarlas si nadie la mira.
  if(b.dataset.tab==='memoria'&&typeof memRecargar==='function')memRecargar();
 window.scrollTo({top:0,behavior:'smooth'});}));
// El token lo inyecta el servidor en el HTML (ver _inyecta_token). Los
// endpoints de memoria lo exigen, igual que el resto de la API.
if(typeof window.JARVIS_TOKEN!=='undefined')MEM_TOKEN=window.JARVIS_TOKEN;
function toast(t,bad){const e=$('toast');e.textContent=t;e.className='on'+(bad?' bad':'');clearTimeout(e._t);e._t=setTimeout(()=>e.className='',2400);}
async function post(p){const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const j=await r.json();toast(j.ok?'Guardado, señor.':'Fallo: '+(j.reason||''),!j.ok);}
function paint(){
 const T=CFG.theme,M=CFG.messages,O=CFG.orb,A=CFG.anims;
 document.documentElement.style.setProperty('--cyan',T.accent);
 document.documentElement.style.setProperty('--ink',T.ink);
 document.querySelector('#demo').style.background=T.bg;
 document.querySelectorAll('#demo .bub.u').forEach(e=>{e.style.background=M.user_bg;e.style.borderRadius=M.radius+'px';e.style.fontSize=M.font+'px';});
 document.querySelectorAll('#demo .bub.j').forEach(e=>{e.style.background=M.jarvis_bg;e.style.borderRadius=M.radius+'px';e.style.fontSize=M.font+'px';e.style.color=T.ink;});
 document.querySelectorAll('#demo .bub.s').forEach(e=>{e.style.color=M.sys_color;});
 const sz=74*(+O.size||1);
 for(const id of ['demoOrb','demoOrb2']){const o=$(id);if(!o)continue;
  o.classList.toggle('off',!O.enabled);
  o.style.width=sz+'px';o.style.height=sz+'px';
  o.style.background=`radial-gradient(circle at 35% 32%, ${T.orb1}, ${T.orb2} 68%, #04070d)`;
  o.style.animation=A.enabled?(A.orb_anim==='spin'?'spinOrb '+(4/(+O.speed||1))+'s linear infinite':A.orb_anim==='glow'?'glowOrb 2.4s ease-in-out infinite':'pulseOrb '+(2/(+O.speed||1))+'s ease-in-out infinite'):'none';}
}
function replayDemo(){
 const A=CFG.anims;
 document.querySelectorAll('#demo [data-anim]').forEach((e,i)=>{
  e.style.animation='none';void e.offsetWidth;
  e.style.animation=A.enabled?(A.msg_anim==='slide'?'slideIn .5s ease both':'fadeIn .5s ease both'):'none';
  e.style.animationDelay=(i*0.25)+'s';});
}
async function load(){
 const j=await (await fetch('/api/config')).json();CFG=j.config;const c=CFG;
 for(const k of ['accent','bg','ink','orb1','orb2'])if(c.theme&&c.theme[k])$('c_'+k).value=c.theme[k];
 $('m_user').value=c.messages.user_bg;$('m_jarvis').value=c.messages.jarvis_bg;$('m_sys').value=c.messages.sys_color;
 $('m_radius').value=c.messages.radius;$('m_font').value=c.messages.font;
 $('o_en').checked=!!c.orb.enabled;$('o_size').value=c.orb.size;$('o_speed').value=c.orb.speed;
 $('oval') && ($('oval').textContent=(+c.orb.size).toFixed(1));
 $('osval') && ($('osval').textContent=(+c.orb.speed).toFixed(1));
 $('a_en').checked=!!c.anims.enabled;$('a_int').value=c.anims.intensity;$('aval').textContent=c.anims.intensity;
 $('a_msg').value=c.anims.msg_anim;$('a_orb').value=c.anims.orb_anim;
 $('p_ctx').value=c.perf.context_window;$('p_recall').checked=!!c.perf.recall;
 $('p_screen').value=c.perf.screen_mode;$('p_sint').value=c.perf.screen_interval;
 $('h_humor').value=c.humor;$('hval').textContent=c.humor;
 if(c.speech&&c.speech.style)$('h_style').value=c.speech.style;
 if(c.voice&&c.voice.tts_speed){$('v_tts').value=c.voice.tts_speed;$('vval').textContent=c.voice.tts_speed;}
 if(c.voice)$('v_read').checked=c.voice.read_aloud!==false;
 if(c.voice)$('v_clap').checked=c.voice.clap_enabled===true;
 paint();
 const t=await (await fetch('/api/tools')).json();
 const sel=$('pr_tool');sel.innerHTML='';
 (t.tools||[]).forEach(x=>{const o=document.createElement('option');o.value=x.name;o.textContent=x.name+' — '+(x.description||'').slice(0,60);sel.appendChild(o);});
  const p=await (await fetch('/api/protocols')).json();
  const box=$('protos');box.innerHTML='';
  const plist=p.protocols||[];
  if(!plist.length){const e0=document.createElement('div');e0.className='empty';e0.textContent='Todavía no hay atajos. Cree uno abajo.';box.appendChild(e0);}
  plist.forEach(x=>{const d=document.createElement('div');d.className='proto';
  const left=document.createElement('div');
  const b0=document.createElement('b');b0.textContent=x.name;
  const m0=document.createElement('div');m0.className='meta';
  const na=(x.actions||[]).length;
  m0.textContent='«'+(x.triggers||[]).join('» «')+'» · '+na+' '+(na===1?'acción':'acciones');
  left.appendChild(b0);left.appendChild(m0);
  const b=document.createElement('button');b.className='danger';b.textContent='Borrar';
  b.onclick=async()=>{await fetch('/api/protocols?name='+encodeURIComponent(x.name),{method:'DELETE'});load();};
  d.appendChild(left);d.appendChild(b);box.appendChild(d);});
 const box2=$('apis');box2.innerHTML='';
 API_NAMES.forEach(k=>{const st=c.apis&&c.apis[k];const row=document.createElement('div');row.className='row';
  row.innerHTML='<label>'+k+'</label><input type="password" id="a_'+k+'" placeholder="••••••"><span class="badge'+(st&&st.set?'':' off')+'">'+(st&&st.set?'guardada':'vacía')+'</span>';
  box2.appendChild(row);});
 const _imp=document.getElementById('importFile');
if(_imp)_imp.addEventListener('change',importKeys);
document.querySelectorAll('input[type=color]').forEach(e=>e.addEventListener('input',readLive));
 ['m_radius','m_font','o_size','o_speed'].forEach(id=>$(id).addEventListener('input',readLive));
}
function readLive(){
 CFG.theme.accent=$('c_accent').value;CFG.theme.bg=$('c_bg').value;CFG.theme.ink=$('c_ink').value;CFG.theme.orb1=$('c_orb1').value;CFG.theme.orb2=$('c_orb2').value;
 CFG.messages.user_bg=$('m_user').value;CFG.messages.jarvis_bg=$('m_jarvis').value;CFG.messages.sys_color=$('m_sys').value;
 CFG.messages.radius=+$('m_radius').value;CFG.messages.font=+$('m_font').value;
 CFG.orb.enabled=$('o_en').checked;CFG.orb.size=+$('o_size').value;CFG.orb.speed=+$('o_speed').value;
 $('oval').textContent=CFG.orb.size.toFixed(1);$('osval').textContent=CFG.orb.speed.toFixed(1);
 paint();
}
function saveTheme(){post({theme:{accent:$('c_accent').value,bg:$('c_bg').value,ink:$('c_ink').value,orb1:$('c_orb1').value,orb2:$('c_orb2').value}});}
function saveMsgs(){post({messages:{user_bg:$('m_user').value,jarvis_bg:$('m_jarvis').value,sys_color:$('m_sys').value,radius:+$('m_radius').value,font:+$('m_font').value}});}
function saveOrb(){post({orb:{enabled:$('o_en').checked,size:+$('o_size').value,speed:+$('o_speed').value}});}
function saveAnims(){post({anims:{enabled:$('a_en').checked,intensity:+$('a_int').value,msg_anim:$('a_msg').value,orb_anim:$('a_orb').value}});}
function savePerf(){post({perf:{context_window:+$('p_ctx').value,recall:$('p_recall').checked,screen_mode:$('p_screen').value,screen_interval:+$('p_sint').value}});}
function saveApis(){const o={};API_NAMES.forEach(k=>{const v=$('a_'+k).value.trim();if(v)o[k]=v;});post({apis:o});setTimeout(load,600);}

// ---------- Memoria persistente (Mem0) ------------------------------------
// Todos los fetch llevan el token. Los endpoints lo exigen igual que el resto
// de la API: quien entre aqui puede leer y borrar todo lo que JARVIS sabe
// del señor, así que no puede ser una página abierta sin más.
// Se reutiliza el mismo patrón que el resto de la página en vez de traer una
// segunda forma de hablar con el servidor.
let MEM_TOKEN='';
function memMsg(t,bad){const e=document.getElementById('memMsg');if(!e)return;e.textContent=t;e.style.color=bad?'#ffb3b3':'';}
function _memFetch(url,opts){
  opts=opts||{};opts.headers=Object.assign({'Content-Type':'application/json'},opts.headers||{});
  if(MEM_TOKEN)opts.headers['X-Jarvis-Token']=MEM_TOKEN;
  return fetch(url,opts);
}
function memEstado(txt){const e=document.getElementById('memEstado');if(e)e.textContent=txt;}
function _memFila(r){
  const d=document.createElement('div');
  d.className='row';d.style.border='0';d.style.padding='8px 0';
  const iz=document.createElement('div');iz.style.flex='1 1 auto';iz.style.minWidth='0';
  const t=document.createElement('div');t.textContent=r.texto||'';
  t.style.fontSize='13px';t.style.wordBreak='break-word';
  const meta=[];
  if(r.categoria)meta.push(r.categoria);
  if(r.created_at)meta.push(String(r.created_at).slice(0,10));
  if(r.source)meta.push(r.source);
  const m=document.createElement('div');m.className='hint';
  m.textContent=meta.join(' · ');
  iz.appendChild(t);iz.appendChild(m);
  const b=document.createElement('button');b.className='ghost';b.textContent='Borrar';
  b.onclick=async()=>{
    if(!confirm('¿Borrar este recuerdo? No hay vuelta atrás.'))return;
    const r2=await _memFetch('/api/memory/'+encodeURIComponent(r.id),{method:'DELETE'});
    const j=await r2.json().catch(()=>({}));
    memMsg(j.ok?'Recuerdo borrado.':(j.error||'No se pudo borrar'),!j.ok);
    memRecargar();
  };
  d.appendChild(iz);d.appendChild(b);
  return d;
}
async function memRecargar(){
  const q=($('memQ')||{}).value||'';
  const url=q?('/api/memory/search?q='+encodeURIComponent(q)):'/api/memory';
  let cuerpo=null, method='GET';
  if(q){method='POST';cuerpo=JSON.stringify({consulta:q,limite:50});}
  const r=await _memFetch(url,{method:method,body:cuerpo||undefined});
  if(r.status===401){memEstado('Sin token: recargue la página.');return;}
  const j=await r.json().catch(()=>({}));
  const est=j.estado||{};
  if(!est.activado){
    memEstado('La memoria persistente está desactivada (MEMORY_ENABLED=0). '
             +'JARVIS sigue funcionando con la memoria local.');
  }else if(est.motivo){
    memEstado('El servicio de memoria no está disponible ahora mismo: '+est.motivo
             +'. JARVIS contesta igual.');
  }else{
    memEstado('Servicio en '+est.url+' · usuario '+est.user_id
             +' · '+(j.total||0)+' recuerdos.');
  }
  const l=document.getElementById('memLista');if(!l)return;
  l.innerHTML='';
  if(!j.recuerdos||!j.recuerdos.length){
    const p=document.createElement('p');p.className='d';
    p.textContent='No hay recuerdos todavía.';
    l.appendChild(p);return;
  }
  j.recuerdos.forEach(x=>l.appendChild(_memFila(x)));
}
function memBuscar(){clearTimeout(window._memT);window._memT=setTimeout(memRecargar,320);}
async function memAnadir(){
  const v=(($('memNuevo')||{}).value||'').trim();
  if(!v)return memMsg('Escriba algo primero.',true);
  const r=await _memFetch('/api/memory',{method:'POST',body:JSON.stringify({texto:v})});
  const j=await r.json().catch(()=>({}));
  memMsg(j.ok?'Guardado.':(j.error||'No se pudo guardar'),!j.ok);
  if(j.ok){$('memNuevo').value='';memRecargar();}
}
async function memVaciar(){
  if(!confirm('Esto borra TODA la memoria de JARVIS, sin vuelta atrás. ¿Seguro?'))return;
  const r=await _memFetch('/api/memory/clear',{method:'POST'});
  const j=await r.json().catch(()=>({}));
  memMsg(j.ok?'Memoria vaciada.':(j.error||'No se pudo vaciar'),!j.ok);
  memRecargar();
}
function _importMsg(t,bad){const e=document.getElementById('importMsg');if(!e)return;e.textContent=t;e.style.color=bad?'#ffb3b3':'';}
async function importKeys(ev){
  const f=(ev.target.files||[])[0];
  if(!f)return;
  _importMsg('Leyendo '+f.name+'\u2026');
  try{
    const texto=await f.text();
    const r=await fetch('/api/config/importar-claves',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({texto})});
    const j=await r.json();
    if(!j.ok){_importMsg('No se pudo: '+(j.reason||''),true);return;}
    _importMsg('Importadas '+j.total+': '+j.importadas.join(', ')+
      (j.ignoradas&&j.ignoradas.length?(' \u00b7 ignoradas (no son de JARVIS): '+j.ignoradas.join(', ')):''));
    setTimeout(load,700);
  }catch(e){_importMsg('Fallo al leer el fichero: '+e,true);}
  ev.target.value='';
}
async function exportKeys(){
  _importMsg('Preparando el fichero\u2026');
  try{
    const r=await fetch('/api/config/exportar-claves',{method:'POST'});
    const j=await r.json();
    if(!j.ok){_importMsg('No se pudo exportar: '+(j.reason||''),true);return;}
    const blob=new Blob([j.texto],{type:'text/plain'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);a.download='keys.env';
    document.body.appendChild(a);a.click();a.remove();
    _importMsg('Descargadas '+j.total+' claves en keys.env. Van en claro: no lo suba a ningun sitio.');
  }catch(e){_importMsg('Fallo al exportar: '+e,true);}
}
function saveTalk(){post({humor:parseInt($('h_humor').value),speech:{style:$('h_style').value}});}
function saveVoice(){post({voice:{tts_speed:parseFloat($('v_tts').value),read_aloud:$('v_read').checked,clap_enabled:$('v_clap').checked}});}
function addAction(){const ta=$('pr_actions');let arr=[];try{arr=JSON.parse(ta.value||'[]');}catch(e){arr=[];}arr.push({tool:$('pr_tool').value,params:{}});ta.value=JSON.stringify(arr,null,1);}
async function saveProto(){
 const p={name:$('pr_name').value.trim(),triggers:$('pr_trig').value.split('|').map(s=>s.trim()).filter(Boolean),say:$('pr_say').value.trim(),actions:[]};
 try{p.actions=JSON.parse($('pr_actions').value||'[]');}catch(e){toast('JSON de acciones inválido');return;}
 const r=await fetch('/api/protocols',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
 const j=await r.json();toast(j.ok?'Protocolo guardado, señor.':'Fallo: '+(j.reason||''));if(j.ok)load();
}
load();
</script></body></html>"""


@app.get("/config")
async def config_page():
    from fastapi.responses import HTMLResponse
    # Esta pagina guarda ajustes, importa y exporta claves: sin token, al
    # proteger /api/* se quedaba sin poder hacer nada. El token se inyecta
    # (junto con el envoltorio de fetch) antes de devolverla.
    return HTMLResponse(_inyecta_token(_CONFIG_PAGE))


# ------------------------------------------------------------------ #
# Modo de instalación: FULL (escritorio) o LITE (servidor / Docker)
# ------------------------------------------------------------------ #
def _mode_file():
    return os.path.join(str(DATA_DIR), "modo.txt")


def get_modo() -> str:
    """FULL por defecto. LITE se elige en la instalación y se recuerda."""
    try:
        with open(_mode_file(), "r", encoding="utf-8") as f:
            m = (f.read() or "").strip().lower()
        if m in ("lite", "full"):
            return m
    except Exception:
        pass
    # Sin archivo no hay eleccion hecha. En un servidor (Docker, NAS) lo
    # razonable es LITE, porque la pizarra web no puede ni sermonselo de lo
    # que hace el escritorio. Antes salia FULL y habia que entrar a /modo a
    # mano; ahora se decide solo la primera vez y luego manda el archivo.
    if _es_servidor():
        try:
            set_modo("lite")
            return "lite"
        except Exception:
            pass
    return "full"


def _es_servidor() -> bool:
    """Servidor sin Electron: lo delata la variable o el propio contenedor."""
    if os.environ.get("JARVIS_SERVER_MODE", "").strip():
        return True
    return os.path.exists("/.dockerenv")


def set_modo(m: str) -> str:
    m = (m or "").strip().lower()
    if m not in ("lite", "full"):
        raise ValueError("modo debe ser lite o full")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_mode_file(), "w", encoding="utf-8") as f:
            f.write(m)
    except Exception as e:
        log.debug("set_modo: %s", e)
    return m


def _cfg_mandatory() -> bool:
    """¿Falta alguna clave imprescindible para que JARVIS hable?"""
    return not (Config.GROQ_API_KEY or Config.GEMINI_API_KEY
                or Config.OPENROUTER_API_KEY)


# --- Apps abiertas y resumen semanal ----------------------------------------
try:
    import apps as _apps
    _apps.registrar(app)
except Exception as _e2:  # noqa: BLE001
    log.warning('no pude montar /api/apps: %s', _e2)

try:
    import live_dashboard as _live_dashboard
    _live_dashboard.register(app)
except Exception as _live_error:
    log.warning('no pude montar JARVIS Live: %s', _live_error)

try:
    import recap as _recap
    _recap.registrar(app)
except Exception as _e:  # noqa: BLE001
    log.warning('no pude registrar /api/recap: %s', _e)


@app.get("/api/mode")
async def api_modo_get():
    return {"ok": True, "modo": get_modo(), "necesita_config": _cfg_mandatory(),
            "instalado": not _cfg_mandatory()}


@app.post("/api/mode")
async def api_modo_post(payload: dict):
    try:
        return {"ok": True, "modo": set_modo((payload or {}).get("modo", ""))}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}


# ------------------------------------------------------------------ #
# La pizarra en el navegador (modo LITE / servidor / Docker)
# ------------------------------------------------------------------ #
def _webboard_dir():
    return os.path.join(str(BRAIN_DIR), "webboard")


@app.get("/board")
async def board_page():
    """La pizarra en el navegador.

    Las rutas del HTML son relativas (así funcionan dentro de Electron, donde
    la página se carga desde su fichero). Por HTTP, abrir '/board' sin barra
    final hacía que 'overlay.css' se pidiera en '/overlay.css' y la pizarra
    salía sin una sola regla de estilo. Por eso aquí se.absolutizan todas.
    """
    d = _webboard_dir()
    idx = os.path.join(d, "index.html")
    if not os.path.isfile(idx):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h1>La pizarra no está instalada</h1><p>Ejecute el script de "
            "dependencias o vuelva a instalar JARVIS.</p>", status_code=503)
    from fastapi.responses import HTMLResponse
    with open(idx, "r", encoding="utf-8") as f:
        html = f.read()
    html = (html.replace('"./vendor/three.module.js"', '"/board/vendor/three.module.js"')
                .replace('"./vendor/jsm/"', '"/board/vendor/jsm/"')
                .replace('src="./browser-bridge.js"', 'src="/board/browser-bridge.js"')
                .replace('src="app.js"', 'src="/board/app.js"')
                .replace('src="orb.js"', 'src="/board/orb.js"')
                .replace('href="overlay.css"', 'href="/board/overlay.css"'))
    html = _inyecta_token(html)
    return HTMLResponse(html)


@app.get("/board/")
async def board_page_slash():
    return await board_page()


@app.get("/board/{ruta:path}")
async def board_static(ruta: str):
    d = os.path.realpath(_webboard_dir())
    destino = os.path.realpath(os.path.join(d, ruta))
    if not destino.startswith(d) or not os.path.isfile(destino):
        from fastapi.responses import HTMLResponse
        return HTMLResponse("No encontrado", status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(destino)


@app.get("/api/board/apps")
async def board_apps():
    try:
        from webboard_backend import list_apps
        return {"ok": True, "apps": list_apps()}
    except Exception as e:
        return {"ok": False, "apps": [], "reason": str(e)[:160]}


@app.get("/api/board/recent")
async def board_recent():
    try:
        from webboard_backend import recent_files
        return {"ok": True, "files": recent_files()[:200]}
    except Exception as e:
        return {"ok": False, "files": [], "reason": str(e)[:160]}


@app.get("/api/board/webapps")
async def board_webapps():
    """Mapa de webapps de la pizarra, para que el puente del navegador sepa
    cuáles son webs y cuáles son programas. El mismo diccionario que usa la
    versión de escritorio: así no se separan."""
    return {"ok": True, "webapps": _WEB_APPS}


@app.get("/api/board/instaladas")
async def board_instaladas(q: str = ""):
    """Todas las apps instaladas, para el gestor de la pizarra."""
    try:
        from webboard_backend import list_apps_all
        return {"ok": True, "apps": list_apps_all(q)}
    except Exception as e:
        return {"ok": False, "apps": [], "reason": str(e)[:160]}


@app.post("/api/board/terminal")
async def board_terminal(payload: dict):
    """Abre una terminal de verdad en el equipo donde corre el brain."""
    try:
        from webboard_backend import abrir_terminal
        return abrir_terminal((payload or {}).get("shell", ""))
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


@app.get("/api/board/dir")
async def board_dir(path: str = ""):
    try:
        from webboard_backend import list_dir
        return {"ok": True, **list_dir(path or None)}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}


@app.api_route("/api/board/open", methods=["GET", "POST"])
async def board_open(request: Request, path: str = ""):
    """Abre una app o un fichero.

    Acepta GET y POST porque el puente del navegador lo llama con GET y sus
    parametros en la query, y con POST manda un cuerpo JSON: con una sola de
    las dos, el explorador de la pizarra no abria nada y recibia un 405 del
    que no se enteraba nadie.
    """
    payload: dict = {}
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    if path:
        payload.setdefault("path", path)
    try:
        from webboard_backend import abrir
        return abrir(payload or {})
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/board/fichero")
async def board_fichero(request: Request, path: str = ""):
    """Sirve un fichero del NAS para verlo o descargarlo desde el navegador.

    En un contenedor no hay escritorio, así que "abrir" un documento no puede
    lanzar una ventana como en Windows. Lo que se puede, y es lo que hace este
    endpoint, es mandarlo al navegador: imágenes, PDF, texto, audio y vídeo se
    ven ahí mismo; el resto se descarga.

    Pide el token como el resto (/api/config/exportar-claves), porque sin el
    cualquiera que lo tenga se baja el .env con todas las claves. Y aunque lo
    tenga, solo se sirve lo que este bajo una raiz permitida: listar carpetas es
    una cosa y leer el contenido de un fichero es otra.
    """
    if not _exige_token_o_url(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    if not path:
        return JSONResponse({"ok": False, "error": "falta el path"}, status_code=400)
    try:
        from webboard_backend import permitido_leer
        ok, motivo = permitido_leer(path)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:160]}, status_code=500)
    if not ok:
        return JSONResponse({"ok": False, "error": motivo}, status_code=403)
    try:
        real = os.path.realpath(os.path.expanduser(path))
        if not os.path.isfile(real):
            return JSONResponse({"ok": False, "error": "no es un fichero"},
                                status_code=404)
        tam = os.path.getsize(real)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)[:160]}, status_code=403)

    # El token va en la query (es una descarga normal del navegador), asi que
    # no se puede poner en la cabecera. FileResponse no lo permite, se hace a
    # mano con un rango para que el navegador pueda ir Positionando el video.
    rango = request.headers.get("range") or ""
    desde, hasta = 0, tam - 1
    parcial = False
    if rango.startswith("bytes="):
        try:
            trozo = rango[6:].split("-")
            desde = int(trozo[0] or 0)
            hasta = int(trozo[1]) if len(trozo) > 1 and trozo[1] else tam - 1
            parcial = True
        except ValueError:
            desde, hasta, parcial = 0, tam - 1, False
    if desde >= tam:
        return JSONResponse({"ok": False, "error": "rango fuera de fichero"},
                            status_code=416)
    hasta = min(hasta, tam - 1)
    longitud = hasta - desde + 1

    def cuerpo():
        with open(real, "rb") as fh:
            fh.seek(desde)
            restante = longitud
            while restante > 0:
                trozo = fh.read(min(65536, restante))
                if not trozo:
                    break
                restante -= len(trozo)
                yield trozo

    # disposition=inline para lo que el navegador sabe mirar y attachment para
    # lo demas: si no, un .exe se descargaria pero un .txt tambien, y al abrir
    # el dashboard te descarga media carpeta.
    #
    # El content-type NO puede ser siempre octet-stream: con eso el navegador
    # descarga la imagen en vez de abrirla, y un PDF te sale como fichero. Se
    # adivina por extension, que es lo que hay, y lo que no se sepa va con
    # octet-stream para que el navegador lo baje en vez de intentar ejecutarlo.
    import mimetypes
    nombre = os.path.basename(real)
    _MIRA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".pdf",
             ".txt", ".md", ".log", ".json", ".csv", ".mp4", ".webm", ".mp3",
             ".wav", ".ogg", ".m4a", ".html", ".htm", ".xml"}
    ext = os.path.splitext(nombre)[1].lower()
    # mimetypes en slim a veces no trae los de webp o svg; se ponen a mano.
    _TIPO = {".webp": "image/webp", ".svg": "image/svg+xml", ".mkv": "video/x-matroska",
             ".m4v": "video/mp4", ".flac": "audio/flac", ".opus": "audio/opus",
             ".epub": "application/epub+zip"}
    tipo = _TIPO.get(ext) or mimetypes.guess_type(nombre)[0]
    if not tipo or ext not in _MIRA:
        tipo = tipo if (tipo and ext in _MIRA) else "application/octet-stream"
    inline = ext in _MIRA
    disposicion = "inline" if inline else "attachment"
    # El nombre va entrecomillado porque los ficheros del NAS tienen espacios,
    # acentos y de todo, y sin eso el navegador los parte por la mitad.
    from urllib.parse import quote
    cabeceras = {
        "Content-Type": tipo,
        "Content-Length": str(longitud),
        "Content-Disposition":
            f"{disposicion}; filename=\"{nombre}\"; "
            f"filename*=UTF-8''{quote(nombre)}",
        "Cache-Control": "private, max-age=60",
        "Accept-Ranges": "bytes",
        # Sin esto el navegador se fia del tipo que le digamos y un HTML del
        # NAS podria ejecutar script con el origen del servidor.
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "X-Content-Type-Options": "nosniff",
    }
    if parcial:
        cabeceras["Content-Range"] = f"bytes {desde}-{hasta}/{tam}"
    return StreamingResponse(cuerpo(), status_code=206 if parcial else 200,
                             headers=cabeceras)


# ------------------------------------------------------------------ #
# Memoria persistente (Mem0 OSS)
#
# Estos endpoints NO pisan ninguno existente: /api/memory no estaba. Todos
# exigen el token de brain/auth.py, el mismo que protege /api/config y el
# WebSocket, porque quien entre aqui puede leer y borrar todo lo que JARVIS
# sabe del señor.
#
# La UI de administracion vive en la pestaña MEMORY de /config, no aquí: no
# hay una segunda pagina que mantener.
# ------------------------------------------------------------------ #
def _mem_manager():
    from memory.manager import manager
    return manager()


@app.get("/api/memory")
async def api_memory_list(request: Request, q: str = "", limite: int = 200):
    """Todo lo que JARVIS recuerda, o lo que case con q."""
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    try:
        if q:
            recs = m.search(q, limit=max(1, min(int(limite), 50)))
        else:
            recs = m.get_all(limit=max(1, min(int(limite), 500)))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:160]}, status_code=500)
    return {"ok": True, "total": len(recs), "recuerdos": recs,
            "estado": m.estado()}


@app.post("/api/memory")
async def api_memory_add(request: Request, payload: dict):
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    texto = (payload or {}).get("texto", "")
    r = m.add(texto, categoria=(payload or {}).get("categoria", ""))
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


@app.post("/api/memory/search")
async def api_memory_search(request: Request, payload: dict):
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    q = (payload or {}).get("consulta") or (payload or {}).get("q") or ""
    try:
        limite = max(1, min(int((payload or {}).get("limite", 10) or 10), 50))
    except (TypeError, ValueError):
        limite = 10
    recs = m.search(q, limit=limite)
    return {"ok": True, "consulta": q, "total": len(recs), "recuerdos": recs}


@app.put("/api/memory/{memory_id}")
async def api_memory_update(request: Request, memory_id: str, payload: dict):
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    r = m.update(memory_id, (payload or {}).get("texto", ""))
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


@app.delete("/api/memory/{memory_id}")
async def api_memory_delete(request: Request, memory_id: str):
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    r = m.delete(memory_id)
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


@app.post("/api/memory/clear")
async def api_memory_clear(request: Request):
    """Borra TODA la memoria. Sin vuelta atrás, por eso es POST y no DELETE."""
    if not _exige_token(request):
        return JSONResponse({"ok": False, "error": "sin token"}, status_code=401)
    m = _mem_manager()
    r = m.clear()
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


# ------------------------------------------------------------------ #
# Asistente de instalación (/setup)
#
# Dos pasos bien separados: primero las claves SIN las que JARVIS no puede
# contestar, y después las que solo abren funciones. No se mezclan porque no
# son lo mismo: no tiene sentido obligar a inventarse una clave de Spotify
# para poder preguntarle el tiempo.
# ------------------------------------------------------------------ #
_SETUP_HTML = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS · Instalación</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070d16;color:#9ec8e0;font:15px/1.55 "Segoe UI",Arial,sans-serif;
padding:34px 16px;min-height:100vh}
.wrap{max-width:700px;margin:0 auto}
h1{color:#e8f2fb;letter-spacing:.3em;font-size:21px;font-weight:600}
.sub{opacity:.7;margin:6px 0 24px;font-size:13px}
.paso{display:none}.paso.on{display:block}
h2{color:#e8f2fb;font-size:15px;margin:0 0 4px}
.aviso{opacity:.65;font-size:13px;margin-bottom:18px}
.clave{display:block;background:#0d1725;border:1px solid #1d3346;border-radius:10px;
padding:14px 16px;margin-bottom:12px}
.clave:focus-within{border-color:#5da6cc;box-shadow:0 0 0 3px rgba(93,166,204,.16)}
.et{display:flex;align-items:center;gap:8px;color:#cfe6f5;font-size:14px;margin-bottom:8px}
.et b{font-size:10px;padding:2px 7px;border-radius:20px;letter-spacing:.1em;font-weight:600}
.obl{background:#7a3b1c;color:#ffd9c2}
.opc{background:#173a4d;color:#a8d4ea}
input{width:100%;background:#071019;border:1px solid #1d3346;border-radius:7px;
color:#e8f2fb;padding:10px 12px;font:13px ui-monospace,Consolas,monospace}
input:focus{outline:none;border-color:#5da6cc}
small{display:block;opacity:.6;font-size:12px;margin-top:6px}
a{color:#7fc4e8}
.barra{height:3px;background:#132234;border-radius:3px;overflow:hidden;margin:0 0 24px}
.barra i{display:block;height:100%;width:50%;background:#5da6cc;transition:width .3s}
.botones{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
button{border:0;border-radius:8px;padding:11px 20px;font:600 14px "Segoe UI",Arial;
cursor:pointer;background:#5da6cc;color:#04121c}
button.sec{background:#16283a;color:#cfe6f5}
#msg{margin-top:16px;font-size:13px;min-height:20px}
.ok{color:#7fe0a0}.mal{color:#ff8a8a}
.modo{display:inline-block;background:#0f2231;border:1px solid #234257;border-radius:20px;
padding:3px 12px;font-size:11px;letter-spacing:.12em;margin-left:8px}
</style></head><body><div class="wrap">
<h1>J.A.R.V.I.S.<span class="modo" id="modo">…</span></h1>
<p class="sub">Configuración inicial</p>
<div class="barra"><i id="prog"></i></div>
<div class="paso on" id="p1">
  <h2>Paso 1 de 2 · Las claves imprescindibles</h2>
  <p class="aviso">Con al menos UNA de estas tres JARVIS ya puede hablar con
  usted. Con las tres va mucho mejor. Ponga las que tenga; las demás las
  añade luego desde el puesto de mando.</p>
  __OBL__
  <div class="botones">
    <button id="sig">Continuar</button>
    <button class="sec" id="rapido">De momento, solo una</button>
  </div>
</div>
<div class="paso" id="p2">
  <h2>Paso 2 de 2 · Funciones adicionales</h2>
  <p class="aviso">Estas no hacen falta para hablar: abren cosas concretas.
  Se pueden dejar vacías y rellenar más adelante.</p>
  __OPC__
  <div class="botones">
    <button class="sec" id="volver">Atrás</button>
    <button id="fin">Guardar y terminar</button>
  </div>
</div>
<div id="msg"></div>
</div><script>
const $=(s)=>document.querySelector(s);
function paso(n){document.querySelectorAll('.paso').forEach((p,i)=>p.classList.toggle('on',i===n));
$('#prog').style.width=((n+1)*50)+'%';}
$('#sig').onclick=()=>paso(1);
$('#rapido').onclick=()=>paso(1);
$('#volver').onclick=()=>paso(0);
$('#fin').onclick=async()=>{
  const apis={};
  document.querySelectorAll('[data-clave]').forEach(i=>{const v=i.value.trim();if(v)apis[i.dataset.clave]=v;});
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({apis})});
  const j=await r.json();
  const m=$('#msg');
  if(j.ok){m.className='ok';m.textContent='Guardado, señor. Abriendo la pizarra…';
    setTimeout(()=>{location.href='/board';},900);}
  else{m.className='mal';m.textContent='Fallo: '+(j.reason||'');}
};
fetch('/api/mode').then(r=>r.json()).then(j=>{
  $('#modo').textContent=j.modo==='lite'?'MODO LITE':'MODO FULL';
});
</script></body></html>"""


def _tarjetas_setup(lista, oblig: bool) -> str:
    import html as _h
    out = []
    for nombre, etiqueta, para, donde in lista:
        insignia = ('<b class="obl">imprescindible</b>' if oblig
                    else '<b class="opc">opcional</b>')
        out.append(
            f'<label class="clave" for="k_{nombre}">'
            f'<span class="et">{_h.escape(etiqueta)}{insignia}</span>'
            f'<input id="k_{nombre}" type="password" data-clave="{nombre}" '
            f'placeholder="{_h.escape(nombre)}" autocomplete="off" spellcheck="false" />'
            f'<small>{_h.escape(para)} &middot; '
            f'<a href="{_h.escape(donde)}" target="_blank" rel="noopener">pedirla</a>'
            '</small></label>')
    return "".join(out)


@app.get("/modo")
async def modo_page():
    """Selector de modo. Es la primera pantalla tras instalar, y vive en el
    brain para que valga igual en Windows, en un servidor y en Docker, donde
    no existe instalador gráfico al que añadirle una página."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_inyecta_token(_MODO_HTML))


@app.get("/setup")
async def setup_page():
    from setup_keys import CLAVES_OBLIGATORIAS as OBL
    from setup_keys import CLAVES_OPCIONALES as OPC
    from fastapi.responses import HTMLResponse
    html = (_SETUP_HTML
            .replace("__OBL__", _tarjetas_setup(OBL, True))
            .replace("__OPC__", _tarjetas_setup(OPC, False)))
    return HTMLResponse(_inyecta_token(html))

# ------------------------------------------------------------------ #
# Selector de modo (/modo)
# ------------------------------------------------------------------ #
_MODO_HTML = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS · Elija versión</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070d16;color:#9ec8e0;font:15px/1.55 "Segoe UI",Arial,sans-serif;
padding:36px 16px;min-height:100vh;display:flex;align-items:center;justify-content:center}
.wrap{max-width:840px;width:100%}
h1{color:#e8f2fb;letter-spacing:.28em;font-size:20px;font-weight:600;text-align:center}
.sub{opacity:.7;text-align:center;margin:8px 0 30px;font-size:13px}
.tarjetas{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.tarjeta{background:#0d1725;border:1px solid #1d3346;border-radius:14px;padding:22px;
cursor:pointer;transition:border-color .18s,transform .18s,box-shadow .18s;text-align:left;color:inherit}
.tarjeta:hover{border-color:#5da6cc;transform:translateY(-2px);box-shadow:0 12px 30px rgba(0,0,0,.4)}
.tarjeta.sel{border-color:#5da6cc;box-shadow:0 0 0 3px rgba(93,166,204,.18)}
.tarjeta h2{color:#e8f2fb;font-size:17px;margin-bottom:4px}
.tarjeta .c{opacity:.6;font-size:12px;letter-spacing:.12em;margin-bottom:12px}
.tarjeta ul{list-style:none;margin:0 0 16px}
.tarjeta li{padding:5px 0 5px 18px;position:relative;font-size:13.5px}
.tarjeta li:before{content:"·";position:absolute;left:6px;color:#5da6cc;font-weight:700}
.recom{display:inline-block;background:#173a4d;color:#a8d4ea;font-size:10px;padding:2px 8px;
border-radius:20px;letter-spacing:.1em;margin-left:8px;vertical-align:middle}
.sig{text-align:center;margin-top:26px}
button{background:#5da6cc;color:#04121c;border:0;border-radius:8px;padding:12px 30px;
font:600 15px "Segoe UI",Arial;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
#msg{margin-top:14px;font-size:13px;min-height:20px}
.ok{color:#7fe0a0}.mal{color:#ff8a8a}
@media(max-width:700px){.tarjetas{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<h1>J.A.R.V.I.S.</h1>
<p class="sub">Elija cómo quiere instalarlo. Podrá cambiarlo después.</p>
<div class="tarjetas">
  <button class="tarjeta" id="lite">
    <h2>Versión LITE</h2>
    <div class="c">para servidores y Docker</div>
    <ul>
      <li>Solo el cerebro, sin ventana de escritorio</li>
      <li>Todo se configura desde el navegador</li>
      <li>Pizarra, chat y programas en la web</li>
      <li>Los archivos y programas de ese equipo</li>
    </ul>
    <div class="recom">si es un servidor</div>
  </button>
  <button class="tarjeta" id="full">
    <h2>Versión FULL<span class="recom">normal</span></h2>
    <div class="c">instalación completa de escritorio</div>
    <ul>
      <li>Ventana de JARVIS y atajo Win+J</li>
      <li>Pizarra sobre el escritorio, traslúcida</li>
      <li>Voz, cámara y gestos con la mano</li>
      <li>Acceso rápido en el menú Inicio</li>
    </ul>
  </button>
</div>
<div class="sig"><button id="ir" disabled>Continuar</button><div id="msg"></div></div>
</div><script>
let elegido=null;
function elige(c){elegido=c;document.querySelectorAll('.tarjeta').forEach(t=>t.classList.remove('sel'));
$('#'+c).classList.add('sel');$('#ir').disabled=false;}
$('#lite').onclick=()=>elige('lite');
$('#full').onclick=()=>elige('full');
$('#ir').onclick=async()=>{
  if(!elegido)return;
  const r=await fetch('/api/mode',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({modo:elegido})});
  const j=await r.json();
  const m=$('#msg');
  if(!j.ok){m.className='mal';m.textContent='No se pudo guardar: '+(j.reason||'');return;}
  m.className='ok';
  m.textContent='Modo '+(elegido==='lite'?'LITE':'FULL')+'. Abriendo la configuración…';
  setTimeout(()=>{location.href='/setup';},700);
};
</script></body></html>"""


def _origen_camara(request) -> str:
    """El origen que hay que devolverle a la camara: el suyo si es de los
    nuestros, y cadena vacia si no.

    Antes esto era "*" en los siete sitios, con lo que cualquier pagina de
    internet podia pedir una foto. Ahora solo contesta el propio JARVIS.
    """
    import auth as _auth
    o = request.headers.get("origin") if request is not None else None
    if o and _auth.origen_permitido(o):
        return str(o)
    return ""
