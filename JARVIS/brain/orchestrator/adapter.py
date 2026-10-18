"""OpenCodeAdapter: el único módulo que habla con OpenCode.

Reglas del diseño (por qué está separado):

  · No sabe nada de FastAPI, ni de la interfaz, ni de React/Vue. Solo habla
    con el servidor de OpenCode y devuelve datos reales.
  · No inventa actividad. Si OpenCode no ha dicho nada, aquí no hay nada.
  · No se salta el sistema de permisos de OpenCode: lo que OpenCode pide se
    entrega tal cual a quien decide, y la decisión se devuelve a OpenCode con
    sus propios valores (`once`, `always`, `reject`).

Todo lo que devuelve sale de la API: /api/session, /api/session/{id}/message,
/api/session/{id}/permission, /api/session/{id}/form, /api/vcs/status.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

from . import service
from .client import OpenCodeClient, OpenCodeError

log = logging.getLogger("brain.opencode.adapter")

# Respuestas posibles de un permiso en OpenCode. Los mismos valores que usa su
# propia API; no se inventan otros porque no los entendería.
PERMISSION_DECISIONS = ("once", "always", "reject")
PERMISSION_LABEL = {"once": "Una vez", "always": "Siempre", "reject": "Denegar"}

# Eventos internos que JARVIS Live y el resto de JARVIS consumen.
EVENT_STARTED = "opencode.started"
EVENT_STOPPED = "opencode.stopped"
EVENT_OUTPUT = "opencode.output"
EVENT_TOOL_CALL = "opencode.tool_call"
EVENT_PERMISSION_REQUESTED = "opencode.permission_requested"
EVENT_PERMISSION_RESOLVED = "opencode.permission_resolved"
EVENT_QUESTION_REQUESTED = "opencode.question_requested"
EVENT_QUESTION_ANSWERED = "opencode.question_answered"
EVENT_FILE_CHANGED = "opencode.file_changed"
EVENT_TEST_STARTED = "opencode.test_started"
EVENT_TEST_FINISHED = "opencode.test_finished"
EVENT_ERROR = "opencode.error"
EVENT_COMPLETED = "opencode.completed"

_TEST_PATTERNS = (
    r"\bpytest\b", r"\bpython\s+-m\s+unittest\b", r"\btox\b", r"\bnosetests\b",
    r"\bnpm\s+(run\s+)?test\b", r"\byarn\s+test\b", r"\bpnpm\s+(run\s+)?test\b",
    r"\bbun\s+test\b", r"\bjest\b", r"\bvitest\b", r"\bmocha\b", r"\bgo\s+test\b",
    r"\bcargo\s+test\b", r"\bmvn\s+(?:-[\w-]+\s+)*test\b", r"\bgradle\s+test\b",
    r"\bphpunit\b", r"\brspec\b", r"\bmake\s+test\b", r"\bnx\s+(run\s+)?\w*:?test\b",
    r"\bphp\s+unit\b", r"\bdotnet\s+test\b", r"\bflutter\s+test\b",
)
_TEST_RE = re.compile("|".join(_TEST_PATTERNS), re.I)

# Extensiones que cuentan como "código del proyecto" para detectar cambios sin Git.
_SOURCE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".html",
               ".css", ".md", ".yml", ".yaml", ".toml", ".sql", ".sh", ".ps1", ".txt")
_SKIP_DIRS = ("node_modules", "venv", ".venv", "dist", "build", "dist-installer",
              "win-unpacked", "__pycache__", ".git", "qdrant", "wa-session", ".next")


# --------------------------------------------------------------------------- #
# Utilidades puras (testeables sin red)
# --------------------------------------------------------------------------- #
def texto_tool(state: dict, limite: int = 4000) -> str:
    """Texto que devolvió una herramienta, tal cual venga."""
    partes = []
    for bloque in (state or {}).get("content") or []:
        if isinstance(bloque, dict):
            valor = bloque.get("text") or bloque.get("content") or bloque.get("data")
            if isinstance(valor, str):
                partes.append(valor)
            elif valor is not None:
                partes.append(json.dumps(valor, ensure_ascii=False))
        elif isinstance(bloque, str):
            partes.append(bloque)
    return "\n".join(partes)[:limite]


def _entrada_resumen(entrada: dict, limite: int = 300) -> str:
    """Una línea legible de lo que se pasó a la herramienta."""
    if not isinstance(entrada, dict):
        return str(entrada or "")[:limite]
    for clave in ("command", "filePath", "file_path", "path", "pattern", "query",
                  "url", "description", "prompt"):
        valor = entrada.get(clave)
        if isinstance(valor, str) and valor.strip():
            una = " ".join(valor.split())
            return una[:limite]
    try:
        return json.dumps(entrada, ensure_ascii=False)[:limite]
    except (TypeError, ValueError):
        return str(entrada)[:limite]


def resumir_parte(parte: dict) -> Optional[dict]:
    """Parte de contenido de un mensaje -> {name, status, detail, call}."""
    if not isinstance(parte, dict) or parte.get("type") != "tool":
        return None
    estado = parte.get("state") or {}
    nombre = str(parte.get("tool") or parte.get("name") or "herramienta")
    status = str(estado.get("status") or "running")
    detalle = _entrada_resumen(estado.get("input"))
    if status == "error":
        error = estado.get("error") or {}
        mensaje = error.get("message") if isinstance(error, dict) else str(error)
        if mensaje:
            detalle = f"{detalle} → error: {mensaje}" if detalle else f"error: {mensaje}"
    return {"call": str(parte.get("callID") or ""), "name": nombre,
            "status": status, "detail": detalle}


def es_comando_de_tests(comando: str) -> bool:
    """¿Ese comando es una batería de tests, lint, build o typecheck?"""
    return bool(_TEST_RE.search(comando or ""))


def contar_tests(salida: str) -> dict:
    """Números reales de un informe de tests. Sin inventar: si no aparece, None."""
    texto = salida or ""
    def _n(patron: str) -> Optional[int]:
        m = re.search(patron, texto, re.I)
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", "").replace(".", "") if m.lastindex
                       else m.group(0))
        except (ValueError, AttributeError):
            return None
    return {
        "passed": _n(r"(\d+)\s+passed") or _n(r"passed\s*[:=]\s*(\d+)"),
        "failed": _n(r"(\d+)\s+failed") or _n(r"failed\s*[:=]\s*(\d+)"),
        "errors": _n(r"(\d+)\s+error") or _n(r"errors?\s*[:=]\s*(\d+)"),
        "skipped": _n(r"(\d+)\s+skipped"),
        "total": _n(r"(\d+)\s+total"),
    }


def resumen_tests(salida: str) -> str:
    """Primera línea útil del informe, para el panel."""
    for linea in (salida or "").splitlines():
        limpia = linea.strip()
        if limpia and re.search(r"passed|failed|error|ok\b|tests?:", limpia, re.I):
            return limpia[:200]
    for linea in (salida or "").splitlines():
        if linea.strip():
            return linea.strip()[:200]
    return ""


def normalizar_permiso(p: dict) -> dict:
    """Petición de permiso de OpenCode -> algo que JARVIS Live pueda pintar."""
    recursos = [str(r)[:200] for r in (p.get("resources") or []) if str(r).strip()]
    return {
        "id": str(p.get("id") or ""),
        "session_id": str(p.get("sessionID") or ""),
        "action": str(p.get("action") or "acción"),
        "resources": recursos,
        "message": str(p.get("message") or "")[:600],
        "detail": recursos[0] if recursos else str(p.get("message") or "")[:400],
        "save": [str(s)[:120] for s in (p.get("save") or [])],
        "source": (p.get("source") or {}).get("type", "") if isinstance(p.get("source"), dict) else "",
        "decisions": [{"value": d, "label": PERMISSION_LABEL[d]} for d in PERMISSION_DECISIONS],
    }


def normalizar_pregunta(form: dict) -> dict:
    """Formulario de OpenCode (sus 'preguntas') -> pregunta con opciones."""
    campos = form.get("fields") or []
    principal = {}
    for campo in campos:
        if isinstance(campo, dict) and not principal:
            principal = campo
        elif isinstance(campo, dict) and campo.get("options") and not principal.get("options"):
            principal = campo
    opciones = []
    for opcion in principal.get("options") or []:
        if isinstance(opcion, dict):
            opciones.append({"value": str(opcion.get("value") or opcion.get("label") or ""),
                             "label": str(opcion.get("label") or opcion.get("value") or "")[:160],
                             "description": str(opcion.get("description") or "")[:240]})
    extras = []
    for campo in campos:
        if isinstance(campo, dict) and campo is not principal and not campo.get("hidden"):
            extras.append({"key": str(campo.get("key") or ""),
                           "title": str(campo.get("title") or campo.get("key") or "")[:160],
                           "type": str(campo.get("type") or "string"),
                           "required": bool(campo.get("required")),
                           "custom": bool(campo.get("custom")),
                           "options": [{"value": str(o.get("value")), "label": str(o.get("label"))}
                                       for o in (campo.get("options") or [])
                                       if isinstance(o, dict)]})
    return {
        "id": str(form.get("id") or ""),
        "session_id": str(form.get("sessionID") or ""),
        "question": str(form.get("title") or principal.get("title") or "OpenCode necesita una decisión"),
        "description": str(principal.get("description") or "")[:600],
        "key": str(principal.get("key") or "respuesta"),
        "options": opciones,
        "extras": extras,
        "multiple": str(principal.get("type") or "") == "multiselect",
        "allow_custom": bool(principal.get("custom")) or not opciones,
        "state": str((form.get("state") or {}).get("status") or "pending"),
    }


def cambios_desde_git(datos: dict) -> list[dict]:
    """Respuesta real de /api/vcs.status -> lista de cambios."""
    salida = []
    for item in (datos or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        estado = str(item.get("status") or "modified")
        marca = {"added": "+", "deleted": "-", "modified": "~"}.get(estado, "~")
        salida.append({"path": str(item.get("file") or "")[:240], "action": estado,
                       "mark": marca, "additions": item.get("additions"),
                       "deletions": item.get("deletions")})
    return salida


def instantanea(raiz: str, limite: int = 4000) -> dict:
    """Huella de los ficheros de código. Sirve para ver cambios sin Git."""
    import hashlib

    mapa: dict = {}
    raiz = os.path.abspath(raiz or ".")
    for carpeta, carpetas, ficheros in os.walk(raiz):
        carpetas[:] = [c for c in carpetas if c not in _SKIP_DIRS and not c.startswith(".")]
        if any(s in carpeta.replace("\\", "/") for s in _SKIP_DIRS):
            continue
        for nombre in ficheros:
            if not nombre.endswith(_SOURCE_EXT):
                continue
            ruta = os.path.join(carpeta, nombre)
            try:
                with open(ruta, "rb") as f:
                    mapa[ruta] = hashlib.sha1(f.read()).hexdigest()
            except OSError as exc:
                # Saltarse el fichero ilegible es lo correcto (bloqueado, en
                # uso, borrado entre medias), pero se anota: si la instantánea
                # sale incompleta sin que nadie lo sepa, restaurar después
                # revierte cambios que en realidad nunca se midieron.
                log.debug("fichero no legible para la instantanea, se salta: %s", exc)
                continue
            if len(mapa) >= limite:
                return mapa
    return mapa


def diff_instantanea(antes: dict, despues: dict, raiz: str) -> list[dict]:
    """Diferencia real entre dos instantáneas: + creado, ~ modificado, - borrado."""
    salida = []
    for ruta in sorted(set(antes) | set(despues)):
        a, d = antes.get(ruta), despues.get(ruta)
        if a == d:
            continue
        if a is None:
            marca, estado = "+", "added"
        elif d is None:
            marca, estado = "-", "deleted"
        else:
            marca, estado = "~", "modified"
        try:
            relativo = os.path.relpath(ruta, raiz)
        except ValueError:
            relativo = ruta
        salida.append({"path": relativo[:240], "action": estado, "mark": marca,
                       "additions": None, "deletions": None})
    return salida


# --------------------------------------------------------------------------- #
# El adaptador
# --------------------------------------------------------------------------- #
class OpenCodeAdapter:
    """Una sesión de OpenCode vigilada de verdad."""

    def __init__(self, emit: Optional[Callable[[dict], None]] = None):
        self._emit = emit
        self.client: Optional[OpenCodeClient] = None
        self.session_id: str = ""
        self.workspace: str = ""
        self.model: str = ""
        self.agent: str = ""
        self.title: str = ""
        self.server: dict = {}
        self.events: list[dict] = []
        self._lock = threading.RLock()
        self._snapshot: dict = {}
        self._started_at = 0.0
        self.last_error: str = ""
        self.started = False

    # ------------------------------ eventos ------------------------------ #
    def _publish(self, tipo: str, **campos) -> dict:
        evento = {"type": tipo, "session_id": self.session_id, "at": time.time(), **campos}
        with self._lock:
            self.events.append(evento)
            if len(self.events) > 400:
                del self.events[:-400]
        if self._emit:
            try:
                self._emit(evento)
            except Exception as exc:  # noqa: BLE001 - el bus no puede romper la tarea
                log.debug("no se pudo publicar %s: %s", tipo, exc)
        return evento

    # ------------------------------ ciclo de vida ------------------------------ #
    def start(self, workspace: str, title: str = "", model: str = "",
              agent: str = "") -> dict:
        """Abre (o reutiliza) la sesión de OpenCode. Sin sesión no hay tarea."""
        self.workspace = os.path.abspath(workspace)
        self.title = title or "JARVIS"
        self.model = model or os.environ.get("OPENCODE_MODEL", "").strip()
        self.agent = agent or os.environ.get("OPENCODE_AGENT", "").strip()

        endpoint = service.ensure()
        if not endpoint.get("ok"):
            self.last_error = endpoint.get("error", "OpenCode no disponible.")
            self._publish(EVENT_ERROR, error=self.last_error, detail=self.last_error)
            return {"ok": False, "error": self.last_error}
        self.server = endpoint
        self.client = OpenCodeClient(endpoint["url"], endpoint.get("username", ""),
                                     endpoint.get("password", ""), timeout=30.0)

        cuerpo = {"title": self.title[:120], "location": {"directory": self.workspace}}
        if self.model:
            cuerpo["model"] = self.model
        if self.agent:
            cuerpo["agent"] = self.agent
        try:
            sesion = self.client.post("/api/session", cuerpo)
        except OpenCodeError as exc:
            self.last_error = f"No se pudo abrir la sesión de OpenCode: {exc}"
            self._publish(EVENT_ERROR, error=self.last_error, detail=self.last_error)
            return {"ok": False, "error": self.last_error}
        if not isinstance(sesion, dict) or not sesion.get("id"):
            self.last_error = "OpenCode no devolvió una sesión válida."
            self._publish(EVENT_ERROR, error=self.last_error, detail=self.last_error)
            return {"ok": False, "error": self.last_error}

        self.session_id = str(sesion.get("id"))
        self.started = True
        self._started_at = time.time()
        self._snapshot = instantanea(self.workspace)
        self._publish(EVENT_STARTED, detail=f"Sesión {self.session_id} en {self.workspace}",
                      workspace=self.workspace, url=endpoint.get("url", ""))
        return {"ok": True, "session_id": self.session_id,
                "workspace": self.workspace, "url": endpoint.get("url", ""),
                "source": endpoint.get("source", "")}

    def attach(self, session_id: str, workspace: str = "") -> dict:
        """Recoloca una sesión que ya existe (continuar, recuperar, reintentar)."""
        endpoint = service.ensure()
        if not endpoint.get("ok"):
            self.last_error = endpoint.get("error", "OpenCode no disponible.")
            return {"ok": False, "error": self.last_error}
        self.server = endpoint
        self.client = OpenCodeClient(endpoint["url"], endpoint.get("username", ""),
                                     endpoint.get("password", ""), timeout=30.0)
        self.session_id = str(session_id or "")
        self.workspace = os.path.abspath(workspace or self.workspace or os.getcwd())
        info = self.get_session()
        if not info.get("ok"):
            self.last_error = info.get("error", "La sesión ya no existe en OpenCode.")
            return {"ok": False, "error": self.last_error}
        self.started = True
        self._started_at = time.time()
        self._snapshot = instantanea(self.workspace)
        self._publish(EVENT_STARTED, detail=f"Continuando la sesión {self.session_id}",
                      workspace=self.workspace, resumed=True)
        return {"ok": True, "session_id": self.session_id,
                "title": info.get("session", {}).get("title", "")}

    def stop(self, reason: str = "") -> dict:
        """Deja de mirar. No cierra la sesión: sigue viva para poder continuar."""
        self.started = False
        self._publish(EVENT_STOPPED, detail=reason or "JARVIS dejó de vigilar esta sesión.")
        return {"ok": True}

    def pause(self) -> dict:
        """Interrumpe el turno en curso (OpenCode conserva lo hecho)."""
        if not self.client or not self.session_id:
            return {"ok": False, "error": "No hay sesión abierta."}
        try:
            r = self.client.post(f"/api/session/{self.session_id}/interrupt")
        except OpenCodeError as exc:
            return {"ok": False, "error": str(exc)}
        interrumpido = bool((r or {}).get("interrupted")) if isinstance(r, dict) else True
        return {"ok": True, "interrupted": interrumpido,
                "detail": "Turno interrumpido." if interrumpido
                          else "OpenCode no estaba ejecutando nada ahora mismo."}

    def resume(self) -> dict:
        """Sigue con la misma sesión. No crea sesiones nuevas por gusto."""
        if not self.session_id:
            return {"ok": False, "error": "No hay sesión que continuar."}
        return {"ok": True, "session_id": self.session_id}

    def send_prompt(self, text: str, timeout: float = 7200.0) -> dict:
        """Manda el prompt a OpenCode y espera a que termine el turno.

        La llamada es larga a propósito: OpenCode contesta cuando la sesión
        queda libre. Por eso el orquestador la lanza en su propio hilo.
        """
        if not self.client or not self.session_id:
            return {"ok": False, "error": "No hay sesión de OpenCode."}
        if not (text or "").strip():
            return {"ok": False, "error": "El prompt está vacío."}
        try:
            respuesta = self.client.post(f"/api/session/{self.session_id}/prompt",
                                         {"text": text}, timeout=timeout)
        except OpenCodeError as exc:
            self.last_error = str(exc)
            self._publish(EVENT_ERROR, error=str(exc), detail=str(exc))
            return {"ok": False, "error": str(exc)}
        resumen = respuesta_texto(respuesta)
        if resumen:
            self._publish(EVENT_OUTPUT, detail=resumen[:1200], text=resumen[:4000])
        return {"ok": True, "reply": resumen, "message": respuesta}

    def continue_session(self, text: str = "") -> dict:
        """Continúa la sesión ya existente con un mensaje nuevo."""
        if not (text or "").strip():
            return {"ok": False, "error": "No hay nada que enviar."}
        return self.send_prompt(text)

    # ------------------------------ permisos ------------------------------ #
    def pending_permissions(self) -> list[dict]:
        if not self.client or not self.session_id:
            return []
        try:
            datos = self.client.get(f"/api/session/{self.session_id}/permission",
                                    timeout=15.0)
        except OpenCodeError as exc:
            log.debug("permisos de OpenCode: %s", exc)
            return []
        lista = datos.get("data") if isinstance(datos, dict) else datos
        return [normalizar_permiso(p) for p in (lista or []) if isinstance(p, dict)]

    def reply_permission(self, permission_id: str, decision: str) -> dict:
        """Devuelve la decisión al sistema de permisos de OpenCode."""
        if decision not in PERMISSION_DECISIONS:
            return {"ok": False, "error": "Decisión no válida para OpenCode."}
        if not self.client or not self.session_id:
            return {"ok": False, "error": "No hay sesión de OpenCode."}
        try:
            self.client.post(
                f"/api/session/{self.session_id}/permission/{permission_id}/reply",
                {"decision": decision}, timeout=20.0)
        except OpenCodeError as exc:
            return {"ok": False, "error": str(exc)}
        self._publish(EVENT_PERMISSION_RESOLVED, permission_id=permission_id,
                      decision=decision, detail=f"Permiso {decision}")
        return {"ok": True, "decision": decision}

    def approve_permission(self, permission_id: str, always: bool = False) -> dict:
        return self.reply_permission(permission_id, "always" if always else "once")

    def reject_permission(self, permission_id: str) -> dict:
        return self.reply_permission(permission_id, "reject")

    # ------------------------------ preguntas ------------------------------ #
    def pending_questions(self) -> list[dict]:
        if not self.client or not self.session_id:
            return []
        try:
            datos = self.client.get(f"/api/session/{self.session_id}/form", timeout=15.0)
        except OpenCodeError as exc:
            log.debug("preguntas de OpenCode: %s", exc)
            return []
        lista = datos.get("data") if isinstance(datos, dict) else datos
        return [normalizar_pregunta(f) for f in (lista or []) if isinstance(f, dict)]

    def send_answer(self, question_id: str, answer: dict) -> dict:
        """Entrega la respuesta del usuario a la pregunta de OpenCode."""
        if not self.client or not self.session_id:
            return {"ok": False, "error": "No hay sesión de OpenCode."}
        cuerpo = {"answer": answer if isinstance(answer, dict) else {"respuesta": str(answer)}}
        try:
            self.client.post(f"/api/session/{self.session_id}/form/{question_id}/reply",
                             cuerpo, timeout=20.0)
        except OpenCodeError as exc:
            return {"ok": False, "error": str(exc)}
        self._publish(EVENT_QUESTION_ANSWERED, question_id=question_id,
                      detail=str(list(cuerpo["answer"].values())[:1]))
        return {"ok": True}

    # ------------------------------ observación ------------------------------ #
    def get_session(self) -> dict:
        if not self.client or not self.session_id:
            return {"ok": False, "error": "No hay sesión."}
        try:
            datos = self.client.get(f"/api/session/{self.session_id}", timeout=15.0)
        except OpenCodeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "session": datos if isinstance(datos, dict) else {}}

    def get_messages(self, limit: int = 40) -> list[dict]:
        """Mensajes reales de la sesión, del más nuevo al más viejo."""
        if not self.client or not self.session_id:
            return []
        try:
            datos = self.client.get(f"/api/session/{self.session_id}/message",
                                    {"limit": str(limit), "order": "desc"}, timeout=20.0)
        except OpenCodeError as exc:
            log.debug("mensajes de OpenCode: %s", exc)
            return []
        lista = datos.get("data") if isinstance(datos, dict) else datos
        return [m for m in (lista or []) if isinstance(m, dict)]

    def get_changes(self) -> list[dict]:
        """Cambios reales: Git si el proyecto lo tiene, instantánea si no."""
        if not self.client or not self.workspace:
            return []
        ruta = self.client.query_path("/api/vcs/status",
                                      **{"location[directory]": self.workspace})
        try:
            datos = self.client.get(ruta, timeout=20.0)
        except OpenCodeError:
            datos = None
        if isinstance(datos, dict) and datos.get("data"):
            return cambios_desde_git(datos)
        despues = instantanea(self.workspace)
        return diff_instantanea(self._snapshot, despues, self.workspace)

    def refresh_snapshot(self) -> list[dict]:
        """Compara contra la última foto y deja la nueva como referencia."""
        if not self.workspace:
            return []
        despues = instantanea(self.workspace)
        cambios = diff_instantanea(self._snapshot, despues, self.workspace)
        self._snapshot = despues
        for cambio in cambios:
            self._publish(EVENT_FILE_CHANGED, path=cambio["path"],
                          action=cambio["action"], detail=f"{cambio['mark']} {cambio['path']}")
        return cambios

    def get_events(self, limit: int = 120) -> list[dict]:
        with self._lock:
            return list(self.events)[-limit:]

    def get_status(self) -> dict:
        """Lo que se sabe del servidor y de la sesión, sin inventar."""
        base = {"ok": bool(self.client and self.session_id),
                "session_id": self.session_id, "workspace": self.workspace,
                "model": self.model, "agent": self.agent,
                "started_at": self._started_at,
                "url": self.server.get("url", ""),
                "source": self.server.get("source", ""),
                "cli": service.cli_path(),
                "error": self.last_error}
        if self.client and self.session_id:
            info = self.get_session()
            base["ok"] = bool(info.get("ok"))
            base["session"] = info.get("session", {})
            base["idle_since"] = (info.get("session") or {}).get("time", {}).get("idle")
            if not info.get("ok"):
                base["error"] = info.get("error", "")
        return base

    def health(self) -> dict:
        if not self.client:
            return {"ok": False, "error": "El adaptador no tiene servidor asignado."}
        return service.health(self.server.get("url", ""),
                              self.server.get("username", ""),
                              self.server.get("password", ""))


def respuesta_texto(respuesta: object) -> str:
    """Texto final de una respuesta de /prompt, si lo hay."""
    if isinstance(respuesta, str):
        return respuesta.strip()
    if not isinstance(respuesta, dict):
        return ""
    partes = respuesta.get("parts") or []
    textos = []
    for parte in partes:
        if isinstance(parte, dict) and parte.get("type") == "text":
            valor = parte.get("text")
            if isinstance(valor, str):
                textos.append(valor.strip())
    if textos:
        return "\n".join(textos).strip()
    info = respuesta.get("info") or respuesta
    contenido = info.get("content") if isinstance(info, dict) else None
    if isinstance(contenido, list):
        for parte in contenido:
            if isinstance(parte, dict) and parte.get("type") == "text" and parte.get("text"):
                textos.append(str(parte["text"]).strip())
    if textos:
        return "\n".join(textos).strip()
    for clave in ("text", "summary", "result"):
        valor = respuesta.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""