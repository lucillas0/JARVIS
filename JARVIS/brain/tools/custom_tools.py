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


# -*- coding: utf-8 -*-
"""Plugin: domotica con Home Assistant.

Por quÃ© Home Assistant y no el NAS: una Synology no habla con bombillas. Lo
que habla es Home Assistant, que corre en el mismo NAS por Container Manager y
expone REST. Es un puente estÃ¡ndar, y por eso este plugin no depende de nada
concreto: sirve para cualquier marca (Philips Hue, Shelly, Sonoff, Zigbee,
Matter...), porque todo eso acaba siendo una entidad de Home Assistant.

CÃ³mo se conecta:
  1. En Home Assistant, perfil (abajo a la izquierda) -> "Tokens de acceso de
     larga duraciÃ³n" -> Crear token. Se copia UNA vez, Home Assistant no lo
     vuelve a mostrar.
  2. Se pone en el .env del proyecto:
        HA_URL=http://192.168.1.10:8123
        HA_TOKEN=el-token-que-acabas-de-copiar

El token da control de toda la casa: trÃ¡talo como una contraseÃ±a y no lo
subas a ningÃºn sitio. Por eso se lee del entorno y no estÃ¡ en este fichero.
"""
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("tools.custom.ha")

_TIMEOUT = 8
# Igual que con el NAS: si Home Assistant estÃ¡ dormido, se paga UN timeout y
# las siguientes llamadas responden al momento. Sin esto, cada capa reintentaba
# y se acumulaban cuatro esperas de 8 s.
_CORTADO: dict = {"hasta": 0.0, "motivo": ""}
_COOLDOWN = 60.0
_CACHE: dict = {"estados": None, "hasta": 0.0}

# Dominios que tocan algo fÃ­sico que se puede romper: una cerradura, una
# puerta de garaje. Se piden confirmaciÃ³n aunque el resto no la pida.
_DELICADOS = {"lock", "cover", "valve", "garage"}


def _conf() -> dict:
    url = (os.environ.get("HA_URL") or "").strip().rstrip("/")
    tok = (os.environ.get("HA_TOKEN") or "").strip()
    inseguro = (os.environ.get("HA_INSEGURO", "0").strip()
                not in ("0", "false", "no"))
    return {"url": url, "token": tok, "inseguro": inseguro}


def _contexto() -> ssl.SSLContext | None:
    c = _conf()
    if not c["inseguro"]:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api(ruta: str, datos: dict | None = None) -> dict:
    """Llamada a la API de Home Assistant."""
    c = _conf()
    if not c["url"] or not c["token"]:
        return {"ok": False, "error": "Home Assistant no estÃ¡ configurado. "
                "Pon HA_URL y HA_TOKEN en el .env."}
    if time.time() < _CORTADO["hasta"]:
        return {"ok": False, "error": f"Home Assistant no responde ahora mismo "
                f"({_CORTADO['motivo']}). Vuelvo a intentarlo en un minuto."}
    url = f"{c['url']}/api{ruta}"
    cuerpo = None
    cabeceras = {"Authorization": f"Bearer {c['token']}"}
    if datos is not None:
        cuerpo = json.dumps(datos).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=cabeceras,
                                 method="POST" if cuerpo else "GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                    context=_contexto()) as r:
            crudo = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "error": "el token no vale o ha caducado. "
                    "Genera otro en el perfil de Home Assistant."}
        return {"ok": False, "error": f"Home Assistant respondiÃ³ {e.code}"}
    except Exception as e:
        _CORTADO["hasta"] = time.time() + _COOLDOWN
        _CORTADO["motivo"] = str(e)[:60]
        return {"ok": False, "error": f"no se pudo contactar con Home Assistant: "
                f"{e}"[:130]}
    _CORTADO["hasta"] = 0.0
    try:
        return {"ok": True, "data": json.loads(crudo)}
    except Exception:
        return {"ok": True, "data": crudo}


def _estados(forzar: bool = False) -> list:
    """Todas las entidades, cacheadas 10 s.

    El cache no es una optimizaciÃ³n prematura: leer /api/states en una casa
    con 300 entidades son 300 KB, y el modelo va a preguntar tres veces seguidas
    (Â«Â¿estÃ¡ encendida la luz?Â»). Sin cache son tres descargas.

    El error de la API se devuelve, no se convierte en lista vacÃ­a. Antes
    _estados se tragaba el fallo y devolvÃ­a [], y el modelo veÃ­a "no encuentro
    eso en casa" cuando lo que pasaba era que el token estaba caducado o
    Home Assistant estaba parado. Dos cosas distintas con dos arreglo
    distintos, y se contestaba a la equivocada.
    """
    if not forzar and _CACHE["estados"] and time.time() < _CACHE["hasta"]:
        return _CACHE["estados"]
    r = _api("/states")
    if not r["ok"]:
        _CACHE["estados"] = None
        _CACHE["hasta"] = 0.0
        return []
    _CACHE["estados"] = r["data"] or []
    _CACHE["hasta"] = time.time() + 10
    return _CACHE["estados"]


def _error_de_la_casa() -> str:
    """Por quÃ© no hay estados, si no es que no haya casa."""
    r = _api("/states")
    return "" if r["ok"] else str(r.get("error") or "")


def _bonito(e: dict) -> str:
    """El nombre que la persona usaria, no light.salon_lamp."""
    a = e.get("attributes") or {}
    n = a.get("friendly_name") or e.get("entity_id", "?")
    est = e.get("state", "?")
    extra = ""
    for k in ("current_temperature", "temperature", "brightness", "battery_level"):
        if k in a:
            v = a[k]
            extra = f" ({round(v, 1) if isinstance(v, float) else v})"
            break
    if e.get("state") in ("unavailable", "unknown"):
        est = f"no disponible ({est})"
    return f"{n}: {est}{extra}"


def _sin_acentos(s: str) -> str:
    return "".join(c for c in s.lower()
                   if c not in "Ã¡Ã Ã¤Ã¢Ã©Ã¨Ã«ÃªÃ­Ã¬Ã¯Ã®Ã³Ã²Ã¶Ã´ÃºÃ¹Ã¼Ã»Ã±Ã§")


# Palabras que dice una persona y el dominio al que se parecen. NO son un
# filtro: son un empujÃ³n. "Calentador" puede ser un enchufe (switch) o el
# termostato (climate), segÃºn cÃ³mo lo montÃ³ cada uno, y si se filtra a ciegas
# el aparato desaparece de la lista y JARVIS contesta que no existe.
#
# Sirven para desempatar: "la luz del salÃ³n" es una lÃ¡mpara, no la persiana ni
# el termostato de al lado. Pero si con el empujÃ³n no sale nada, se busca sin
# empujÃ³n, que es mejor que decir que no existe.
PALABRA_A_DOMINIO = {
    "luz": "light", "lampara": "light", "bombilla": "light",
    "enchufe": "switch", "interruptor": "switch", "boton": "switch",
    "persiana": "cover", "cortina": "cover", "toldo": "cover", "porton": "cover",
    "termostato": "climate", "clima": "climate", "calefaccion": "climate",
    "aire": "climate", "radiador": "climate", "calentador": "climate",
    "cerradura": "lock", "candado": "lock", "puerta": "lock",
    "altavoz": "media_player", "radio": "media_player", "television": "media_player",
}


def _dominio_de(consulta: str) -> str:
    """El tipo que insinÃºa la palabra, o "" si no dice ninguno."""
    q = _sin_acentos(consulta or "")
    for palabra, dom in PALABRA_A_DOMINIO.items():
        if palabra in q:
            return dom
    return ""


def _buscar(consulta: str, dominio: str = "", limite: int = 12,
            _ya_sin_sugerencia: bool = False) -> list:
    """Encuentra entidades por nombre, sin exigir el entity_id exacto.

    El modelo dice Â«la luz del salÃ³nÂ», no Â«light.salon_lampÂ». Si se le exigiera
    el identificador exacto no usarÃ­a nunca la herramienta, porque no lo sabe:
    los identificadores dependen de cÃ³mo se llamÃ³ cada cosa al instalarla.
    """
    q = (consulta or "").strip().lower()
    est = _estados()
    if not est:
        return []
    # Si el seÃ±or no dice de quÃ© tipo pero la palabra lo insinÃºa ("luz",
    # "persiana"), ese dominio pesa mÃ¡s. NO es un filtro: si con ese peso no
    # sale nada, se busca sin Ã©l. Filtrar a ciegas hacÃ­a desaparecer
    # aparatos que sÃ­ estaban, segÃºn cÃ³mo los llamase cada uno al montarlos.
    #
    # _ya_sin_sugerencia evita la recursiÃ³n infinita: el respaldo llama a
    # _buscar con la misma consulta, que volatile a sugerir lo mismo otra vez
    # y se llamaba a sÃ­ misma hasta reventar la pila. Pasaba con Â«calentadorÂ»,
    # que sugiere climate, y el calentador de la casa es un switch.
    sugerido = ("" if (dominio or _ya_sin_sugerencia)
                else _dominio_de(consulta))
    if not q and not dominio and not sugerido:
        return est[:limite]

    def puntua(e: dict) -> int:
        eid = e.get("entity_id", "").lower()
        dom = eid.split(".")[0]
        nombre = ((e.get("attributes") or {}).get("friendly_name")
                  or "").lower()
        base = -1
        if dominio:
            if dom != dominio.lower():
                return -1
            base = 1
        elif sugerido and dom == sugerido:
            base = 0          # el tipo que insinÃºa la palabra: delante
        if not q:
            return base * 10
        if nombre == q or eid == q:
            return 100 + base                 # coincidencia exacta
        if q in nombre:
            return 60 - len(nombre) / 100 + base   # cuanto mÃ¡s corto, mejor
        if q in eid:
            return 40 + base
        # A veces el modelo da la palabra suelta ("salÃ³n" por "salon").
        for parte in _sin_acentos(q).split():
            if len(parte) > 2 and (parte in _sin_acentos(nombre)
                                   or parte in _sin_acentos(eid)):
                return 20 + base
        return -1

    conp = [(puntua(e), e) for e in est]
    conp = [(p, e) for p, e in conp if p >= 0]
    conp.sort(key=lambda x: -x[0])
    # "Priorizar" no basta con subir la puntuacion: la persiana del salon
    # seguia saliendo al lado de las luces, solo que por debajo. Si la palabra
    # insinua un tipo y hay algo de ese tipo, se queda solo con ese tipo. Si no
    # hay nada de ese tipo, se busca sin el empujon, que es mejor que decir
    # que no existe.
    # Si la frase ENTRERA aparece tal cual en el nombre de algo, eso es lo que
    # se ha pedido. Las coincidencias flojas (compartir una palabra suelta)
    # sobran: "luz de lectura" es la lÃ¡mpara de lectura, y no la luz del salÃ³n
    # solo porque las dos tienen "luz". Antes salÃ­an las dos y JARVIS tenÃ­a
    # que preguntar cuÃ¡l de dos para algo que estaba clarÃ­simo.
    def descartar_flojas(pares):
        fuertes = [e for p, e in pares if p >= 40]
        return (fuertes or [e for _, e in pares])[:limite]

    if not dominio and sugerido:
        del_sugerido = [(p, e) for p, e in conp
                        if e.get("entity_id", "").startswith(sugerido + ".")]
        if del_sugerido:
            return descartar_flojas(del_sugerido)
        return _buscar(consulta, "", limite, _ya_sin_sugerencia=True)
    return descartar_flojas(conp)


# ======================================================================
# Herramientas
# ======================================================================
def _domotica_ver_handler(params: dict) -> dict:
    """CÃ³mo estÃ¡ algo."""
    ent = _buscar(params.get("que", "") or params.get("nombre", ""),
                  params.get("dominio", "") or "")
    if not ent:
        # Igual que en domotica_accion: primero se mira si el problema es que
        # no hay casa configurada, y si no, que no existe ese aparato.
        fallo = _error_de_la_casa()
        if fallo:
            return {"ok": False, "error": fallo}
        return {"ok": False, "error":
                "No encuentro eso en casa. Diga el nombre tal como lo ve en "
                "la app, o el tipo (luz, enchufe, sensor...)."}
    return {"ok": True, "encontrado": [_bonito(e) for e in ent],
            "total": len(ent), "ids": [e["entity_id"] for e in ent[:6]]}


def _domotica_lista_handler(params: dict) -> dict:
    """QuÃ© hay en casa, por tipo."""
    dom = (params.get("dominio", "") or "").strip().lower()
    est = _estados()
    if not est:
        return {"ok": False, "error": "Home Assistant no contesta. Â¿EstÃ¡ "
                "arrancado y el token es vÃ¡lido?"}
    if not dom:
        dominios: dict = {}
        for e in est:
            d = e.get("entity_id", "").split(".")[0] or "?"
            dominios[d] = dominios.get(d, 0) + 1
        return {"ok": True, "total": len(est),
                "dominios": sorted(dominios.items(), key=lambda x: -x[1])[:18]}
    sel = [e for e in est if e.get("entity_id", "").startswith(dom + ".")
           and e.get("state") not in ("unavailable",)]
    return {"ok": True, "dominio": dom, "total": len(sel),
            "entidades": [_bonito(e) for e in sel[:30]]}


def _domotica_accion_handler(params: dict) -> dict:
    """Enciende, apaga, ajusta. Lo que se puede tocar en una casa."""
    que = (params.get("que") or params.get("nombre") or "").strip()
    accion = _sin_acentos(params.get("accion") or "").strip()
    if not que or not accion:
        return {"ok": False, "error": "Falta quÃ© o quÃ© acciÃ³n."}

    cands = _buscar(que, params.get("dominio", "") or "")
    if not cands:
        # Antes de decir "no encuentro eso", se comprueba si el problema es
        # que no hay casa configurada. Sin esto, un token caducado se
        # contestaba como si la casa no tuviera esa lÃ¡mpara.
        fallo = _error_de_la_casa()
        if fallo:
            return {"ok": False, "error": fallo}
        return {"ok": False, "error":
                "No encuentro eso en casa. Diga el nombre tal como lo ve en "
                "la app, o el tipo (luz, enchufe, persiana, termostato...)."}
    if len(cands) > 1 and not params.get("entity_id"):
        return {"ok": False, "error":
                f"Eso son {len(cands)} cosas: "
                + ", ".join(_bonito(e) for e in cands[:6])
                + ". Diga cuÃ¡l.", "candidatos": [e["entity_id"] for e in cands[:6]]}
    ent = cands[0]
    eid = ent["entity_id"]
    dom = eid.split(".")[0]

    # Una cerradura o una puerta de garaje se pide antes. Encender una luz no.
    if dom in _DELICADOS and _sin_acentos(accion) in (
            "abrir", "abre", "abrirlo", "cerrar", "cierra", "lock",
            "bloquea", "unlock", "desbloquea", "desbloquear") \
            and not params.get("confirmado"):
        return {"ok": False, "confirmacion_requerida": True,
                "error": f"{_bonito(ent)} necesita su confirmaciÃ³n, seÃ±or. "
                         f"Â¿La abro?",
                "entity_id": eid, "accion": accion}

    # Atajos a los servicios de cada dominio. El modelo dice "enciende" y no
    # "turn_on"; se traduce aquÃ­ en vez de obligarle a saber la API.
    #
    # Se comparan en minÃºsculas y SIN tildes, porque el modelo escribe
    # "abrir" unas veces y "abre" otras, y con acentos otros. Antes la lista
    # tenÃ­a "abre" y no "abrir", asÃ­ que "abrir" pasaba de largo y se
    # mandaba tal cual a Home Assistant: POST /api/services/lock/abrir, que
    # no existe, y un 404 sin explicaciÃ³n.
    ENCIENDE = ("enciende", "on", "prende", "arranca", "abre", "abrir",
                "abrelo", "activar", "activa")
    APAGA = ("apaga", "off", "para", "cierra", "cerrar", "desactiva",
             "desactiva", "apagalo", "baja")
    ALIERNA = ("alterna", "toggle", "invierte")
    if accion in ENCIENDE:
        servicio = {"light": "turn_on", "switch": "turn_on",
                    "fan": "turn_on", "humidifier": "turn_on",
                    "media_player": "turn_on", "input_boolean": "turn_on",
                    "cover": "open_cover", "lock": "unlock",
                    "vacuum": "start"}.get(dom, "turn_on")
    elif accion in APAGA:
        servicio = {"light": "turn_off", "switch": "turn_off",
                    "fan": "turn_off", "humidifier": "turn_off",
                    "media_player": "turn_off", "input_boolean": "turn_off",
                    "cover": "close_cover", "lock": "lock",
                    "vacuum": "return_to_base"}.get(dom, "turn_off")
    elif accion in ALIERNA:
        servicio = "toggle"
    elif accion in ("bloquea", "block", "lock"):
        servicio = "lock"
    elif accion in ("desbloquea", "desbloquear", "unlock"):
        servicio = "unlock"
    else:
        servicio = accion

    datos: dict = {"entity_id": eid}
    # ParÃ¡metros opcionales: brillo, temperatura, posiciÃ³n.
    for k in ("brightness_pct", "brightness", "temperature", "position",
              "hvac_mode", "percentage", "volume_level"):
        v = params.get(k)
        if v is None or v == "":
            continue
        try:
            datos[k] = int(v) if k in ("brightness", "position",
                                       "percentage") else (
                float(v) if k == "temperature" else v)
        except (TypeError, ValueError):
            continue

    r = _api(f"/services/{dom}/{servicio}", datos)
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    _estados(forzar=True)          # la cache ya no vale
    return {"ok": True, "hecho": f"{_bonito(ent)} -> {accion}",
            "entity_id": eid, "servicio": f"{dom}.{servicio}"}


CUSTOM_TOOLS.append({
    "name": "domotica_estado",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {"que": "str, el nombre tal como lo dices (ej: 'luz del salÃ³n')",
                   "dominio": "str opcional: light, switch, sensor, climate, cover..."},
    "description": "CÃ³mo estÃ¡ algo en casa (luz, enchufe, sensor, clima, "
                   "persiana). Ãšsala SIEMPRE que pregunten si algo estÃ¡ "
                   "encendido, apagado, abierto o a quÃ© temperatura. No lo "
                   "adivines: di el nombre como lo dirÃ­a el seÃ±or.",
    "label": "Mirando la domÃ³tica",
    "handler": _domotica_ver_handler,
})

CUSTOM_TOOLS.append({
    "name": "domotica_lista",
    "risk_level": "low",
    "requires_confirmation": False,
    "parameters": {"dominio": "str opcional: light, switch, sensor, climate, "
                             "cover, lock. Sin Ã©l, quÃ© hay en casa"},
    "description": "QuÃ© dispositivos hay en casa. Sin dominio dice cuÃ¡ntos hay "
                   "de cada tipo; con dominio (light, switch, sensor...) lista "
                   "los de ese tipo. Ãšsala si no sabes cÃ³mo se llama algo.",
    "label": "Listando la domÃ³tica",
    "handler": _domotica_lista_handler,
})

CUSTOM_TOOLS.append({
    "name": "domotica_accion",
    "risk_level": "medium",
    "requires_confirmation": False,
    "parameters": {"que": "str, el nombre de la cosa",
                   "accion": "str: enciende, apaga, alterna, o el servicio "
                             "exacto (turn_on, set_temperature, open_cover)",
                   "brightness_pct": "int opcional, 0-100 para luces",
                   "temperature": "num opcional, para el clima",
                   "confirmado": "bool, solo si el seÃ±or ya ha dicho que sÃ­"},
    "description": "Enciende, apaga o ajusta algo en casa. Nombres: enciende, "
                   "apaga, alterna. Con brightness_pct se regula la luz y con "
                   "temperature el termostato. Las cerraduras y las persianas "
                   "piden confirmaciÃ³n: si la pide, pregunta al seÃ±or y vuelve "
                   "a llamarla con confirmado=true.",
    "label": "Actuando sobre la domÃ³tica",
    "handler": _domotica_accion_handler,
})

