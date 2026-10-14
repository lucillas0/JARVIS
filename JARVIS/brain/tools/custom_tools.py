# -*- coding: utf-8 -*-
"""Herramientas creadas por la autocorrecciÃ³n de JARVIS.

Este fichero lo escribe OpenCode (vÃ­a selfheal) cuando detecta que falta
una herramienta: aÃ±ade un handler + entrada en CUSTOM_TOOLS siguiendo el
formato de ToolManager. Se carga automÃ¡ticamente al arrancar (y en caliente
si el reparador lo pide). NUNCA poner secretos aquÃ­.

TambiÃ©n se edita a mano para aÃ±adir Plugins. Los Plugins van aquÃ­ en vez de
en tools/ porque asÃ­ no hay que tocar el nÃºcleo: se sueltan en este fichero y
arrancan solos. Es la vÃ­a para ampliar JARVIS sin riesgo de romperlo.
"""
import logging

log = logging.getLogger("tools.custom")


def _ejemplo_handler(params: dict) -> dict:
    """Plantilla: no se registra (nombre empieza por _)."""
    return {"ok": True}


CUSTOM_TOOLS: list = []


# ======================================================================
# PLUGIN 1 Â· Agenda y correo
#
# Por quÃ© existe: integrations/calendar.py y integrations/gmail.py ya estaban
# escritos y funcionaban, pero NO los exponÃ­a ninguna herramienta. Solo los
# usaba routines/wake_up.py, el ritual de las maÃ±anas. Preguntarle a JARVIS
# "Â¿quÃ© tengo hoy?" o "Â¿tengo correos nuevos?" en una conversaciÃ³n normal
# devolvÃ­a un "ProcederÃ© a revisarlo, seÃ±or" y nada mÃ¡s: una promesa, sin
# hacer nada. El mÃ³dulo estaba ahÃ­, a la vista, sin puerta de entrada.
#
# Con esto, una llamada y estÃ¡. Si las credenciales no estÃ¡n puestas, lo dicen
# claro en vez de fingir.
# ======================================================================
def _agenda_handler(params: dict) -> dict:
    """Lo que hay en la agenda. Por defecto, hoy."""
    try:
        from integrations import calendar as cal
    except Exception as e:
        return {"ok": False, "error": f"no pude cargar el calendario: {e}"[:160]}
    if not cal.available():
        return {"ok": False, "error": "Calendar no estÃ¡ configurado. "
                "Hace falta el credentials.json de Google y conectarlo en /config."}
    try:
        dias = int(params.get("dias", 1) or 1)
    except (TypeError, ValueError):
        dias = 1
    dias = max(1, min(dias, 30))
    try:
        eventos = cal.list_today(limit=int(params.get("limite", 12) or 12))
    except Exception as e:
        return {"ok": False, "error": f"el calendario fallÃ³: {e}"[:160]}
    if dias > 1 and eventos:
        # list_today solo mira hoy. Para varios dias se encadena a mano; de
        # momento con mas de un dia se avisa en vez de fingir que se ha mirado.
        eventos = eventos[:1]
    return {"ok": True, "dia": dias, "eventos": eventos, "total": len(eventos)}


def _correo_handler(params: dict) -> dict:
    """Correos sin leer, resumidos."""
    try:
        from integrations import gmail as gm
    except Exception as e:
        return {"ok": False, "error": f"no pude cargar el correo: {e}"[:160]}
    try:
        n = max(1, min(int(params.get("limite", 10) or 10), 30))
    except (TypeError, ValueError):
        n = 10
    try:
        correos = gm.unread_summary(limit=n)
    except Exception as e:
        return {"ok": False, "error": f"el correo fallÃ³: {e}"[:160]}
    return {"ok": True, "correos": correos, "total": len(correos)}


CUSTOM_TOOLS.append({
    "name": "agenda_de_hoy",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {"limite": "int opcional, cuÃ¡ntos eventos (defecto 12)"},
    "description": "Lo que hay hoy en el calendario del seÃ±or: hora, tÃ­tulo y "
                   "dÃ³nde. Ãšsala SIEMPRE que pregunte quÃ© tiene hoy, la agenda, "
                   "si estÃ¡ ocupado, o a quÃ© hora es algo. No contestes de memoria.",
    "label": "Mirando la agenda",
    "handler": _agenda_handler,
})

CUSTOM_TOOLS.append({
    "name": "correo_sin_leer",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {"limite": "int opcional, cuÃ¡ntos correos (defecto 10)"},
    "description": "Correos sin leer, con remitente y asunto. Ãšsala SIEMPRE "
                   "que pregunte por el correo, por mensajes nuevos o si tiene "
                   "algo pendiente. No contestes de memoria.",
    "label": "Mirando el correo",
    "handler": _correo_handler,
})


# -*- coding: utf-8 -*-
"""Plugin: control del Synology por su API (DSM 7).

Por qué sin dependencias: la API de DSM es HTTP + JSON con un identificador de
sesión. Se puede hablar con ella con la librería estándar, y en un contenedor
eso importa: `pip install synology-api` añadiría seis paquetes a la imagen solo
para leer un JSON. Si algún día hace falta más, esta misma capa se cambia por
la librería sin tocar las herramientas.

El NAS se identifica con DSM_NAS_URL (http://192.168.1.10:5000 o
https://nas:5001), y las credenciales con DSM_NAS_USER y DSM_NAS_PASSWORD. Sin
ellas las herramientas lo dicen claro en vez de fingir que han preguntado.

La API va por el puerto 5000 (http) o 5001 (https), NO por el 5001 de DSM web
que es el 5001/5000 respectivamente; de ahí los dos valores de abajo.
"""
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("tools.custom.dsm")

# Las capacidades de DSM se piden una vez: cada endpoint va con su versión, y
# adivinarla es la causa número uno de "API no encontrada" en DSM 7.
_CAPS: dict = {}
_SID: dict = {"valor": "", "hasta": 0.0}
_TIMEOUT = 8

# Cortacircuitos. Sin esto, pedir el espacio cuando el NAS está dormido costaba
# 32 SEGUNDOS: la versión de la API, el login y la llamada en sí. Cada capa
# reintenta por su cuenta y se apilan, 8 s cada una. Es justo el tipo de espera
# invisible que hace que JARVIS parezca lento en un servidor, y por nada,
# porque el NAS no está.
#
# Si una llamada falla por red, se anota aquí y las siguientes contestan al
# momento hasta que pase un minuto. Se olvida en cuanto el NAS contesta.
_CORTADO: dict = {"hasta": 0.0, "motivo": ""}
_COOLDOWN = 60.0


def _cortado() -> str:
    if time.time() < _CORTADO["hasta"]:
        return _CORTADO["motivo"]
    return ""


def _cortar(motivo: str) -> None:
    _CORTADO["hasta"] = time.time() + _COOLDOWN
    _CORTADO["motivo"] = motivo
    log.info("NAS no disponible durante %ss: %s", int(_COOLDOWN), motivo)


def _descansar() -> None:
    _CORTADO["hasta"] = 0.0
    _CORTADO["motivo"] = ""


def _conf() -> dict:
    url = (os.environ.get("DSM_NAS_URL") or "").strip().rstrip("/")
    return {
        "url": url,
        "user": (os.environ.get("DSM_NAS_USER") or "").strip(),
        "clave": os.environ.get("DSM_NAS_PASSWORD") or "",
        "insecure": (os.environ.get("DSM_NAS_INSEGURO", "1").strip()
                     not in ("0", "false", "no")),
    }


def _contexto() -> ssl.SSLContext | None:
    c = _conf()
    if not c["insecure"]:
        return None                      # verificación normal
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pedir(ruta: str, params: dict) -> dict:
    """Una llamada a la API. Devuelve {"ok":bool, "data":..., "error":...}."""
    c = _conf()
    if not c["url"]:
        return {"ok": False, "error": "No hay NAS configurado. Pon DSM_NAS_URL "
                                      "(http://IP:5000) y las credenciales."}
    # Cortacircuitos: si hace poco que el NAS no contestaba, se dice al momento.
    if _cortado():
        return {"ok": False, "error": f"El NAS no responde ahora mismo ({_cortado()}). "
                                      f"Vuelvo a intentarlo en un minuto."}
    url = f"{c['url']}{ruta}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT,
                                    context=_contexto()) as r:
            crudo = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Un 401/403 es el NAS contestando: el cortacircuitos no aplica.
        return {"ok": False, "error": f"el NAS respondió {e.code}"}
    except Exception as e:
        _cortar(str(e)[:80])
        return {"ok": False, "error": f"no se pudo contactar con el NAS: {e}"[:150]}
    _descansar()
    try:
        d = json.loads(crudo)
    except Exception:
        return {"ok": False, "error": "respuesta del NAS que no es JSON"}
    if not d.get("success"):
        return {"ok": False, "error": (d.get("error") or {}).get("errors")
                or f"error {d.get('error')}"}
    return {"ok": True, "data": d.get("data")}


def _api(nombre: str) -> str:
    """La versión que dice el propio NAS, no una inventada."""
    if nombre in _CAPS:
        return _CAPS[nombre]
    r = _pedir("/webapi/query.cgi", {"api": "SYNO.API.Info", "version": 1,
                                     "method": "query", "query": "ALL"})
    if not r["ok"]:
        _CAPS[nombre] = "1"
        return "1"
    apis = (r["data"] or {}).get("SYNO.API") or {}
    info = apis.get(nombre) or {}
    _CAPS[nombre] = str(info.get("maxVersion") or info.get("minVersion") or 1)
    return _CAPS[nombre]


def _sid() -> str:
    """Sesión, con caducidad. En DSM 7 la sesión caduca a los 30 min."""
    if _SID["valor"] and time.time() < _SID["hasta"]:
        return _SID["valor"]
    c = _conf()
    if not c["url"]:
        return ""
    if c["user"] and c["clave"]:
        r = _pedir("/webapi/auth.cgi", {
            "api": "SYNO.API.Auth", "version": _api("SYNO.API.Auth"),
            "method": "login", "account": c["user"],
            "passwd": c["clave"], "session": "FileStation", "format": "sid",
        })
    else:
        # Sin credenciales: sesión anónima. DSM la da si el usuario esInvitado
        # con permiso de lectura, que es el caso normal de un share.
        r = _pedir("/webapi/auth.cgi", {
            "api": "SYNO.API.Auth", "version": _api("SYNO.API.Auth"),
            "method": "login", "account": "guest", "passwd": "",
            "session": "FileStation", "format": "sid",
        })
    if not r["ok"]:
        log.debug("login DSM: %s", r.get("error"))
        return ""
    _SID["valor"] = (r["data"] or {}).get("sid", "")
    _SID["hasta"] = time.time() + 25 * 60      # un poco antes de caducar
    return _SID["valor"]


def _llamar(api: str, metodo: str, version: str, params: dict | None = None) -> dict:
    p = {"api": api, "version": version, "method": metodo}
    p.update(params or {})
    s = _sid()
    if s:
        p["_sid"] = s
    return _pedir("/webapi/entry.cgi", p)


# -*- coding: utf-8 -*-
"""Plugin: control del Synology por su API (DSM 7).

Por qué sin dependencias: la API de DSM es HTTP + JSON con un identificador de
sesión. Se puede hablar con ella con la librería estándar, y en un contenedor
eso importa: `pip install synology-api` añadiría seis paquetes a la imagen solo
para leer un JSON. Si algún día hace falta más, esta misma capa se cambia por
la librería sin tocar las herramientas.

El NAS se identifica con DSM_NAS_URL (http://192.168.1.10:5000 o
https://nas:5001), y las credenciales con DSM_NAS_USER y DSM_NAS_PASSWORD. Sin
ellas las herramientas lo dicen claro en vez de fingir que han preguntado.

La API va por el puerto 5000 (http) o 5001 (https), NO por el 5001 de DSM web
que es el 5001/5000 respectivamente; de ahí los dos valores de abajo.
"""
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("tools.custom.dsm")

# Las capacidades de DSM se piden una vez: cada endpoint va con su versión, y
# adivinarla es la causa número uno de "API no encontrada" en DSM 7.
_CAPS: dict = {}
_SID: dict = {"valor": "", "hasta": 0.0}
_TIMEOUT = 8

# Cortacircuitos. Sin esto, pedir el espacio cuando el NAS está dormido costaba
# 32 SEGUNDOS: la versión de la API, el login y la llamada en sí. Cada capa
# reintenta por su cuenta y se apilan, 8 s cada una. Es justo el tipo de espera
# invisible que hace que JARVIS parezca lento en un servidor, y por nada,
# porque el NAS no está.
#
# Si una llamada falla por red, se anota aquí y las siguientes contestan al
# momento hasta que pase un minuto. Se olvida en cuanto el NAS contesta.
_CORTADO: dict = {"hasta": 0.0, "motivo": ""}
_COOLDOWN = 60.0


def _cortado() -> str:
    if time.time() < _CORTADO["hasta"]:
        return _CORTADO["motivo"]
    return ""


def _cortar(motivo: str) -> None:
    _CORTADO["hasta"] = time.time() + _COOLDOWN
    _CORTADO["motivo"] = motivo
    log.info("NAS no disponible durante %ss: %s", int(_COOLDOWN), motivo)


def _descansar() -> None:
    _CORTADO["hasta"] = 0.0
    _CORTADO["motivo"] = ""


def _conf() -> dict:
    url = (os.environ.get("DSM_NAS_URL") or "").strip().rstrip("/")
    return {
        "url": url,
        "user": (os.environ.get("DSM_NAS_USER") or "").strip(),
        "clave": os.environ.get("DSM_NAS_PASSWORD") or "",
        "insecure": (os.environ.get("DSM_NAS_INSEGURO", "1").strip()
                     not in ("0", "false", "no")),
    }


def _contexto() -> ssl.SSLContext | None:
    c = _conf()
    if not c["insecure"]:
        return None                      # verificación normal
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pedir(ruta: str, params: dict) -> dict:
    """Una llamada a la API. Devuelve {"ok":bool, "data":..., "error":...}."""
    c = _conf()
    if not c["url"]:
        return {"ok": False, "error": "No hay NAS configurado. Pon DSM_NAS_URL "
                                      "(http://IP:5000) y las credenciales."}
    # Cortacircuitos: si hace poco que el NAS no contestaba, se dice al momento.
    if _cortado():
        return {"ok": False, "error": f"El NAS no responde ahora mismo ({_cortado()}). "
                                      f"Vuelvo a intentarlo en un minuto."}
    url = f"{c['url']}{ruta}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT,
                                    context=_contexto()) as r:
            crudo = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Un 401/403 es el NAS contestando: el cortacircuitos no aplica.
        return {"ok": False, "error": f"el NAS respondió {e.code}"}
    except Exception as e:
        _cortar(str(e)[:80])
        return {"ok": False, "error": f"no se pudo contactar con el NAS: {e}"[:150]}
    _descansar()
    try:
        d = json.loads(crudo)
    except Exception:
        return {"ok": False, "error": "respuesta del NAS que no es JSON"}
    if not d.get("success"):
        return {"ok": False, "error": (d.get("error") or {}).get("errors")
                or f"error {d.get('error')}"}
    return {"ok": True, "data": d.get("data")}


def _api(nombre: str) -> str:
    """La versión que dice el propio NAS, no una inventada."""
    if nombre in _CAPS:
        return _CAPS[nombre]
    r = _pedir("/webapi/query.cgi", {"api": "SYNO.API.Info", "version": 1,
                                     "method": "query", "query": "ALL"})
    if not r["ok"]:
        _CAPS[nombre] = "1"
        return "1"
    apis = (r["data"] or {}).get("SYNO.API") or {}
    info = apis.get(nombre) or {}
    _CAPS[nombre] = str(info.get("maxVersion") or info.get("minVersion") or 1)
    return _CAPS[nombre]


def _sid() -> str:
    """Sesión, con caducidad. En DSM 7 la sesión caduca a los 30 min."""
    if _SID["valor"] and time.time() < _SID["hasta"]:
        return _SID["valor"]
    c = _conf()
    if not c["url"]:
        return ""
    if c["user"] and c["clave"]:
        r = _pedir("/webapi/auth.cgi", {
            "api": "SYNO.API.Auth", "version": _api("SYNO.API.Auth"),
            "method": "login", "account": c["user"],
            "passwd": c["clave"], "session": "FileStation", "format": "sid",
        })
    else:
        # Sin credenciales: sesión anónima. DSM la da si el usuario esInvitado
        # con permiso de lectura, que es el caso normal de un share.
        r = _pedir("/webapi/auth.cgi", {
            "api": "SYNO.API.Auth", "version": _api("SYNO.API.Auth"),
            "method": "login", "account": "guest", "passwd": "",
            "session": "FileStation", "format": "sid",
        })
    if not r["ok"]:
        log.debug("login DSM: %s", r.get("error"))
        return ""
    _SID["valor"] = (r["data"] or {}).get("sid", "")
    _SID["hasta"] = time.time() + 25 * 60      # un poco antes de caducar
    return _SID["valor"]


def _llamar(api: str, metodo: str, version: str, params: dict | None = None) -> dict:
    p = {"api": api, "version": version, "method": metodo}
    p.update(params or {})
    s = _sid()
    if s:
        p["_sid"] = s
    return _pedir("/webapi/entry.cgi", p)


# ======================================================================
# Herramientas
# ======================================================================
def _nas_espacio_handler(params: dict) -> dict:
    """Cuánto disco queda, por volumen."""
    r = _llamar("SYNO.Core.Storage.Volume", "list", _api("SYNO.Core.Storage.Volume"))
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    vols = []
    for v in (r["data"] or {}).get("volumes", []):
        try:
            total = int(v.get("size", 0) or 0)
            libre = int(v.get("size_free", 0) or 0)
        except (TypeError, ValueError):
            continue
        usado = max(0, total - libre)
        pct = (usado / total * 100) if total else 0
        vols.append({
            "volumen": v.get("volume") or v.get("volume_id"),
            "tipo": v.get("volume_type"),
            "estado": v.get("status"),
            "total_gb": round(total / 1e9, 1),
            "libre_gb": round(libre / 1e9, 1),
            "usado_pct": round(pct, 1),
            "aviso": "LLENO" if pct >= 90 else ("ajustado" if pct >= 75 else ""),
        })
    vols.sort(key=lambda x: x["volumen"] or "")
    return {"ok": True, "volumenes": vols, "total": len(vols)}


def _nas_salud_handler(params: dict) -> dict:
    """CPU, RAM, temperatura y nombre del NAS."""
    r = _llamar("SYNO.Core.System", "info", _api("SYNO.Core.System"))
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    d = r["data"] or {}
    u = _llamar("SYNO.Core.System", "utilization", _api("SYNO.Core.System"))
    cpu = {}
    if u["ok"]:
        raw = (u["data"] or {}).get("cpu", {})
        try:
            total = float(raw.get("total", 0) or 0)
            # La API devuelve el desglose en jiffies: user, system, nice, irq,
            # softirq, steal e idle. Lo ocupado es el total MENOS idle. Sumar
            # todos los campos contaria el idle como carga, que es justo lo
            # contrario de lo que significa.
            idle = float(raw.get("idle", 0) or 0)
            if total > 0:
                cpu = {"carga_pct": round((total - idle) / total * 100, 1)}
        except Exception as e:
            log.debug("cpu: %s", e)
    mem = {}
    if u["ok"]:
        m = (u["data"] or {}).get("memory", {})
        try:
            tot = float(m.get("total", 0) or 0)
            used = float(m.get("used", 0) or 0)
            mem = {"ram_total_gb": round(tot / 1e9, 1),
                   "ram_usada_gb": round(used / 1e9, 1),
                   "ram_usada_pct": round(used / tot * 100, 1) if tot else 0}
        except Exception:
            pass
    temp = ""
    try:
        t = _llamar("SYNO.Core.System", "temperature",
                    _api("SYNO.Core.System"))
        if t["ok"]:
            temp = str(((t["data"] or {}).get("temperature") or {}).get("celsius", ""))
    except Exception:
        pass
    return {"ok": True, "modelo": d.get("model"),
            "hostname": d.get("hostname"),
            "uptime_seg": d.get("uptime"),
            "temperatura_c": temp, **cpu, **mem}


def _nas_contenedores_handler(params: dict) -> dict:
    """Qué contenedores hay corriendo en Container Manager."""
    r = _llamar("SYNO.Docker.Container", "list", _api("SYNO.Docker.Container"))
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    conts = []
    for c in (r["data"] or {}).get("containers", []):
        est = c.get("status")
        conts.append({
            "nombre": c.get("name"),
            "imagen": c.get("image"),
            "estado": est,
            "corriendo": (est or "").lower() in ("running", "started"),
        })
    return {"ok": True, "contenedores": conts, "total": len(conts),
            "corriendo": sum(1 for c in conts if c["corriendo"])}


def _nas_logs_handler(params: dict) -> dict:
    """Las últimas líneas del registro del NAS."""
    n = max(10, min(int(params.get("lineas", 60) or 60), 500))
    r = _llamar("SYNO.Core.System", "log", _api("SYNO.Core.System"),
                {"mode": "fetch", "limit": n, "start": 0})
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    lineas = []
    for it in (r["data"] or {}).get("logs", []):
        if isinstance(it, dict):
            msg = it.get("msg") or it.get("message") or ""
            lvl = it.get("level", "")
            ts = it.get("time", "")
            if msg:
                lineas.append(f"[{lvl}] {ts} {msg}".strip())
    return {"ok": True, "lineas": lineas, "total": len(lineas)}


CUSTOM_TOOLS.append({
    "name": "nas_espacio",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {},
    "description": "Cuánto disco queda en el NAS, por volumen, con un aviso si "
                   "alguno está lleno. Úsala SIEMPRE que pregunte por el "
                   "espacio, el disco, si hay que borrar algo o por qué va lento.",
    "label": "Mirando el espacio del NAS",
    "handler": _nas_espacio_handler,
})

CUSTOM_TOOLS.append({
    "name": "nas_salud",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {},
    "description": "Estado del NAS: CPU, RAM, temperatura, modelo y cuánto "
                   "tiempo lleva encendido. Úsala cuando pregunte si el NAS "
                   "está bien, va lento o se calienta.",
    "label": "Mirando el NAS",
    "handler": _nas_salud_handler,
})

CUSTOM_TOOLS.append({
    "name": "nas_contenedores",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {},
    "description": "Qué contenedores hay en el Container Manager del NAS, con su "
                   "estado y si están corriendo. Úsala siempre que pregunte por "
                   "los contenedores, Docker o los servicios del NAS.",
    "label": "Mirando los contenedores",
    "handler": _nas_contenedores_handler,
})

CUSTOM_TOOLS.append({
    "name": "nas_registro",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {"lineas": "int opcional, cuántas (defecto 60)"},
    "description": "Últimas líneas del registro del NAS. Úsala si preguntan por "
                   "un error del NAS, un fallo de un servicio o del registro.",
    "label": "Leyendo el registro del NAS",
    "handler": _nas_logs_handler,
})
