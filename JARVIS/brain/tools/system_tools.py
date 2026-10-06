"""Herramientas de sistema operativo Windows (sección 5 de la especificación).

Prioridad: 1) abrir apps/archivos (búsqueda en todo el disco) 2) navegador
predeterminado 3) ventanas/escritorio 4) multimedia (Spotify y volumen).

Cada herramienta lleva su nivel de riesgo; las sensibles pasan por el doble
canal del Risk Engine.
"""
import ctypes
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger("tools.system")

# Evitar llamadas a ctypes para cosas que no sean estrictamente necesarias.
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
APPCMDS = {"shell:appsFolder"}


# ----------------------------------------------------------------------------- #
# Helper base: abrir con la app asociada
# ----------------------------------------------------------------------------- #
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _launch_native(target: str) -> subprocess.CompletedProcess:
    """Abre target (app/archivo/URL) SIN abrir consolas y SIN bloquear.
    Usa os.startfile (API nativa de Windows) y, si no resuelve,
    cmd /c start con CREATE_NO_WINDOW y pipes DEVNULL (fire-and-forget).
    OJO: NO usar subprocess.run(capture_output=True) con 'cmd /c start':
    la app GUI hereda los pipes de cmd y run() se queda esperando a que cierre."""
    t = os.path.abspath(target) if isinstance(target, str) and os.path.exists(target) else target
    name_only = isinstance(target, str) and not os.path.exists(target) and not _is_url(target)

    # Preferir ruta absoluta del ejecutable (evita cmd y sus pipes).
    if name_only:
        resolved = _command_path(target)
        if resolved:
            resolved = os.path.abspath(resolved)
            os.startfile(resolved)
            return subprocess.CompletedProcess(["startfile", resolved], 0)

    try:
        if os.path.exists(t):
            os.startfile(t)
            return subprocess.CompletedProcess(["startfile", t], 0)
    except Exception:
        pass

    # Fire-and-forget: no esperamos, no capturamos nada.
    try:
        p = subprocess.Popen(
            ["cmd", "/c", "start", "", t],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        return subprocess.CompletedProcess(["cmd", "/c", "start", t], 0)
    except Exception as e:
        return subprocess.CompletedProcess(["cmd", "/c", "start", t], 1, stderr=str(e).encode())


def _is_url(s: str) -> bool:
    return re.match(r"^[a-z][a-z0-9+.-]*://", s.strip(), re.I) is not None


def _command_path(cmd: str) -> str | None:
    """Devuelve la ruta completa del ejecutable vía where.exe, o None."""
    try:
        r = subprocess.run(
            ["where.exe", cmd], capture_output=True, text=True, timeout=3,
            creationflags=_NO_WINDOW,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def _os_start(path: str) -> subprocess.CompletedProcess:
    return _launch_native(path)


# ----------------------------------------------------------------------------- #
# 1) Abrir apps y archivos con búsqueda en todo el disco
# ----------------------------------------------------------------------------- #
def _resolve_open_target(name: str) -> str:
    """Resuelve un nombre a: .exe en PATH, .lnk en menú inicio/escritorio,
    archivo por nombre en disco, o el comando tal cual."""
    name = name.strip().strip('"').strip("'")
    lower = name.lower()

    # 1. Si es una ruta existente literal
    if os.path.exists(name):
        return os.path.abspath(name)

    # 2. Comando directo (npm, calc, etc.)
    if _command_exists(name) or _command_exists(f"{name}.exe"):
        return name

    # 3. Buscar .lnk/.exe en Start Menu y escritorio
    paths = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
    ]
    for base in paths:
        if not base.exists():
            continue
        candidates = list(base.rglob(f"*{name}*.lnk")) + list(base.rglob(f"*{name}*.exe"))
        if candidates:
            return str(candidates[0])

    # 4. Búsqueda en todo el disco por nombre de archivo (rápida sobre directorios clave)
    for drive in _fixed_drives():
        hits = _search_disk(drive, name)
        if hits:
            return hits[0]

    return name


def _command_exists(cmd: str) -> bool:
    try:
        r = subprocess.run(["where.exe", cmd], capture_output=True, timeout=3,
                           creationflags=_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def _fixed_drives() -> list[str]:
    drives = []
    try:
        from ctypes import wintypes
        bitmask = kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask >> i & 1:
                letter = chr(65 + i)
                if kernel32.GetDriveTypeW(f"{letter}:\\") != 3:  # DRIVE_FIXED
                    continue
                drives.append(f"{letter}:\\")
    except Exception:
        drives = ["C:\\"]
    return drives


def _search_disk(root: str, name: str) -> list[str]:
    """Búsqueda acotada: examina carpetas de usuarios y programas para no tardar
    una eternidad. Es mejor que la búsqueda de Windows en la mayoría de casos."""
    results: list[str] = []
    start = time.time()
    skip = {"$Recycle.Bin", "System Volume Information", "Windows", "node_modules",
            "AppData", "ProgramData", "$WinREAgent", "Recovery", "Temp"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith("$")]
        for f in filenames:
            if name.lower() in f.lower():
                results.append(os.path.join(dirpath, f))
        if results:
            break
        if time.time() - start > 15:  # máx 15s por unidad
            break
    return results[:3]


def open_app_handler(params: dict) -> dict:
    app = params.get("app_name") or params.get("name")
    if not app:
        raise ValueError("Falta el nombre de la app")
    target = _resolve_open_target(app)
    r = _launch_native(target)
    return {"opened": app, "target": target, "rc": r.returncode}


def open_file_handler(params: dict) -> dict:
    path = params.get("path") or params.get("file")
    if not path:
        raise ValueError("Falta la ruta del archivo")
    path = os.path.expandvars(path)
    if not os.path.exists(path):
        hit = _search_disk(os.path.splitdrive(path)[0] + "\\" if os.path.splitdrive(path)[0] else "C:\\",
                           os.path.basename(path))
        if not hit:
            raise FileNotFoundError(f"No encuentro el archivo: {path}")
        path = hit[0]
    r = _os_start(path)
    return {"opened": path, "rc": r.returncode}


# ----------------------------------------------------------------------------- #
# 2) Navegador predeterminado del sistema
# ----------------------------------------------------------------------------- #
def _default_browser() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        m = re.search(r'"(.*?\.exe)"', cmd)
        return m.group(1) if m else cmd.strip('"')
    except Exception as e:
        log.warning("No pude detectar navegador predeterminado: %s", e)
        return "http"


def open_browser_handler(params: dict) -> dict:
    url = params.get("url", "https://www.google.com")
    if not url.startswith("http"):
        url = "https://" + url
    _launch_native(url)
    return {"opened": url}


def web_search_handler(params: dict) -> dict:
    query = params.get("query", "")
    engine = params.get("engine", "google")
    urls = {"google": "https://www.google.com/search?q=",
            "bing": "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q="}
    base = urls.get(engine, urls["google"])
    from urllib.parse import quote
    url = base + quote(query)
    _launch_native(url)
    return {"opened": url}


# ----------------------------------------------------------------------------- #
# 3) Gestión de ventanas y escritorio
# ----------------------------------------------------------------------------- #
def _find_window(title_fragment: str):
    """Devuelve handle de la primera ventana visible cuyo título contenga el fragmento."""
    enum = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                enum.append((hwnd, buf.value))
        return True

    user32.EnumWindows(_cb, 0)
    frag = title_fragment.lower()
    for hwnd, title in enum:
        if frag in title.lower():
            return hwnd
    if enum:
        return enum[0][0]  # en su defecto la primera
    return None


def _show_window(hwnd, flag: int) -> dict:
    user32.ShowWindow(hwnd, flag)
    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    return {"hwnd": hwnd}


def minimize_window_handler(params: dict) -> dict:
    hwnd = _find_window(params.get("title", ""))
    if not hwnd:
        raise RuntimeError("No hay ninguna ventana que minimizar")
    return _show_window(hwnd, 6)  # SW_MINIMIZE


def maximize_window_handler(params: dict) -> dict:
    hwnd = _find_window(params.get("title", ""))
    if not hwnd:
        raise RuntimeError("No hay ninguna ventana que maximizar")
    return _show_window(hwnd, 3)  # SW_MAXIMIZE


def focus_window_handler(params: dict) -> dict:
    hwnd = _find_window(params.get("title", ""))
    if not hwnd:
        raise RuntimeError(f"No encuentro la ventana '{params.get('title', '')}'")
    return _show_window(hwnd, 9)  # SW_RESTORE + enfocar


def switch_window_handler(params: dict) -> dict:
    """Trae a primer plano la ventana indicada (cambio de ventana por voz)."""
    title = params.get("title", "")
    if not title:
        raise RuntimeError("Dime a qué ventana cambiar")
    hwnd = _find_window(title)
    if not hwnd:
        raise RuntimeError(f"No encuentro la ventana de {title}")
    return _show_window(hwnd, 5)


# ----------------------------------------------------------------------------- #
# 4) Multimedia: Spotify y volumen
# ----------------------------------------------------------------------------- #
def spotify_play_handler(params: dict) -> dict:
    if _spotify_running():
        _send_media_key(0xB3)  # VK_MEDIA_PLAY_PAUSE
    else:
        _launch_native("spotify")
        time.sleep(3)
        _send_media_key(0xB3)
    return {"spotify": "play/pause"}


def spotify_pause_handler(params: dict) -> dict:
    """Pausa la reproducción. Usa la API oficial (spotipy) si está configurada;
    si no, envía la media key (toggle, fiable en la práctica para 'pausa')."""
    try:
        from integrations import spotify
        if spotify.available():
            spotify.pause()
            return {"spotify": "pause", "via": "api"}
    except Exception:
        pass
    if _spotify_running():
        _send_media_key(0xB3)
    return {"spotify": "pause", "via": "media_key"}


def spotify_next_handler(params: dict) -> dict:
    _send_media_key(0xB0)  # VK_MEDIA_NEXT_TRACK
    return {"spotify": "next"}


def spotify_prev_handler(params: dict) -> dict:
    _send_media_key(0xB1)  # VK_MEDIA_PREV_TRACK
    return {"spotify": "prev"}


def spotify_search_handler(params: dict) -> dict:
    """Busca canciones en Spotify (API)."""
    from integrations import spotify
    q = (params.get("query", "") or "").strip()
    if not q:
        raise ValueError("Falta la búsqueda.")
    return {"results": spotify.search_tracks(q)}


def spotify_playlists_handler(params: dict) -> dict:
    """Tus playlists de Spotify (API)."""
    from integrations import spotify
    return {"playlists": spotify.get_playlists()}


def spotify_play_name_handler(params: dict) -> dict:
    """Reproduce una playlist por nombre (API) o una canción (búsqueda+URI)."""
    from integrations import spotify
    name = (params.get("name", "") or "").strip()
    kind = (params.get("kind", "") or "playlist").strip().lower()
    if not name:
        raise ValueError("Falta el nombre.")
    if kind.startswith("play"):
        pls = spotify.get_playlists(limit=20)
        hit = next((p for p in pls if name.lower() in p["name"].lower()), None)
        if not hit:
            raise ValueError(f"No veo la playlist '{name}'.")
        spotify.play_context(hit["id"])
        return {"playing": "playlist", "name": hit["name"]}
    res = spotify.search_tracks(name, limit=1)
    if not res:
        raise ValueError(f"Nada para '{name}'.")
    spotify.play_track(res[0]["uri"])
    return {"playing": "track", "name": res[0].get("title", name)}


def volume_set_handler(params: dict) -> dict:
    level = int(params.get("level", 50))
    _set_volume(max(0, min(100, level)))
    return {"volume": level}


def _spotify_running() -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=_NO_WINDOW)
        return "Spotify.exe" in r.stdout
    except Exception:
        return False


def _send_media_key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def _set_volume(level: int) -> None:
    # Control de volumen maestro con API de Windows (mixer del endpoint de audio).
    try:
        from ctypes import POINTER, cast, wintypes
        from ctypes.wintypes import DWORD
        # CoCreateInstance IAudioEndpointVolume
        import comtypes
        from comtypes import GUID, CoCreateInstance, CLSCTX_ALL
        from comtypes.automation import IDispatch
        IID_IDeviceEnumerator = GUID("{a95664d2-9614-4f35-a746-de8db63617e6}")
        CLSID_MMDeviceEnumerator = GUID("{bcde0395-e52f-467c-8e3d-c4579291692e}")
        # Usamos pyaudiowpatch si está, sino comtypes genérico.
        try:
            from pycaw.pycaw import AudioUtilities
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                try:
                    vol = s.SimpleAudioVolume
                    vol.SetMasterVolume(level / 100.0, None)
                except Exception:
                    pass
            return
        except Exception:
            pass
    except Exception as e:
        log.warning("Control de volumen WM falló (%s); uso nircmd si existe", e)
    # Último recurso: atajo de teclado (impreciso) — evitamos, mejor nircmd/soundvolumecommand
    raise RuntimeError("No pude ajustar el volumen del sistema en este equipo.")


def mute_notifications_handler(params: dict) -> dict:
    """Modo no molestar (reuniones): desactiva toasts vía registro y, si no, baja volumen."""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Notifications"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0, winreg.REG_DWORD, 0)
        return {"notifications": "off", "method": "registry"}
    except Exception as e:
        log.info("mute_notifications: registry no disponible (%s); bajo volumen", e)
        try:
            _set_volume(20)
        except Exception:
            pass
        return {"notifications": "off", "method": "volume_fallback"}


# ----------------------------------------------------------------------------- #
# 8) Control total por voz: navegador concreto, escribir y teclas (ctypes puro)
# ----------------------------------------------------------------------------- #
def _browser_exe(name: str) -> str | None:
    n = (name or "").strip().lower()
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LOCALAPPDATA", "")
    cands = {
        "brave": [os.path.join(pf, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                  os.path.join(lad, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")],
        "chrome": [os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
                   os.path.join(pfx, "Google", "Chrome", "Application", "chrome.exe"),
                   os.path.join(lad, "Google", "Chrome", "Application", "chrome.exe")],
        "edge": [os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
                 os.path.join(pfx, "Microsoft", "Edge", "Application", "msedge.exe")],
        "firefox": [os.path.join(pf, "Mozilla Firefox", "firefox.exe"),
                    os.path.join(pfx, "Mozilla Firefox", "firefox.exe")],
    }.get(n, [])
    for p in cands:
        if p and os.path.exists(p):
            return p
    hit = _command_path(n + ".exe") if n else None
    return hit


def open_url_handler(params: dict) -> dict:
    """Abre una URL en el navegador indicado (o el predeterminado)."""
    url = (params.get("url") or "").strip()
    if not url:
        raise ValueError("Falta la URL")
    if not _is_url(url):
        url = "https://" + url.lstrip("/")
    browser = (params.get("browser") or "default").strip().lower()
    if browser in ("default", "predeterminado", "sistema", ""):
        _launch_native(url)
        return {"opened": url, "browser": "predeterminado"}
    exe = _browser_exe(browser)
    if not exe:
        raise RuntimeError(f"No encuentro el navegador '{browser}' instalado")
    subprocess.Popen([exe, url], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=_NO_WINDOW, close_fds=True)
    try:
        _show_window(_find_window(os.path.basename(exe).replace(".exe", "")), 9)
    except Exception:
        pass
    return {"opened": url, "browser": browser}


# --- Teclado vía SendInput (sin dependencias) ---
_KEYEVENTF_KEYUP = 0x0002
_VK_MODS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
            "control": 0x11, "windows": 0x5B}
_VK_NAMED = {"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
             "space": 0x20, "espacio": 0x20, "backspace": 0x08, "delete": 0x2E,
             "supr": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25,
             "right": 0x27, "home": 0x24, "end": 0x23, "insert": 0x2D,
             "pageup": 0x21, "pagedown": 0x22}
for _i in range(1, 13):
    _VK_NAMED[f"f{_i}"] = 0x6F + _i


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ki", _KEYBDINPUT)]


def _key_event(vk: int, up: bool = False) -> None:
    ki = _KEYBDINPUT(wVk=vk, wScan=0,
                     dwFlags=_KEYEVENTF_KEYUP if up else 0,
                     time=0, dwExtraInfo=None)
    # Puntero nulo válido para dwExtraInfo (ULONG_PTR 0).
    ki.dwExtraInfo = ctypes.POINTER(ctypes.c_ulong)()
    inp = _INPUT(type=1, ki=ki)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _tap_key(vk: int) -> None:
    _key_event(vk, False)
    time.sleep(0.02)
    _key_event(vk, True)
    time.sleep(0.02)


def translate_keys(spec: str) -> list[tuple[str, int]]:
    """'ctrl+t' → [('down', VK_CONTROL), ('tap', ord T)]. Solo lectura."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("Teclas vacías")
    *mods, main = parts
    seq = []
    for m in mods:
        if m not in _VK_MODS:
            raise ValueError(f"Modificador desconocido: {m}")
        seq.append(("down", _VK_MODS[m]))
    vk = _VK_NAMED.get(main)
    if vk is None:
        if len(main) == 1 and (main.isascii() and (main.isalnum())):
            vk = ord(main.upper())
        else:
            raise ValueError(f"Tecla desconocida: {main}")
    seq.append(("tap", vk))
    for m in reversed(mods):
        seq.append(("up", _VK_MODS[m]))
    return seq


def press_keys_handler(params: dict) -> dict:
    """Pulsa teclas/atajos: 'enter', 'ctrl+t', 'alt+tab', 'ctrl+l', 'f5'..."""
    seq = translate_keys(params.get("keys", ""))
    window = (params.get("window") or "").strip()
    if window:
        hwnd = _find_window(window)
        if not hwnd:
            raise RuntimeError(f"No encuentro la ventana '{window}'")
        _show_window(hwnd, 9)
        time.sleep(0.4)
    for action, vk in seq:
        if action == "down":
            _key_event(vk, False)
        elif action == "up":
            _key_event(vk, True)
        else:
            _tap_key(vk)
    return {"pressed": params.get("keys", ""), "window": window or "activa"}


# --- Portapapeles vía ctypes (pegar Unicode: acentos, código, emojis) ---
_CF_UNICODETEXT = 13

_VOID_P = ctypes.c_void_p
kernel32.GlobalAlloc.restype = _VOID_P
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = _VOID_P
kernel32.GlobalLock.argtypes = [_VOID_P]
kernel32.GlobalUnlock.argtypes = [_VOID_P]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [_VOID_P]
kernel32.GlobalFree.argtypes = [_VOID_P]
user32.OpenClipboard.argtypes = [_VOID_P]
user32.GetClipboardData.restype = _VOID_P
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.SetClipboardData.restype = _VOID_P
user32.SetClipboardData.argtypes = [ctypes.c_uint, _VOID_P]


def _clipboard_get() -> str | None:
    try:
        if not user32.OpenClipboard(None):
            return None
        try:
            h = user32.GetClipboardData(_CF_UNICODETEXT)
            if not h:
                return None
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return None
            try:
                size = kernel32.GlobalSize(h)
                raw = ctypes.string_at(ptr, size)
                return raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    data = (text + "\x00").encode("utf-16-le")
    for _ in range(8):
        try:
            if user32.OpenClipboard(None):
                try:
                    user32.EmptyClipboard()
                    h = kernel32.GlobalAlloc(0x0002, len(data))
                    if not h:
                        raise RuntimeError("Sin memoria para el portapapeles")
                    ptr = kernel32.GlobalLock(h)
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h)
                    if not user32.SetClipboardData(_CF_UNICODETEXT, h):
                        kernel32.GlobalFree(h)
                        raise RuntimeError("SetClipboardData falló")
                    return
                finally:
                    user32.CloseClipboard()
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("Portapapeles ocupado")


def type_text_handler(params: dict) -> dict:
    """Escribe texto (pegar Unicode) en la ventana activa o la indicada."""
    text = params.get("text", "")
    if not text:
        raise ValueError("Falta el texto")
    text = str(text)[:20000]
    window = (params.get("window") or "").strip()
    if window:
        hwnd = _find_window(window)
        if not hwnd:
            raise RuntimeError(f"No encuentro la ventana '{window}'")
        _show_window(hwnd, 9)
        time.sleep(0.5)
    saved = _clipboard_get()
    try:
        _clipboard_set(text)
        time.sleep(0.15)
        # Ctrl+V manual: down ctrl, tap V, up ctrl
        _key_event(_VK_MODS["ctrl"], False)
        _tap_key(ord("V"))
        _key_event(_VK_MODS["ctrl"], True)
        time.sleep(0.3)
    finally:
        if saved is not None:
            try:
                _clipboard_set(saved)
            except Exception:
                pass
    return {"typed_chars": len(text), "window": window or "activa"}


# ----------------------------------------------------------------------------- #
# Registro de herramientas
# ----------------------------------------------------------------------------- #
TOOLS = [
    {"name": "open_app",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"app_name": "str"},
     "description": "Abre una app o comando de Windows (búsqueda en todo el disco).",
     "label": "Abriendo aplicación",
     "handler": open_app_handler},
    {"name": "open_file",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str"},
     "description": "Abre un archivo con su aplicación asociada (con búsqueda por ruta).",
     "label": "Abriendo archivo",
     "handler": open_file_handler},
    {"name": "open_browser",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"url": "str"},
     "description": "Abre una URL en el navegador predeterminado del sistema.",
     "label": "Abriendo navegador",
     "handler": open_browser_handler},
    {"name": "web_search",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"query": "str"},
      "description": "Abre una búsqueda en el navegador (SOLO visual, no devuelve contenido). Si piden buscar, analizar o resumir, usa web_search_results + web_fetch.",
     "label": "Buscando en la web",
     "handler": web_search_handler},
    {"name": "spotify_play",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Reproduce o pausa Spotify.",
     "label": "Controlando Spotify",
     "handler": spotify_play_handler},
    {"name": "spotify_pause",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Pausa la reproducción de Spotify.",
     "label": "Pausando Spotify",
     "handler": spotify_pause_handler},
    {"name": "spotify_next",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Siguiente canción en Spotify.",
     "label": "Siguiente canción",
     "handler": spotify_next_handler},
    {"name": "spotify_prev",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Canción anterior en Spotify.",
     "label": "Canción anterior",
     "handler": spotify_prev_handler},
    {"name": "spotify_search",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"query": "str"},
     "description": "Busca canciones en Spotify (API).",
     "label": "Buscando música",
     "handler": spotify_search_handler},
    {"name": "spotify_playlists",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Tus playlists de Spotify (API).",
     "label": "Listando playlists",
     "handler": spotify_playlists_handler},
    {"name": "spotify_play_name",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"name": "str", "kind": "str: playlist|cancion (defecto playlist)"},
     "description": "Reproduce una playlist por nombre o una canción (API).",
     "label": "Poniendo música",
     "handler": spotify_play_name_handler},
]

WINDOW_TOOLS = [
    {"name": "minimize_window",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"title": "str"},
     "description": "Minimiza una ventana por su título.",
     "label": "Minimizando ventana",
     "handler": minimize_window_handler},
    {"name": "maximize_window",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"title": "str"},
     "description": "Maximiza una ventana por su título.",
     "label": "Maximizando ventana",
     "handler": maximize_window_handler},
    {"name": "switch_window",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"title": "str"},
     "description": "Trae a primer plano una ventana (cambio de ventana).",
     "label": "Cambiando de ventana",
     "handler": switch_window_handler},
]

MEDIA_TOOLS = [
    {"name": "volume_set",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"level": "int (0-100)"},
     "description": "Sube o baja el volumen del sistema.",
     "label": "Ajustando volumen",
     "handler": volume_set_handler},
    {"name": "mute_notifications",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Activa 'No molestar' para silenciar notificaciones (reuniones).",
     "label": "Silenciando notificaciones",
     "handler": mute_notifications_handler},
]

NAV_TOOLS = [
    {"name": "open_url",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"url": "str", "browser": "str: default|brave|chrome|edge|firefox"},
     "description": "Abre una URL en el navegador indicado (brave, chrome, edge, firefox o default).",
     "label": "Abriendo URL",
     "handler": open_url_handler},
    {"name": "type_text",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"text": "str", "window": "str opcional con parte del título"},
     "description": "Escribe texto (con tildes y código) en la ventana activa o en la indicada.",
     "label": "Escribiendo texto",
     "handler": type_text_handler},
    {"name": "press_keys",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"keys": "str: enter|tab|escape|ctrl+t|alt+tab|ctrl+l|f5…", "window": "str opcional"},
     "description": "Pulsa teclas o atajos (enter, tab, ctrl+t, ctrl+l, f5…) en la ventana activa o indicada.",
     "label": "Pulsando teclas",
     "handler": press_keys_handler},
]

# Herramientas sensibles: borrar archivos requiere doble confirmación.
SENSITIVE_TOOLS = [
    {"name": "delete_file",
     "risk_level": "sensitive",
     "requires_confirmation": True,
     "parameters": {"path": "str"},
     "description": "Borra PERMANENTEMENTE un archivo. Requiere doble confirmación.",
     "label": "Eliminando archivo",
     "confirm_text": "Voy a eliminar '{path}'. ¿Confirma? Esto es irreversible.",
     "handler": lambda params: (_delete_file(params), {"deleted": params["path"]})[1]},
]


def _delete_file(params: dict) -> dict:
    path = os.path.expandvars(params.get("path", ""))
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"No existe el archivo: {path}")
    os.remove(path)
    return {"deleted": path}


def danger_tools() -> list[dict]:
    """Herramientas sensibles registrables (p. ej. borrar archivos)."""
    return SENSITIVE_TOOLS