"""Campus AVIESAU (Moodle del ciclo): entregas y notas."""
import logging
import time

log = logging.getLogger("tools.campus")


def campus_deadlines_handler(params: dict) -> dict:
    from integrations import campus
    if not campus.login():
        raise RuntimeError("No pude entrar al campus (¿credenciales?).")
    items = campus.upcoming_deadlines(limit=8)
    return {"deadlines": items}


def campus_grades_handler(params: dict) -> dict:
    from integrations import campus
    if not campus.login():
        raise RuntimeError("No pude entrar al campus (¿credenciales?).")
    items = campus.grades_overview(limit=12)
    return {"grades": items}


def _campus_base() -> str:
    try:
        from config import Config
        return (Config.CAMPUS_URL or "https://av.ciclosallerulloa.gal").rstrip("/")
    except Exception:
        return "https://av.ciclosallerulloa.gal"


def _campus_creds():
    try:
        from config import Config
        u, p = (Config.CAMPUS_USER or "").strip(), (Config.CAMPUS_PASSWORD or "").strip()
    except Exception:
        u, p = "", ""
    if not u or not p:
        raise RuntimeError("Sin credenciales del campus (CAMPUS_USER/PASSWORD)")
    return u, p


def campus_open_handler(params: dict) -> dict:
    """Abre el campus en el navegador y mete el login por UI.

    Ya logueado -> solo abre. Devuelve {url, logged_in}.

    Con {"login": False} solo abre la página y devuelve enseguida: la
    automatización de ventanas tarda seis segundos y el señor lo único que
    quiere es ver la página. El login se procura aparte, en segundo plano.
    """
    import webbrowser
    base = _campus_base()
    solo_abrir = not params.get("login", True)
    url = (base + "/my/courses.php" if solo_abrir
           else base + "/login/index.php")
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        raise RuntimeError(f"No pude abrir el navegador: {e}")
    if solo_abrir:
        return {"url": url, "logged_in": False,
                "note": "Entro en su sesión."}
    try:
        from pywinauto import Desktop
    except Exception:
        return {"url": url, "logged_in": False,
                "note": "Abierto; mete el login a mano (sin UI automation aquí)."}
    wins = []
    try:
        for w in Desktop(backend="uia").windows():
            try:
                t = w.window_text() or ""
                if "aviesau" in t.lower() or "entrar al sitio" in t.lower():
                    wins.append(w)
            except Exception:
                pass
    except Exception as e:
        return {"url": url, "logged_in": False, "note": f"Abierto; no veo la ventana: {e}"}
    if not wins:
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            raise RuntimeError(f"No pude abrir el navegador: {e}")
        time.sleep(8)
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    t = w.window_text() or ""
                    if "aviesau" in t.lower() or "entrar al sitio" in t.lower():
                        wins.append(w)
                except Exception:
                    pass
        except Exception as e:
            return {"url": url, "logged_in": False, "note": f"Abierto; no veo la ventana: {e}"}
    if not wins:
        return {"url": url, "logged_in": False,
                "note": "Abierto pero sin ventana a la vista; termina el login a mano."}
    w = wins[0]
    try:
        w.set_focus()
    except Exception:
        pass
    time.sleep(1)
    # Navegación determinista en la pestaña ACTIVA (nada de pestañas viejas
    # con tokens caducados): omnibox → URL → Enter → esperar login o dentro.
    from tools.system_tools import _clipboard_set

    def _find_omni(edits):
        for e in edits:  # 1º por automation-id típico de Chromium
            try:
                if "view_" in str(getattr(e.element_info, "automation_id", "")):
                    return e
            except Exception:
                pass
        best, best_top = None, None  # 2º el Edit más alto (siempre arriba)
        for e in edits:
            try:
                top = e.rectangle().top
            except Exception:
                continue
            if best_top is None or top < best_top:
                best, best_top = e, top
        return best

    try:
        edits0 = w.descendants(control_type="Edit")
    except Exception:
        edits0 = []
    omni = _find_omni(edits0)
    if omni is None:
        import webbrowser
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            raise RuntimeError(f"No pude abrir el navegador: {e}")
        time.sleep(6)
        try:
            edits0 = w.descendants(control_type="Edit")
        except Exception:
            edits0 = []
        for e in edits0:
            try:
                v = e.get_value() or ""
            except Exception:
                v = ""
            if "http" in v.lower():
                omni = e
                break
    if omni is None:
        return {"url": url, "logged_in": False,
                "note": "Abierto pero sin barra de direcciones; termina a mano."}
    try:
        omni.set_focus()
        time.sleep(0.4)
        _clipboard_set(url)
        time.sleep(0.2)
        omni.type_keys("^a^v{ENTER}")
    except Exception as e:
        raise RuntimeError(f"No pude navegar: {e}")
    for _ in range(8):
        time.sleep(2)
        try:
            title = (w.window_text() or "").lower()
        except Exception:
            continue
        if title and not any(k in title for k in ("entrar", "login", "log in", "sign in")):
            return {"url": url, "logged_in": True,
                    "note": "Ya estaba dentro (sesión guardada)."}
        if "entrar" in title or "login" in title:
            break
    time.sleep(2)
    try:
        edits = [e for e in w.descendants(control_type="Edit")]
    except Exception:
        edits = []
    if len(edits) < 2:
        try:
            title = (w.window_text() or "").lower()
        except Exception:
            title = ""
        if title and not any(k in title for k in ("entrar", "login", "log in", "sign in")):
            return {"url": url, "logged_in": True,
                    "note": "Ya estaba dentro (sesión guardada)."}
        return {"url": url, "logged_in": False,
                "note": "Veo la página pero no el formulario (¿ya dentro?)."}
    user, pw = _campus_creds()

    def _by_autoid(edits, *ids):
        for e in edits:
            try:
                if getattr(e.element_info, "automation_id", "") in ids:
                    return e
            except Exception:
                pass
        return None

    def _paste_into(edit, text):
        # set_edit_text no necesita foco (a prueba de usuario activo).
        # El pegado por portapapeles queda como respaldo.
        try:
            edit.set_edit_text(text)
            time.sleep(0.5)
            return
        except Exception:
            pass
        from tools.system_tools import _clipboard_set
        try:
            edit.set_focus()
        except Exception:
            pass
        time.sleep(0.4)
        _clipboard_set(text)
        time.sleep(0.2)
        edit.type_keys("^a^v")
        time.sleep(0.4)

    def _read_back(edit):
        try:
            return edit.get_value() or edit.window_text() or ""
        except Exception:
            return ""

    # Asentamiento: el título aparece antes que el DOM usable. Espera al
    # campo username habilitado (hasta ~12s), re-enumerando.
    for _settle in range(6):
        try:
            edits = [e for e in w.descendants(control_type="Edit")]
        except Exception:
            edits = []
        _ue = _by_autoid(edits, "username", "user", "login_username")
        if _ue is not None:
            try:
                if _ue.is_enabled():
                    break
            except Exception:
                break
        time.sleep(2)

    try:
        # 1º por automation-id (infalible): username/password.
        user_edit = _by_autoid(edits, "username", "user", "login_username")
        pw_edit = _by_autoid(edits, "password", "pass", "passwd",
                             "login_password", "current-password")
        if user_edit is None or pw_edit is None:
            # Sin la omnibox (por identidad, no por su contenido).
            omni_el = _find_omni(edits)
            cands = [e for e in edits if e is not omni_el]
            if len(cands) < 2:
                # Fallback: los dos últimos (suele ser usuario+clave).
                cands = edits[-2:]
            user_edit, pw_edit = cands[0], cands[1]
        last_err = ""
        for _attempt in range(3):
            try:
                _paste_into(user_edit, user)
                time.sleep(0.5)
                if _read_back(user_edit).strip() != user:
                    last_err = "el usuario no quedó escrito"
                    time.sleep(1)
                    continue
                _paste_into(pw_edit, pw)
                time.sleep(0.5)
                last_err = ""
                break
            except Exception as e:
                last_err = str(e)[:100]
                time.sleep(1)
        if last_err:
            raise RuntimeError(last_err)
        submitted = False
        try:
            pw_edit.type_keys("{ENTER}")
            submitted = True
        except Exception:
            pass
        if not submitted:
            # Clic en el botón Acceder/Entrar (lista completa, no solo chrome).
            try:
                btns = w.descendants(control_type="Button")
            except Exception:
                btns = []
            for b in btns:
                try:
                    nm = (b.window_text() or "").lower()
                except Exception:
                    continue
                if any(k in nm for k in ("acced", "entr", "log in", "iniciar sesi")):
                    try:
                        b.click_input()
                    except Exception:
                        b.click()
                    submitted = True
                    break
        if not submitted:
            raise RuntimeError("sin vía de envío")
    except Exception as e:
        raise RuntimeError(f"No pude escribir el login: {e}")
    logged = False
    for _ in range(8):
        time.sleep(2)
        try:
            title = w.window_text() or ""
            if title and not any(k in title.lower() for k in ("entrar", "login", "log in", "sign in")):
                logged = True
                break
        except Exception:
            pass
    return {"url": url, "logged_in": logged}


def campus_courses_handler(params: dict) -> dict:
    """Índice completo de "Mis cursos" (tareas, notas, avisos, secciones)."""
    from integrations import campus_index as ix
    return ix.resumen()


def campus_search_handler(params: dict) -> dict:
    """Busca una palabra en todo el campus: cursos, tareas, notas, avisos."""
    from integrations import campus_index as ix
    q = (params.get("query") or params.get("q") or "").strip()
    if not q:
        return {"error": "Falta la palabra a buscar."}
    return {"query": q, "resultados": ix.buscar(q, limite=12)}


def campus_course_handler(params: dict) -> dict:
    """Todo lo indexado de un curso concreto."""
    from integrations import campus_index as ix
    nombre = (params.get("course") or params.get("nombre") or "").strip()
    if not nombre:
        return {"error": "Falta el nombre del curso."}
    return {"curso": nombre, "detalle": ix.detalle(nombre)}


def campus_refresh_handler(params: dict) -> dict:
    """Vuelve a leer el Moodle (tareas nuevas, notas nuevas)."""
    from integrations import campus_index as ix
    ix.construir(forzar=True)
    return {"ok": True, "detalle": ix.resumen()}


def campus_notas_handler(params: dict) -> dict:
    """Los apuntes de un tema: lee el contenido de los cursos y devuelve el pasaje."""
    from integrations import campus_apuntes as ap
    q = (params.get("topic") or params.get("query") or params.get("tema")
         or "").strip()
    if not q:
        e = ap.estado()
        return {"error": "Falta el tema.", "indexado": e}
    hallados = ap.buscar(q, limite=6)
    return {"tema": q, "encontrado": len(hallados),
            "resultados": [{"curso": h["curso"], "actividad": h["actividad"],
                            "url": h["url"], "texto": h["pasaje"]}
                           for h in hallados]}


def campus_indexar_handler(params: dict) -> dict:
    """Vuelve a leer los apuntes de los cursos (hay tareas nuevas)."""
    from integrations import campus_apuntes as ap
    ap.construir(forzar=True)
    return {"ok": True, "estado": ap.estado()}


CAMPUS_TOOLS = [
    {"name": "campus_deadlines",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Próximas entregas y vencimientos del campus AVIESAU (tareas con fecha).",
     "label": "Mirando entregas del campus",
     "handler": campus_deadlines_handler},
    {"name": "campus_grades",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Notas por asignatura del campus AVIESAU.",
     "label": "Mirando notas del campus",
     "handler": campus_grades_handler},
    {"name": "campus_open",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Abre o desbloquea la página del ciclo (campus AVIESAU) en el navegador y mete el login (usuario y contraseña ya configurados).",
     "label": "Abriendo el campus",
     "handler": campus_open_handler},
    {"name": "campus_courses",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Lista sus cursos del Moodle con sus actividades, notas y avisos. Úsala para saber qué materias tiene y qué hay en cada una.",
     "label": "Abriendo el índice de cursos",
     "handler": campus_courses_handler},
    {"name": "campus_search",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"query": "str: palabra o frase a buscar en el campus"},
     "description": "Busca una palabra en todo el campus: cursos, actividades, notas y avisos. Útil para encontrar una tarea concreta.",
     "label": "Buscando en el campus",
     "handler": campus_search_handler},
    {"name": "campus_course",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"course": "str: nombre (o parte) del curso"},
     "description": "Todo lo indexado de un curso: secciones, actividades, notas y avisos.",
     "label": "Leyendo un curso",
     "handler": campus_course_handler},
    {"name": "campus_refresh",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Vuelve a leer el Moodle para recoger tareas o notas nuevas. Úsala si el señor acaba de subir algo.",
     "label": "Actualizando el campus",
     "handler": campus_refresh_handler},
    {"name": "campus_notas",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"topic": "str: el tema que se busca, p.ej. 'configuracion dhcp'"},
     "description": "Busca en el contenido de los apuntes de sus cursos y devuelve el pasaje literal, con su curso y su dirección. Úsala cuando pregunte por un tema concreto de una asignatura.",
     "label": "Buscando en los apuntes",
     "handler": campus_notas_handler},
    {"name": "campus_indexar",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Vuelve a leer el contenido de los cursos para tener al día los apuntes. Úsala si el señor acaba de subir material nuevo.",
     "label": "Leyendo los apuntes",
     "handler": campus_indexar_handler},
]
