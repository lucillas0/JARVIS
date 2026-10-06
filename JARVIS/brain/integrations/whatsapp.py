"""WhatsApp — SOLO LECTURA (sección 9). Sin envío de mensajes.

Estrategia no invasiva y segura frente a baneos: si la app abierta WhatsApp for
Desktop (`WhatsApp.exe`) está corriendo, leemos el último chat visible en su
árbol de UI (mensajes entrantes recientes) usando pywinauto. Nunca se autoclick
ni se envía nada.

comprobar_mensajes_nuevos() se usa desde el Modo Despertar y bajo demanda.
"""
import logging
import subprocess

log = logging.getLogger("integrations.whatsapp")


def app_running() -> bool:
    # La app moderna corre como WhatsApp.Root.exe (antes WhatsApp.exe).
    for img in ("WhatsApp.Root.exe", "WhatsApp.exe"):
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {img}"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if img in r.stdout:
                return True
        except Exception:
            continue
    return False


def read_recent_chat() -> list[dict]:
    """Lee (sin interactuar) las líneas de mensajes visibles del chat abierto."""
    if not app_running():
        return []
    try:
        from pywinauto import Desktop
        windows = Desktop(backend="uia").windows()
        for w in windows:
            if "whatsapp" in w.window_text().lower():
                elements = w.descendants(control_type="Text")
                lines = [e.window_text().strip() for e in elements
                         if e.window_text().strip()]
                # Reconstruir pares probable (nombre del contacto: mensaje) es
                # propenso a errores, así que devolvemos las últimas 12 líneas visibles.
                recent = lines[-12:]
                if recent:
                    return [{"line": l} for l in recent]
    except Exception as e:
        log.warning("pywinauto no pudo leer WhatsApp: %s", e)
    return []


def unread_summary() -> dict:
    """Resumen honesto para el ritual: app abierta + líneas recientes visibles."""
    if not app_running():
        return {"open": False, "lines": []}
    lines = [d.get("line", "") for d in read_recent_chat() if d.get("line")]
    return {"open": True, "lines": lines}


def open_web() -> dict:
    """Abre WhatsApp Web en el navegador predeterminado (sesión del señor)."""
    import webbrowser
    url = "https://web.whatsapp.com"
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        raise RuntimeError(f"No pude abrir el navegador: {e}")
    return {"url": url}


def read_web_window() -> list[dict]:
    """Lee (sin interactuar) el texto visible de una ventana con WhatsApp Web."""
    try:
        from pywinauto import Desktop
        wins = Desktop(backend="uia").windows()
        for w in wins:
            try:
                title = (w.window_text() or "").lower()
            except Exception:
                continue
            if "whatsapp" not in title:
                continue
            try:
                elements = w.descendants(control_type="Text")
            except Exception:
                continue
            lines = [e.window_text().strip() for e in elements
                     if e.window_text().strip()]
            recent = [l for l in lines[-18:] if len(l) > 1]
            if recent:
                return [{"line": l} for l in recent]
    except Exception as e:
        log.warning("pywinauto no pudo leer WhatsApp Web: %s", e)
    return []


def read_anywhere() -> dict:
    """Lee WhatsApp donde esté visible: app Desktop primero, si no Web."""
    if app_running():
        lines = [d.get("line", "") for d in read_recent_chat() if d.get("line")]
        if lines:
            return {"source": "app", "lines": lines}
    web = [d.get("line", "") for d in read_web_window() if d.get("line")]
    if web:
        return {"source": "web", "lines": web}
    return {"source": "none", "lines": []}


def recent_notifications() -> list[dict]:
    """Comprueba notificaciones recientes de WhatsApp (WNS) registradas por JARVIS."""
    from memory import sqlite_store as db
    rows = db.search_messages("whatsapp", limit=5)
    return rows