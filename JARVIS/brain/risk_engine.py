"""Permission & Risk Engine (sección 6 de la especificación).

- Ninguna acción se ejecuta directo desde el LLM: siempre pasa por el Tool
  Manager, que consulta a este motor.
- Auditoría de todo lo ejecutado, sin excepción.
- Las acciones "sensibles" requieren confirmación por voz Y popup (doble canal)
  con un timeout configurable (30s por defecto).
"""
import asyncio
import logging
import threading
import uuid
from typing import Awaitable, Callable, Optional

from memory import sqlite_store as db

log = logging.getLogger("risk_engine")

# Los cuatro niveles que pide el diseño (LOW/MEDIUM/HIGH/CRITICAL). `sensitive`
# se conserva porque hay herramientas ya etiquetadas asi y quitarlo las dejaria
# con un nivel que el motor no reconoce; se trata como synonym de `high`.
RISK_LEVELS = ("low", "medium", "high", "critical", "sensitive")

SINONIMOS_NIVEL = {"sensitive": "high"}

# Lo que NO se puede deshacer o afecta al equipo entero. No basta con pedir
# confirmacion: estas nunca se ejecutan sin que el senor lo diga, y se avisa
# aunque la herramienta venga marcada como de riesgo bajo.
CRITICAL_KEYWORDS = ("desinstalar", "uninstall", "formatear", "format",
                     "borrar_disco", "wipe", "apagar_equipo", "shutdown_hard",
                     "cambio_de_red", "desactivar_seguridad", "firewall_off",
                     "borrar_todo", "drop_table", "rm_rf")

HIGH_KEYWORDS = ("delete", "remove", "borrar", "eliminar", "shutdown", "apagar",
                 "restart", "reboot", "suspend", "hibernate", "kill", "matar",
                 "wifi_off", "firewall", "netsh")

SENSITIVE_KEYWORDS = HIGH_KEYWORDS  # se mantiene el nombre por compatibilidad

MEDIUM_KEYWORDS = ("volume", "volumen", "media", "spotify_play", "spotify_pause",
                   "window_", "minimizar", "maximizar", "cerrar_ventana")


def classify(action_type: str) -> str:
    """Nivel de riesgo de una acción, por heurística si no viene etiquetada.

    El orden importa: se mira primero lo CRITICO (que no depende del nombre
    de la herramienta sino de lo que hace), luego MEDIUM, y despues HIGH.
    """
    a = action_type.lower()
    if any(k in a for k in CRITICAL_KEYWORDS):
        return "critical"
    if any(k in a for k in MEDIUM_KEYWORDS):
        return "medium"
    if any(k in a for k in HIGH_KEYWORDS):
        return "high"
    return "low"


def normalizar_nivel(nivel: str) -> str:
    """Deja el nivel en uno de los cuatro canónicos."""
    n = (nivel or "low").strip().lower()
    return SINONIMOS_NIVEL.get(n, n if n in RISK_LEVELS else "low")


class PendingConfirmation:
    """Representa una confirmación pendiente con timeout."""
    def __init__(self, action_id: str, description: str, timeout_seconds: int,
                 cancel_callback: Optional[Callable[[], Awaitable[None]]] = None):
        self.action_id = action_id
        self.description = description
        self.timeout_seconds = timeout_seconds
        self.cancel_callback = cancel_callback
        self.result: Optional[bool] = None
        self._event = asyncio.Event()

    def set_result(self, approved: bool) -> None:
        self.result = approved
        self._event.set()

    async def wait(self) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            self.result = False
            if self.cancel_callback is not None:
                try:
                    await self.cancel_callback()
                except Exception as e:
                    log.warning("cancel_callback falló: %s", e)
        return bool(self.result)


class RiskEngine:
    """Gestiona la puerta de seguridad: espera confirmación o ejecuta directo."""

    def __init__(self, emit, confirmation_timeout: Optional[int] = None,
                 session_id: str = ""):
        """emit(ws_message: dict) → envía mensajes al app."""
        self.emit = emit
        self.timeout = confirmation_timeout or 30
        # La sesión de JARVIS. Las autorizaciones de sesión solo valen dentro de
        # ella: al reiniciar el proceso se genera otra y las anteriores dejan de
        # contar, que es justo lo que evita que "acuerdos" sobrevivan a un
        # reinicio sin que nadie los haya vuelto a dar.
        import os as _os
        import uuid as _uuid

        self.session_id = session_id or _os.environ.get("JARVIS_SESSION_ID") or \
            _uuid.uuid4().hex[:12]
        self._pending: dict[str, PendingConfirmation] = {}
        self._pending_lock = threading.RLock()

    async def check(self, tool: dict, params: dict) -> bool:
        """True → ejecutar. False → no ejecutar.

        Riesgo low      → ejecuta sin pedir nada.
        Riesgo medium   → ejecuta (se registra en auditoría con confirmed=None).
        Riesgo high     → pide confirmación (voz + popup), timeout 30s.
                          'sensitive' es el mismo nivel con otro nombre.
        Riesgo critical → NUNCA se ejecuta automáticamente: pide confirmación y
                          avisa por el bus como incidente, porque es de las que
                          no tienen vuelta atrás.
        """
        name = tool.get("name", "?")
        risk = normalizar_nivel(tool.get("risk_level") or classify(name))
        if risk not in RISK_LEVELS:
            risk = "medium"
        # El Decision Engine traduce nivel → política. Aquí no se decide nada
        # nuevo: se pregunta a la pieza que traduce, y si algún día no está,
        # se aplica la regla de siempre (preguntar cuando el riesgo es alto).
        try:
            import decision_engine as _de

            _politica = _de.decidir(risk, contexto=name)
        except Exception as _e:  # noqa: BLE001
            log.debug("Decision Engine no disponible, se aplica la regla base: %s", _e)
            _politica = {"decision": "USER_APPROVAL" if risk in ("high", "critical")
                         else ("LOW_RISK" if risk == "medium" else "AUTONOMOUS"),
                         "requiere_confirmacion": risk in ("high", "critical"),
                         "nivel": risk, "motivo": "regla base del Risk Engine"}

        requires = (tool.get("requires_confirmation", False)
                    or bool(_politica["requiere_confirmacion"]))
        if risk == "critical":
            self.emit({"type": "incident.created", "severity": "high",
                       "source": "risk_engine",
                       "description": f"Acción crítica preparada: {name}",
                       "action": name,
                       "decision": _politica["decision"],
                       "motivo": _politica["motivo"]})

        # Antes de preguntar, se mira si el señor ya lo autorizó para esta categoría
        # exacta (FASE 7). El alcance es la herramienta con su tipo de
        # operación: una autorización NO se amplía sola a otra herramienta.
        if requires:
            try:
                import authz as _authz

                alcance = tool.get("scope") or f"{name}"
                previa = _authz.esta_autorizado(alcance, session_id=self.session_id)
                if previa.get("ok") and risk != "critical":
                    # Las críticas nunca se recordan, ni siquiera si alguien lo
                    # pidió: se comprueba en _authz.concede(), y aquí se
                    # repite a propósito para que quede claro en el código.
                    db.add_audit(name, params, risk, True, "success",
                                 f"autorizado:{previa.get('decision')}")
                    self.emit({"type": "authz.applied", "action": name,
                               "scope": alcance,
                               "decision": previa.get("decision")})
                    return True
                if not previa.get("ok") and "denegada" in str(previa.get("motivo")):
                    db.add_audit(name, params, risk, False, "cancelled",
                                 "denegado previamente")
                    return False
            except Exception as _e:  # noqa: BLE001
                log.debug("memoria de autorizaciones no disponible: %s", _e)

        if not requires:
            db.add_audit(name, params, risk, None, "success", "auto")
            return True

        action_id = uuid.uuid4().hex[:8]
        description = tool.get("confirm_text", "").format(**params) or (
            f"Voy a ejecutar: {name} ({params}). ¿Confirma?"
        )

        pending = PendingConfirmation(action_id, description, self.timeout)
        with self._pending_lock:
            self._pending[action_id] = pending

        # Doble canal: popup en la app (esta emit) + anuncio por voz (en main.py).
        self.emit({
            "type": "confirmation_request",
            "action_id": action_id,
            "description": description,
            "timeout_seconds": self.timeout,
        })
        log.info("Confirmación requerida para %s (%s)", name, action_id)

        approved = await pending.wait()
        with self._pending_lock:
            self._pending.pop(action_id, None)

        if approved:
            db.add_audit(name, params, risk, True, "success", "confirmado por usuario")
        else:
            if pending.result is None:
                db.add_audit(name, params, risk, False, "timeout", "sin respuesta en 30s")
            else:
                db.add_audit(name, params, risk, False, "cancelled", "usuario denegó")
        return approved

    def resolve(self, action_id: str, approved: bool) -> bool:
        with self._pending_lock:
            p = self._pending.get(action_id)
            if not p:
                return False
            p.set_result(approved)
            return True