# -*- coding: utf-8 -*-
"""Autocorrección de JARVIS: bus de errores + reparador persistente.

- report(source, error, context): registra y dispara reparación INMEDIATA.
- fixer_loop: cada 60s revisa pendientes con backoff exponencial.
  NO abandona: reintenta hasta 10 veces con estrategias escaladas
  (transitorio -> reinicio -> protocolo -> OpenCode parche -> OpenCode
  crea-herramienta). Tras 10 intentos pasa a "escalated" y sigue
  reintentando 1 vez al día, avisando al señor.
- Cubre cualquier error: sistema, cerebro, Discord, pantalla, voz,
  pensamiento (LLM) o herramienta inexistente (la crea).
- Emite "estoy solucionándolo, señor" al empezar y
  "error solucionado, señor" al cerrar, por voz y panel.
"""
import json
import logging
import os
import threading
import time
import traceback

log = logging.getLogger("memory.selfheal")

_MAX_ATTEMPTS = 10
_BACKOFF = [0, 60, 300, 900, 1800, 3600, 7200, 14400, 43200, 86400]
_APPROVAL_TIMEOUT = 90.0
_loop_started = False
_fixing_now: set = set()
_approvals: dict = {}


def _path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "errors.json")


def _load() -> list:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items[-200:], f, ensure_ascii=False)
        os.replace(tmp, _path())
    except Exception as e:
        log.debug("selfheal save: %s", e)


def report(source: str, error, context: str = "") -> dict:
    items = _load()
    now = time.time()
    err = str(error)[:400]
    for it in reversed(items[-20:]):
        if it.get("source") == source and it.get("error") == err \
                and now - it.get("ts", 0) < 3600 \
                and it.get("status") in ("pending", "escalated", "informativo"):
            it["hits"] = it.get("hits", 1) + 1
            it["ts"] = now
            # La severidad se recalcula siempre. Si el error ya no cumple los
            # criterios críticos (o nunca los cumplió y lo marcó una versión
            # antigua del filtro), degrado a aviso en vez de arrastrarlo a la
            # cola de reparación para siempre.
            critico = es_critico(source, err, context)
            it["severidad"] = "critico" if critico else "aviso"
            if critico:
                it["status"] = "pending"
            elif it.get("status") in ("pending", "escalated"):
                it["status"] = "informativo"
                it["next_retry"] = now + 86400
            _save(items)
            if critico and it["hits"] == 1:
                _announce("found", it)
                fix_now()
            return it
    critico = es_critico(source, err, context)
    item = {"id": f"E{int(now)}", "ts": now, "source": source, "error": err,
            "context": (context or "")[:800],
            "status": "pending" if critico else "informativo",
            "severidad": "critico" if critico else "aviso",
            "attempts": 0, "hits": 1, "history": [],
            "next_retry": now if critico else now + 86400}
    items.append(item)
    _save(items)
    if critico:
        _announce("found", item)
        fix_now()
    else:
        log.info("[autocorrector] aviso, no se repara: %s: %s", source, err[:140])
    return item


def list_errors(limit: int = 10, only_pending: bool = False) -> list:
    items = _load()
    if only_pending:
        # Solo lo que de verdad hay que arreglar: los avisos no entran aquí.
        items = [i for i in items
                 if i.get("status") in ("pending", "escalated")
                 and _es_critico_item(i)]
    return items[-limit:]


def _set(item_id: str, **kw) -> None:
    items = _load()
    for it in items:
        if it.get("id") == item_id:
            it.update(kw)
            break
    _save(items)


def _plan_for(item: dict) -> str:
    kinds = {"transient": "esperar y reintentar",
             "restart": "reiniciar el componente",
             "missing_tool": "crear un protocolo con herramientas existentes",
             "create_tool": "programar la herramienta que falta con OpenCode",
             "opencode": "reparar el código con OpenCode",
             "needs_user": "guiarle con los pasos a seguir"}
    return kinds.get(_classify(item), "repararlo")


def request_approval(item: dict) -> bool:
    """Pide permiso al señor antes de tocar nada. Devuelve True si aprueba.

    Pregunta por popup Win+J + voz + DM Discord. 90s sin respuesta = procede
    solo avisando (para no dejar errores colgados si no está delante).
    """
    if item.get("approved") is True:
        return True
    if item.get("approved") is False:
        return False
    err_short = (item.get("error", "") or "")[:140]
    plan = _plan_for(item)
    emit_fn = _CTX.get("emit_fn")
    if emit_fn:
        try:
            emit_fn({"type": "heal_approval_request",
                     "id": item.get("id"), "source": item.get("source", ""),
                     "error": err_short, "plan": plan,
                     "timeout_seconds": int(_APPROVAL_TIMEOUT)})
        except Exception:
            pass
    q = (f"Señor, ¿me permite arreglarlo? Error: {err_short}. "
         f"Plan: {plan}. Dígame sí o no.")
    _say(emit_fn, q)
    voice_fn = _CTX.get("voice_fn")
    if voice_fn:
        try:
            voice_fn(q)
        except Exception:
            pass
    discord_fn = _CTX.get("discord_fn")
    if discord_fn:
        try:
            discord_fn(f"Solicitud de permiso.\n¿Me permite el arreglo, señor?\n"
                       f"Error: {err_short}\n"
                       f"Plan: {plan}\nResponda APROBAR o DENEGAR.")
        except Exception:
            pass
    ev = threading.Event()
    _approvals[item["id"]] = {"event": ev, "approved": None}
    got = ev.wait(timeout=_APPROVAL_TIMEOUT)
    rec = _approvals.pop(item["id"], {"approved": None})
    if got and rec.get("approved") is True:
        _set(item["id"], approved=True)
        return True
    if got and rec.get("approved") is False:
        _set(item["id"], approved=False, status="denied",
             note="denegado por el señor")
        return False
    # Sin respuesta NO se procede: el señor manda. Queda pendiente para
    # cuando conteste, sin reintentar por nuestra cuenta.
    _set(item["id"], next_retry=time.time() + 1800, note="esperando su permiso")
    if emit_fn:
        try:
            emit_fn({"type": "assistant_reply",
                     "text": "Sigo esperando su permiso, señor. No tocaré nada "
                             "hasta que me lo dé."})
        except Exception:
            pass
    return False


def resolve_approval(item_id: str, approved: bool) -> bool:
    """El señor respondió (popup Win+J, chat o Discord)."""
    rec = _approvals.get((item_id or "").strip())
    if not rec:
        ids = list(_approvals.keys())
        if len(ids) == 1:
            rec = _approvals.get(ids[0])
            item_id = ids[0]
        else:
            return False
    rec["approved"] = bool(approved)
    try:
        rec["event"].set()
    except Exception:
        pass
    return True


def pending_approval_id() -> str | None:
    ids = list(_approvals.keys())
    return ids[-1] if ids else None


def busy() -> bool:
    """¿Hay alguna reparación ejecutándose ahora mismo?"""
    return bool(_fixing_now)


def active_items() -> list:
    """Errores que se están reparando en este instante (para preguntar)."""
    out = []
    for it in _load():
        if it.get("id") in _fixing_now:
            out.append(it)
    return out


def stop_active(reason: str = "parada por el señor") -> int:
    """Cancela las reparaciones en marcha. Devuelve cuántas paré."""
    n = 0
    for it in _load():
        if it.get("id") in _fixing_now and it.get("status") in ("pending", "escalated"):
            _set(it["id"], status="cancelled", note=reason,
                 next_retry=time.time() + 86400)
            n += 1
    return n


_NEEDS_USER_RE = (
    "sin acceso", "notallowederror", "notreadableerror",
    "could not start video source", "no hay cámara", "sin permiso",
    "permission denied", "device in use", "busy",
)

# --------------------------------------------------------------------------- #
# Qué merece reparación automática y qué no.
#
# Antes, cualquier cosa que fallara (un móvil sin conectar, una clave que
# falta, el señor cancelando) entraba en la cola, se anunciaba por voz y se
# llamaba a OpenCode para que tocara el código. Eso es tirar balones fuera del
# campo por una avería que no existe. Aquí se separa:
#
#   - CRÍTICO: el sistema roto de verdad (excepciones, módulos que faltan,
#     puertos ocupados, base de datos bloqueada, cableados mal). Se repara.
#   - AVISO: depende de que el señor conecte o configure algo, o de que él
#     cancele. Se apunta y ya está: ni voz, ni OpenCode, ni reintentos.
# --------------------------------------------------------------------------- #
_CRITICOS = (
    "traceback", "nameerror", "attributeerror", "importerror",
    "modulenotfound", "syntaxerror", "keyerror", "typeerror",
    "unboundlocalerror", "referenceerror", "indexerror", "assertionerror",
    "recursionerror", "memoryerror", "jsonsyntaxerror", "unicodedecodeerror",
    "cannot find module", "no such file or directory", "address already in use",
    "eaddrinuse", "database is locked", "no space left on device",
    "out of memory", "no me deja hablar con la interfaz",
    "no se pudo arrancar", "no arranca", "se cae el proceso",
    # El puente con la interfaz: si el Electron no habla con el cerebro, JARVIS
    # es sordo, y eso no se arregla esperando.
    "connection refused", "connectionerror", "connectionreseterror",
    "connection aborted", "econnrefused", "no se pudo conectar",
    "websocketserver is not running", "handshake status 404",
)

# Situaciones que NO son averías: sin móvil, sin configurar, cancelado, hardware
# ocupado o sin permiso. Ningún parche de código las arregla.
_NO_REPARAR = (
    "no veo ningun movil", "no veo ningún móvil", "ningun movil conectado",
    "ningún móvil conectado", "conectalo por usb", "conéctalo por usb",
    "no configurado", "no esta configurado", "no está configurado",
    "sin claves", "falta la clave", "no hay cámara", "no hay camara",
    "notallowederror", "notreadableerror", "could not start video source",
    "permission denied", "sin permiso", "acceso denegado", "device in use",
    "cancelado", "sin confirmacion", "sin confirmación",
    "no conozco ese juego", "no conozco esa herramienta",
    "no esta en el catalogo", "no está en el catálogo",
    "no such device", "no such host", "no hay raspberry",
    "no such file or directory: 'adb", "requested device not found",
)

def es_critico(source: str, error, context: str = "") -> bool:
    """True solo si esto es una avería real del sistema.

    Se mira el error y de dónde viene, NO el contexto: el contexto lleva el
    volcado de la excepción, y ahí aparece la palabra 'Traceback' hasta en un
    fallo tonto como que la máquina virtual está apagada. Clasificar por el
    contexto convertía cualquierimuslo en avería.

    Con una sola vez no se toca nada: casi todo lo que falla una vez se
    resuelve solo. La lista es corta a propósito, porque una reparación
    automática equivocada es peor que no reparar.
    """
    txt = ("%s %s" % (source, error)).lower()
    if any(k in txt for k in _NO_REPARAR):
        return False
    return any(k in txt for k in _CRITICOS)


def _es_critico_item(item: dict) -> bool:
    # No me fío del campo guardado: si un error antiguo quedó marcado como
    # crítico con el filtro viejo, aquí se recalcula con el de ahora.
    return es_critico(item.get("source", ""), item.get("error", ""),
                      item.get("context", ""))


def _classify(item: dict) -> str:
    err = (item.get("error", "") + " " + item.get("source", "")).lower()
    ctx = (item.get("context", "") or "").lower()
    src = (item.get("source", "") or "").lower()
    n = item.get("attempts", 0)
    # Hardware ocupado/denegado: ningún parche de código lo arregla.
    # Se guía al señor una vez y se cierra (no se quema OpenCode).
    if any(k in err for k in _NEEDS_USER_RE):
        return "needs_user"
    # Errores de UI (orbe, overlay, cámara, gestos) casi nunca son
    # transitorios: van directos a OpenCode con el repo correcto.
    if any(k in src for k in ("orb", "hud", "overlay", "camera", "gesture")) \
            and "restart:" not in src:
        if any(k in err for k in ("timeout", "tempor", "econn", "socket")) and n == 0:
            return "transient"
        return "opencode"
    if any(k in err for k in ("timeout", "429", "503", "tempor", "connection",
                              "rate limit", "econn", "socket")) and n == 0:
        return "transient"
    if "nodetool" in err or "no tengo herramienta" in ctx \
            or "no conozco esa herramienta" in err \
            or "missing_tool" in item.get("source", ""):
        return "missing_tool" if n < 3 else "create_tool"
    if item.get("source", "").startswith("restart:"):
        return "restart"
    if n == 0 and any(k in err for k in ("timeout", "tempor")):
        return "transient"
    if n >= 4:
        return "create_tool" if "herramienta" in err or "protocol" in ctx else "opencode"
    if any(k in err for k in ("nameerror", "attributeerror", "importerror",
                              "modulenotfound", "syntaxerror", "keyerror",
                              "typeerror", "traceback", "exception", "error",
                              "falló", "failed", "discord", "pantalla",
                              "screen", "tts", "stt", "wake", "cámara")):
        return "opencode"
    return "opencode" if n >= 1 else "transient"


def _fix_transient(item: dict) -> tuple[bool, str]:
    time.sleep(10)
    return True, "reintentado tras espera (transitorio)"


def _fix_needs_user(item: dict) -> tuple[bool, str]:
    err = (item.get("error", "") or "")[:300]
    if "permiso" in err.lower() or "notallowed" in err.lower() or "denied" in err.lower():
        guide = ("Permita la cámara para JARVIS en Configuración > Privacidad y "
                 "seguridad > Cámara, señor, y reintente.")
    elif "no hay cámara" in err.lower():
        guide = "Este equipo no expone ninguna cámara, señor: nada que activar."
    else:
        guide = ("Si la cámara es USB externa, desenchúfela y conéctela a OTRO "
                 "puerto USB trasero, señor. Si no, cierre las apps que la usen "
                 "(Zoom, Teams, Discord, navegador) y reintente.")
    return True, f"requiere al señor: {guide} (detalle: {err})"


def _fix_restart(item: dict, hooks: dict) -> tuple[bool, str]:
    comp = item.get("source", "").split(":", 1)[-1]
    fn = (hooks or {}).get(comp)
    if not fn:
        return False, f"sin hook de reinicio para {comp}"
    try:
        fn()
        return True, f"componente {comp} reiniciado"
    except Exception as e:
        return False, f"reinicio falló: {e}"


def _fix_missing_tool(item: dict, ask_fn) -> tuple[bool, str]:
    """Pide al LLM un protocolo con tools existentes y lo guarda."""
    if not ask_fn:
        return False, "sin LLM disponible para mapear protocolo"
    try:
        from memory import protocols as _prot
        tools = []
        try:
            import tool_manager as _tm  # noqa
            tools = sorted((_tm.all_tools_names() if hasattr(_tm, "all_tools_names") else []))
        except Exception:
            pass
        plan = ask_fn(item.get("context", "") or item.get("error", ""), tools)
        if not plan:
            return False, "sin mapeo posible: escalo a crear herramienta"
        _prot.add(plan.get("name", "auto"), plan.get("triggers", []),
                  plan.get("actions", []), plan.get("say", ""))
        return True, f"protocolo «{plan.get('name', 'auto')}» creado"
    except Exception as e:
        return False, f"protocolo falló: {e}"


def _fix_create_tool(item: dict) -> tuple[bool, str]:
    """Escalado máximo: OpenCode escribe una herramienta nueva real en
    brain/tools/custom_tools.py y se recarga en caliente."""
    from tools import opencode_tools as _oc
    try:
        workdir = _oc._brain_dir()
    except Exception:
        workdir = "."
    task = (_oc.BUILD_TOOL_PROMPT.format(
        source=item.get("source", "?"), error=item.get("error", "?"),
        context=item.get("context", "") or "-"))
    res = _oc.opencode_fix(task, workdir=workdir, timeout=600)
    if not res.get("ok"):
        return False, res.get("error", "OpenCode no pudo crear la herramienta")
    try:
        import tool_manager as _tmmod  # noqa
        loaded = 0
        try:
            from tools import custom_tools  # noqa
            import importlib as _il
            _il.reload(custom_tools)
            loaded = len(getattr(custom_tools, "CUSTOM_TOOLS", []) or [])
        except Exception:
            pass
        return True, (f"herramienta creada por OpenCode "
                      f"({len(res.get('files', []))} ficheros, "
                      f"{loaded} custom registradas): "
                      f"{', '.join(res.get('files', [])[:3])}")
    except Exception as e:
        return True, f"herramienta creada (recarga pendiente de reinicio: {e})"


def _fix_opencode(item: dict) -> tuple[bool, str]:
    from tools import opencode_tools as _oc
    if not _oc.ensure_opencode():
        return False, "OpenCode CLI no disponible (intenté instalarlo sin éxito)"
    try:
        workdir = _oc._brain_dir()
    except Exception:
        workdir = "."
    blob = ((item.get("error", "") or "") + " " + (item.get("context", "") or "")
            + " " + (item.get("source", "") or "")).lower()
    if any(k in blob for k in ("gestures.js", "overlay", "orb", "hud",
                               "app\\ui", "app/ui", "win+j", "electron",
                               ".js", "camera", "cámara", "camera-tracking")):
        # Fuentes de la app derivadas del brain en ejecución (nunca rutas fijas).
        try:
            from config import BRAIN_DIR as _BD
            repo_app_ui = os.path.join(str(_BD), "..", "app", "ui")
        except Exception:
            repo_app_ui = os.path.join(workdir, "..", "app", "ui")
        for cand in (os.path.abspath(repo_app_ui),
                     os.path.join(workdir, "..", "app", "ui")):
            if os.path.isdir(cand):
                workdir = os.path.abspath(cand)
                break
    task = (_oc.REPAIR_PROMPT.format(
        workdir=workdir, source=item.get("source", "?"),
        error=item.get("error", "?"), context=item.get("context", "") or "-",
        attempt=item.get("attempts", 0) + 1))
    res = _oc.opencode_fix(task, workdir=workdir)
    if res.get("ok"):
        changed = res.get("files", [])
        return True, f"OpenCode reparó ({len(changed)} ficheros): {', '.join(changed[:4])}"
    return False, res.get("error", "OpenCode no pudo")


def _say(emit_fn, text: str) -> None:
    if not emit_fn:
        return
    try:
        emit_fn({"type": "assistant_reply", "text": text})
    except Exception:
        pass


def _announce(stage: str, item: dict, note: str = "", quiet: bool = False) -> None:
    """Avisa por TODOS los canales a la vez: panel Win+J (evento estructurado),
    chat (texto), voz (TTS si está en reposo) y Discord (DM al móvil).

    stages: found | fixing | fixed | needs_user | escalated
    quiet=True: solo evento al panel (para fallos hardware ya sentenciados
    que se repiten: sin voz, sin DM, sin spam).
    """
    emit_fn = _CTX.get("emit_fn")
    err_short = (item.get("error", "") or "")[:140]
    texts = {
        "found": f"He encontrado un error, señor: {err_short}. Voy a arreglarlo ahora mismo.",
        "fixing": f"Estoy solucionándolo, señor: {err_short}.",
        "fixed": f"Error solucionado, señor: {(note or '')[:200]}.",
        "needs_user": f"Necesito su ayuda, señor: {(note or '')[:220]}",
        "escalated": f"Sigo con ello, señor, no lo dejo: {(note or '')[:150]}.",
    }
    text = texts.get(stage, note or err_short)
    if emit_fn:
        try:
            emit_fn({"type": "selfheal_update",
                     "id": item.get("id"), "source": item.get("source", ""),
                     "error": err_short, "stage": stage,
                     "note": (note or "")[:300],
                     "attempt": int(item.get("attempts", 0) or 0)})
        except Exception:
            pass
    if quiet:
        return
    _say(emit_fn, text)
    if stage in ("found", "fixed", "needs_user", "escalated"):
        voice_fn = _CTX.get("voice_fn")
        if voice_fn:
            try:
                voice_fn(text)
            except Exception:
                pass
        discord_fn = _CTX.get("discord_fn")
        if discord_fn:
            try:
                discord_fn(text)
            except Exception:
                pass


def _repeat_fixed_count(item: dict) -> int:
    """Veces que este mismo fallo ya se cerró antes (para no taladrar con
    permisos/avisos por un hardware sentenciado)."""
    try:
        key = ((item.get("source", "") or "") + "|" + (item.get("error", "") or "")[:40]).lower()
        n = 0
        for it in _load():
            k = ((it.get("source", "") or "") + "|" + (it.get("error", "") or "")[:40]).lower()
            if k == key and it.get("status") == "fixed" and it.get("id") != item.get("id"):
                n += 1
        return n
    except Exception:
        return 0


def fixer_pass(hooks: dict = None, ask_fn=None, emit_fn=None) -> list:
    """Una pasada sobre pendientes vencidos. Nunca abandona: backoff + escalado."""
    out = []
    now = time.time()
    for item in [i for i in _load() if i.get("status") in ("pending", "escalated")]:
        if not _es_critico_item(item):
            # Aviso (sin móvil, sin clave, cancelado): se cierra como resuelto
            # y no vuelve a intentarlo. Antes quemaba intentos y preguntaba.
            _set(item["id"], status="informativo",
                 note="aviso, no es una avería: no se repara solo")
            continue
        if item.get("id") in _fixing_now:
            continue
        if now < float(item.get("next_retry", 0) or 0):
            continue
        n = int(item.get("attempts", 0) or 0)
        kind = _classify(item)
        _fixing_now.add(item["id"])
        quiet = kind == "needs_user" and _repeat_fixed_count(item) >= 2
        if n == 0 and not quiet and not request_approval(item):
            _fixing_now.discard(item["id"])
            out.append((item["id"], False, "denegado por el señor"))
            continue
        _announce("fixing", item, quiet=quiet)
        _set(item["id"], attempts=n + 1)
        if kind in ("opencode", "create_tool"):
            # La ventanita muestra en qué anda OpenCode (tarda minutos).
            _announce("fixing", {**item, "attempts": n + 1},
                      f"OpenCode manos a la obra, señor (intento {n + 1}). "
                      f"Tarda unos minutos; le aviso al terminar.")
        try:
            if kind == "transient":
                ok, note = _fix_transient(item)
            elif kind == "needs_user":
                ok, note = _fix_needs_user(item)
            elif kind == "restart":
                ok, note = _fix_restart(item, hooks or {})
            elif kind == "missing_tool":
                ok, note = _fix_missing_tool(item, ask_fn)
                if not ok and "escalo" in note:
                    ok, note = _fix_create_tool(item)
            elif kind == "create_tool":
                ok, note = _fix_create_tool(item)
            else:
                ok, note = _fix_opencode(item)
                if not ok and n >= 5:
                    ok2, note2 = _fix_create_tool(item)
                    if ok2:
                        ok, note = ok2, note2
        except Exception as e:
            ok, note = False, f"fixer: {e}"
            log.debug("fixer %s: %s", item.get("id"), e)
        finally:
            _fixing_now.discard(item["id"])
        hist = item.get("history", []) + [f"intento {n + 1} [{kind}]: {note}"]
        if ok:
            _set(item["id"], status="fixed", note=note, history=hist)
            item["attempts"] = n + 1
            _announce("fixed" if kind != "needs_user" else "needs_user", item, note,
                      quiet=quiet)
        else:
            nn = n + 1
            wait = _BACKOFF[min(nn, len(_BACKOFF) - 1)]
            status = "pending" if nn < _MAX_ATTEMPTS else "escalated"
            _set(item["id"], history=hist, status=status, note=note,
                 next_retry=time.time() + wait)
            if status == "escalated":
                item["attempts"] = nn
                _announce("escalated", item, note)
        out.append((item["id"], ok, note))
    return out


def auto_detect() -> list:
    """Chequeo proactivo: Discord muerto, brain sin keys, TTS roto, watcher
    caído. Reporta lo que encuentre. Llamado por el fixer_loop."""
    found = []
    try:
        from config import Config
        if not Config.has_any_key():
            found.append(report("health:keys", "Sin API keys configuradas",
                                "ModelRouter sin Groq/Gemini/OpenRouter"))
    except Exception as e:
        found.append(report("health:config", e, "leyendo Config"))
    try:
        import socket as _sk
        s = _sk.create_connection(("127.0.0.1", 8000), timeout=2)
        s.close()
    except Exception as e:
        found.append(report("health:brain-port", f"Puerto 8000 sin escucha: {e}",
                            "brain caído o arrancando"))
    return found


_CTX = {"hooks": None, "ask_fn": None, "emit_fn": None,
         "discord_fn": None, "voice_fn": None}


def set_notifiers(discord_fn=None, voice_fn=None) -> None:
    """Registra los canales push (DM Discord) y voz (TTS en reposo)."""
    if discord_fn is not None:
        _CTX["discord_fn"] = discord_fn
    if voice_fn is not None:
        _CTX["voice_fn"] = voice_fn


def fix_now(hooks=None, ask_fn=None, emit_fn=None) -> bool:
    """Dispara una pasada del reparador YA (sin esperar al ciclo)."""
    def _run():
        try:
            fixer_pass(hooks=hooks or _CTX.get("hooks"),
                       ask_fn=ask_fn or _CTX.get("ask_fn"),
                       emit_fn=emit_fn or _CTX.get("emit_fn"))
        except Exception as e:
            log.debug("fix_now: %s", e)

    threading.Thread(target=_run, daemon=True, name="selfheal-now").start()
    return True


def start_fixer_loop(hooks=None, ask_fn=None, emit_fn=None,
                     discord_fn=None, voice_fn=None,
                     every_sec: int = 60) -> bool:
    global _loop_started
    if _loop_started:
        _CTX["hooks"] = hooks or _CTX.get("hooks")
        _CTX["ask_fn"] = ask_fn or _CTX.get("ask_fn")
        _CTX["emit_fn"] = emit_fn or _CTX.get("emit_fn")
        _CTX["discord_fn"] = discord_fn or _CTX.get("discord_fn")
        _CTX["voice_fn"] = voice_fn or _CTX.get("voice_fn")
        return True
    _loop_started = True
    _CTX["hooks"] = hooks
    _CTX["ask_fn"] = ask_fn
    _CTX["emit_fn"] = emit_fn
    _CTX["discord_fn"] = discord_fn
    _CTX["voice_fn"] = voice_fn

    def _loop():
        time.sleep(20)
        while True:
            try:
                auto_detect()
            except Exception as e:
                log.debug("autodetect: %s", e)
            try:
                fixer_pass(hooks=_CTX.get("hooks"), ask_fn=_CTX.get("ask_fn"),
                           emit_fn=_CTX.get("emit_fn"))
            except Exception as e:
                log.debug("fixer loop: %s", e)
            time.sleep(every_sec)

    threading.Thread(target=_loop, daemon=True, name="selfheal").start()
    return True


def report_error(source: str, error, context: str = "") -> dict:
    """Atajo público para reportar desde cualquier módulo."""
    try:
        tb = "".join(traceback.format_exception(type(error), error,
                                                error.__traceback__))[-800:] \
            if isinstance(error, BaseException) else ""
        ctx = ((context or "") + ("\n" + tb if tb else "")).strip()[:800]
        return report(source, error, ctx)
    except Exception:
        return {}
