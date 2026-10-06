"""Permission & Risk Engine (sección 6 de la especificación).

- Ninguna acción se ejecuta directo desde el LLM: siempre pasa por el Tool
  Manager, que consulta a este motor.
- Auditoría de todo lo ejecutado, sin excepción.
- Las acciones "sensibles" requieren confirmación por voz Y popup (doble canal)
  con un timeout configurable (30s por defecto).
"""
import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Optional

from memory import sqlite_store as db

log = logging.getLogger("risk_engine")

RISK_LEVELS = ("low", "medium", "sensitive")

# Marcos de amplios grupos de acciones sensibles: si una acción nueva no está
# etiquetada en su definición, este motor decide por heurística.
SENSITIVE_KEYWORDS = ("delete", "remove", "borrar", "eliminar", "shutdown", "apagar",
                      "restart", "reboot", "suspend", "hibernate", "kill", "matar",
                      "wifi_off", "firewall", "netsh", "uninstall", "desinstalar")

MEDIUM_KEYWORDS = ("volume", "volumen", "media", "spotify_play", "spotify_pause",
                   "window_", "minimizar", "maximizar", "cerrar_ventana")


def classify(action_type: str) -> str:
    a = action_type.lower()
    if any(k in a for k in MEDIUM_KEYWORDS):
        return "medium"
    if any(k in a for k in SENSITIVE_KEYWORDS):
        return "sensitive"
    return "low"


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

    def __init__(self, emit, confirmation_timeout: Optional[int] = None):
        """emit(ws_message: dict) → envía mensajes al app."""
        self.emit = emit
        self.timeout = confirmation_timeout or 30
        self._pending: dict[str, PendingConfirmation] = {}

    async def check(self, tool: dict, params: dict) -> bool:
        """True → ejecutar. False → no ejecutar.

        Riesgo low     → ejecuta sin pedir nada.
        Riesgo medium  → ejecuta (se registra en auditoría con confirmed=None).
        Riesgo sensitive → pide confirmación (voz + popup), timeout 30s.
        """
        name = tool.get("name", "?")
        risk = tool.get("risk_level") or classify(name)
        if risk not in RISK_LEVELS:
            risk = "medium"
        requires = tool.get("requires_confirmation", False) or risk == "sensitive"

        if not requires:
            db.add_audit(name, params, risk, None, "success", "auto")
            return True

        action_id = uuid.uuid4().hex[:8]
        description = tool.get("confirm_text", "").format(**params) or (
            f"Voy a ejecutar: {name} ({params}). ¿Confirma?"
        )

        pending = PendingConfirmation(action_id, description, self.timeout)
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
        p = self._pending.get(action_id)
        if not p:
            return False
        p.set_result(approved)
        return True