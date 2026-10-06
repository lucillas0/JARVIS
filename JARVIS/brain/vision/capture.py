"""Visión de pantalla (sección 3.1).

- Modo continuo: captura periódica que envía contexto al Proactive Engine.
- Modo bajo pedido: la captura más reciente se usa cuando el usuario pregunta
  "¿qué dice esto?".
- Ventanas concretas en segundo plano: PrintWindow por título (apps y juegos
  en ventana; exclusiva a pantalla completa puede salir en negro y se avisa).
- Ligero: JPEG q65 + ancho máx 1280 (pesa ~10x menos que el PNG anterior).
- Privacidad NO negociable: las capturas solo viven en memoria, nunca en disco
  ni en el historial.
"""
import base64
import ctypes
import io
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("vision")

_capture_handlers = ["mss", "pil"]
_last_capture_b64: Optional[str] = None
_capture_lock = threading.Lock()
_handler_cooldown: dict[str, float] = {}


def _handler_ok(name: str) -> bool:
    """¿Pasó el enfriamiento tras un fallo? (los fallos transitorios como
    juegos en exclusiva ya no vetan el método para siempre)."""
    return time.time() >= _handler_cooldown.get(name, 0.0)


def _handler_fail(name: str, cooldown: int = 60) -> None:
    _handler_cooldown[name] = time.time() + cooldown

_JPEG_Q = 65
_MAX_W = 1280


def _grab_png_bytes() -> Optional[bytes]:
    # 1) mss (preferido, rápido)
    if "mss" in _capture_handlers and _handler_ok("mss"):
        try:
            import mss
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])  # pantalla principal
            from PIL import Image
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            return _jpeg_bytes(img)
        except Exception as e:
            log.warning("mss falló: %s", e)
            _handler_fail("mss")
    # 2) PIL.ImageGrab
    if "pil" in _capture_handlers and _handler_ok("pil"):
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=False)
            return _jpeg_bytes(img)
        except Exception as e:
            log.warning("PIL ImageGrab falló: %s", e)
            _handler_fail("pil")
    return None


def _jpeg_bytes(img) -> bytes:
    from PIL import Image
    if img.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > _MAX_W:
        h = int(img.height * _MAX_W / img.width)
        img = img.resize((_MAX_W, h))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=True)
    return buf.getvalue()


def capture_recent(force: bool = False) -> Optional[str]:
    """Devuelve (o fuerza) la captura como base64 JPEG en memoria."""
    global _last_capture_b64
    with _capture_lock:
        if force or _last_capture_b64 is None:
            png = _grab_png_bytes()
            if png:
                _last_capture_b64 = base64.b64encode(png).decode("ascii")
        return _last_capture_b64


def is_mostly_black(b64: str, threshold: float = 8.0) -> bool:
    """¿Imagen casi negra? (juego en exclusiva / ventana sin pintar)."""
    try:
        from PIL import Image, ImageStat
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
        img.thumbnail((160, 160))
        return ImageStat.Stat(img).mean[0] < threshold
    except Exception:
        return False


def find_window(fragment: str):
    """HWND de la primera ventana visible cuyo título contenga el fragmento."""
    if not fragment:
        return None, ""
    frag = fragment.strip().lower()
    if not frag:
        return None, ""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
            if frag in title.lower():
                found.append((hwnd, title))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception as e:
        log.debug("enumwindows: %s", e)
    return found[0] if found else (None, "")


def foreground_window():
    """(hwnd, título) de la ventana al frente."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, ""
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(n + 1, 2))
        user32.GetWindowTextW(hwnd, buf, max(n + 1, 2))
        return hwnd, buf.value or ""
    except Exception:
        return None, ""


def capture_window(fragment: str = "") -> tuple[Optional[str], str]:
    """Captura una ventana concreta (aunque esté en segundo plano) con
    PrintWindow. Sin fragmento = la ventana al frente.
    Devuelve (b64|None, título|motivo)."""
    if fragment:
        hwnd, title = find_window(fragment)
        if not hwnd:
            return None, f"No veo ninguna ventana con '{fragment}'."
    else:
        hwnd, title = foreground_window()
        if not hwnd:
            return None, "No hay ventana al frente."
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        from PIL import Image
        rect = (ctypes.c_long * 4)()
        if not user32.GetWindowRect(hwnd, rect):
            return None, f"No pude medir '{title}'."
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < 10 or h < 10 or w > 8000 or h > 8000:
            return None, f"Tamaño raro en '{title}'."
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        gdi32.SelectObject(hdc_mem, hbmp)
        try:
            # PW_RENDERFULLCONTENT = 0x2: pinta aunque esté oculta.
            ok = user32.PrintWindow(hwnd, hdc_mem, 0x2)
            if not ok:
                return None, f"No pude pintar '{title}'."
            bmpinfo = {"width": w, "height": h}
            buf_len = w * h * 4
            buf = (ctypes.c_ubyte * buf_len)()
            class _BIH(ctypes.Structure):
                _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                            ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                            ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                            ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                            ("biClrImportant", ctypes.c_ulong)]
            bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, buf_len, 0, 0, 0, 0)
            if gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bih), 0) == 0:
                return None, f"No pude leer '{title}'."
            img = Image.frombytes("RGBA", (w, h), bytes(buf), "raw", "BGRA")
        finally:
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
        return _to_b64(img), title or "ventana"
    except Exception as e:
        log.debug("capture_window: %s", e)
        return None, f"No pude capturar '{title or fragment}': {e}"


def _to_b64(img) -> str:
    return base64.b64encode(_jpeg_bytes(img)).decode("ascii")


def clear_capture() -> None:
    global _last_capture_b64
    with _capture_lock:
        _last_capture_b64 = None


class ScreenWatcher:
    """Modo continuo: captura cada N segundos en memoria y avisa al callback."""

    def __init__(self, interval: int = 5, on_capture: Optional[callable] = None):
        self.interval = interval
        self.on_capture = on_capture
        self._enabled = True
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._enabled = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="screen-watch")
        self._thread.start()

    def stop(self):
        self._enabled = False

    def set_interval(self, seconds: int):
        self.interval = max(2, seconds)

    def _run(self):
        while self._enabled:
            try:
                b64 = capture_recent(force=True)
                if b64 and self.on_capture:
                    self.on_capture(b64)
            except Exception as e:
                log.debug("captura continua falló: %s", e)
            time.sleep(self.interval)