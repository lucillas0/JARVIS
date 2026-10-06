"""Campus AVIESAU (Moodle del ciclo): login con sesión persistente en memoria.

Credenciales solo en .env (CAMPUS_USER/CAMPUS_PASSWORD), jamás en logs.
"""
import logging
import re
import threading

log = logging.getLogger("integrations.campus")

_session = None
_session_lock = threading.Lock()
_logged_user = ""


def base_url() -> str:
    try:
        from config import Config
        return (Config.CAMPUS_URL or "https://av.ciclosallerulloa.gal").rstrip("/")
    except Exception:
        return "https://av.ciclosallerulloa.gal"


def _client():
    global _session
    with _session_lock:
        if _session is None:
            try:
                import requests
                _session = requests.Session()
                _session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS/1.0",
                    "Accept-Language": "es-ES,es;q=0.9",
                })
            except Exception:
                _session = False
        return _session or None


def _creds():
    try:
        from config import Config
        return ((Config.CAMPUS_USER or "").strip(),
                (Config.CAMPUS_PASSWORD or "").strip())
    except Exception:
        return "", ""


def login(force: bool = False) -> bool:
    """Inicia sesión (o reutiliza la válida). Devuelve True si dentro."""
    global _logged_user
    s = _client()
    if s is None:
        return False
    user, pw = _creds()
    if not user or not pw:
        log.warning("Campus sin credenciales.")
        return False
    base = base_url()
    if not force and _logged_user == user:
        try:
            r = s.get(base + "/my/", timeout=15)
            if r.status_code == 200 and ("logout" in r.text.lower() or user.lower() in r.text.lower()):
                return True
        except Exception:
            pass
        _logged_user = ""
    try:
        r = s.get(base + "/login/index.php", timeout=20)
        html = r.text
        m = re.search(r'name="logintoken" value="([^"]+)"', html)
        token = m.group(1) if m else ""
        r = s.post(base + "/login/index.php", timeout=20, allow_redirects=True, data={
            "anchor": "", "logintoken": token,
            "username": user, "password": pw,
        })
        body = r.text or ""
        bad = ("error" in body[max(0, body.lower().find("login")):][:2000].lower()
               and ("incorrect" in body.lower() or "incorrecto" in body.lower()
                    or "error/invalidlogin" in body.lower()))
        if r.status_code == 200 and ("logout" in body.lower() or user.lower() in body.lower()) and not bad:
            _logged_user = user
            return True
        # Comprobación extra: el dashboard exige login.
        try:
            r2 = s.get(base + "/my/", timeout=15)
            if r2.status_code == 200 and ("logout" in r2.text.lower() or user.lower() in r2.text.lower()):
                _logged_user = user
                return True
        except Exception:
            pass
        log.warning("Campus: login fallido (¿credenciales?).")
        return False
    except Exception as e:
        log.debug("campus login: %s", e)
        return False


def get_page(path: str, timeout: int = 20) -> str:
    """GET autenticado (re-loguea una vez si expiró)."""
    s = _client()
    if s is None:
        raise RuntimeError("Sin cliente HTTP")
    base = base_url()
    if not path.startswith("/"):
        path = "/" + path
    r = s.get(base + path, timeout=timeout)
    if r.status_code == 200 and ('name="logintoken"' in r.text and "/login/index.php" in r.text[:5000]):
        if login(force=True):
            r = s.get(base + path, timeout=timeout)
        else:
            raise RuntimeError("Sesión caducada y no pude reentrar")
    if r.status_code != 200:
        raise RuntimeError(f"Campus HTTP {r.status_code}")
    return r.text


def _clean(s: str, limit: int = 140) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] if len(s) > limit else s


def _course_map(html: str) -> dict:
    return {m.group(1): _clean(m.group(2), 80)
            for m in re.finditer(r"<option value=\"(\d+)\">([^<]+)</option>", html)}


def upcoming_deadlines(limit: int = 8) -> list[dict]:
    """Próximos vencimientos: [{title, course, date, ts}]."""
    html = get_page("/calendar/view.php?view=upcoming")
    courses = _course_map(html)
    out = []
    for m in re.finditer(r"<div data-type=\"event\"[^>]*>", html):
        block_open = m.group(0)
        cid = (re.search(r"data-course-id=\"(\d+)\"", block_open) or [None, None])[1]
        mt = re.search(r"data-event-title=\"([^\"]+)\"", block_open)
        if not mt:
            continue
        seg = html[m.start():m.start() + 4000]
        md = re.search(r"<span\s+class=\"date\s*\"[^>]*data-timestamp=\"(\d+)\"[^>]*>([^<]+)</span>", seg)
        ts = int(md.group(1)) if md else 0
        date = _clean(md.group(2), 60) if md else ""
        out.append({"title": _clean(mt.group(1), 120),
                    "course": courses.get(cid or "", ""),
                    "date": date, "ts": ts})
    out.sort(key=lambda e: (e["ts"] or 10 ** 12))
    return out[:limit]


def grades_overview(limit: int = 12) -> list[dict]:
    """Notas por curso: [{course, grade}]."""
    html = get_page("/grade/report/overview/index.php")
    out = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        row = m.group(1)
        cells = [_clean(c, 80) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 2 and not re.search(r"curso|calificaci|grade", cells[0], re.I):
            grade = cells[1][:20] if cells[1] != "-" else "sin nota"
            out.append({"course": cells[0][:80], "grade": grade})
        if len(out) >= limit:
            break
    return out