"""Proactive Engine (sección 8).

Detección de contexto por proceso/ventana activa + análisis de pantalla
(continuo). Puede actuar con libertad (silenciar notificaciones en reunión,
pausar música en gaming), pero lo "sensible" siempre pasa por el doble canal.
"""
import ctypes
import logging
import threading
import time
from typing import Callable, Optional

from config import Config

log = logging.getLogger("proactive")

user32 = ctypes.windll.user32

CONTEXTS = {"coding", "gaming", "meeting", "writing", "browsing", "media", "idle"}

MEETING_APPS = ("zoom", "meet", "teams", "webex", "meeting", "slack")
CODING_APPS = ("vscode", "code", "visual studio", "pycharm", "intellij", "sublime", "cursor", "windsurf", "notepad")
GAMING_HINTS = ("steam", "galaxy", "epicgames", "geforce", ".exe")
MEDIA_APPS = ("spotify", "vlc", "mpv", "foobar", "youtube")


class ActiveWindowInfo:
    def __init__(self, proc_name: str = "", title: str = ""):
        self.proc_name = proc_name.lower()
        self.title = title.lower()

    @property
    def context(self) -> str:
        p, t = self.proc_name, self.title
        if any(m in p or m in t for m in MEETING_APPS):
            return "meeting"
        if any(m in p or m in t for m in CODING_APPS):
            return "coding"
        if any(m in p or m in t for m in MEDIA_APPS):
            return "media"
        if any(g in t for g in GAMING_HINTS) or self._looks_like_game():
            return "gaming"
        if "chrome" in p or "firefox" in p or "edge" in p or "brave" in p:
            return "browsing"
        if "winword" in p or "wps" in p or "notepad" in p or "word" in p:
            return "writing"
        return "idle"

    def _looks_like_game(self) -> bool:
        try:
            import psutil
            pid = _foreground_pid()
            if not pid:
                return False
            proc = psutil.Process(pid)
            exe = proc.exe().lower()
            return "game" in exe or "games" in exe
        except Exception:
            return False


def _foreground_pid() -> Optional[int]:
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


def foreground_window_info() -> ActiveWindowInfo:
    info = ActiveWindowInfo()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return info
    length = user32.GetWindowTextLengthW(hwnd)
    if length:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        info.title = buf.value
    pid = _foreground_pid()
    if pid:
        try:
            import psutil
            info.proc_name = psutil.Process(pid).name() or ""
        except Exception:
            pass
    return info


class ProactiveEngine:
    def __init__(self, emit: Callable[[dict], None],
                 run_tool: Callable[[str, dict], dict],
                 get_context_callback: Optional[Callable[[], str]] = None):
        """emit → a la app. run_tool(tool, params) → pasa por el Tool Manager
        (y por el Risk Engine). get_context_callback() → descripción de pantalla."""
        self.emit = emit
        self.run_tool = run_tool
        self.get_context_callback = get_context_callback
        self._enabled = True
        self._last_context = "idle"
        self._last_notify = 0.0
        self._screen_b64: Optional[str] = None

    def set_screen_capture(self, b64: Optional[str]) -> None:
        self._screen_b64 = b64

    def current_context(self) -> str:
        win = foreground_window_info()
        ctx = win.context
        # El análisis de pantalla puede refinar (ej. si en VSCode depuras un error)
        if ctx == "coding" and self.get_context_callback:
            try:
                desc = self.get_context_callback()
                if desc and "depur" in desc.lower() or desc and "error" in desc.lower():
                    ctx = "coding_debug"
            except Exception:
                pass
        self._last_context = ctx
        return ctx

    def tick(self) -> None:
        """Se llama periódicamente desde el scheduler del brain."""
        if not self._enabled:
            return
        ctx = self.current_context()
        now = time.time()
        # Reunión: bajar notificaciones (acción medium → pasa por risk: no sensible)
        if ctx == "meeting" and (now - self._last_notify > 120):
            self._last_notify = now
            self.emit({"type": "proactive_notice", "context": ctx,
                       "message": "Estás en una reunión. Silencio las notificaciones."})
            try:
                self.run_tool("mute_notifications", {})
            except Exception as e:
                log.info("Mute notificaciones falló: %s", e)
        # Gaming con música: pausar música
        if ctx == "gaming":
            try:
                import subprocess
                r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
                                   capture_output=True, text=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if "Spotify.exe" in r.stdout and (now - self._last_notify > 90):
                    self._last_notify = now
                    self.emit({"type": "proactive_notice", "context": ctx,
                               "message": "Detecto gaming: pauso la música."})
                    self.run_tool("spotify_pause", {})
            except Exception:
                pass

    def pause_screen_analysis(self) -> None:
        """'Jarvis, deja de ver mi pantalla' → apaga el modo continuo al instante."""
        Config.SCREEN_ANALYSIS_MODE = "off"
        self.set_screen_capture(None)
        self.emit({"type": "screen_status", "state": "off"})
        log.info("Análisis de pantalla desactivado por comando.")

    def resume_screen_analysis(self) -> None:
        Config.SCREEN_ANALYSIS_MODE = "continuous"
        self.emit({"type": "screen_status", "state": "continuous"})
        log.info("Análisis de pantalla reactivado.")