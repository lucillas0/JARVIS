"""Autorizaciones recordadas: qué se puede hacer sin volver a preguntar.

**Por defecto NO se recuerda nada.** `once` no deja rastro: se autorizó una vez
y a la siguiente vuelve a preguntar, igual que ahora. Esto es a propósito: un
sistema que "acuerda" por su cuenta que puede borrar cosas es un sistema que
un día se borra a sí mismo.

Lo que sí existe, y solo si el señor lo pide expresamente:

  once     esta vez y nada más (no se guarda nada)
  session  una categoría acotada, hasta que JARVIS se cierre
  until    una categoría acotada, hasta una hora concreta
  reject   está denegado: se vuelve a preguntar y la respuesta por defecto
           es no

Tres reglas que no se negocian:

1. **El alcance es exacto y no se amplía solo.** Una autorización para un
   fichero no vale para su carpeta. Ampliar el alcance es un cambio de
   política: exige nueva aprobación, siempre.

2. **Nada de autorización permanente y global** para shell, filesystem,
   Docker o instalaciones. Esos, si alguna vez se quisieran recordar, sería
   con caducidad y categoría; mientras tanto, `session`/`until` para ellos no
   se conceden (ver `PERMITEN_RECORDAR`).

3. **Ante la duda, se vuelve a preguntar.** Si el alcance no encaja
   exactamente, no hay autorización. Fallar hacia "preguntar" es un
   inconveniente; fallar hacia "no preguntar" es una pérdida de datos.

Cada autorización guarda quién la concedió, qué cubría, cuándo se creó, cuándo
caduca, si está revocada y cuántas veces se usó. Y todas van al registro de
auditoría, como todo lo demás.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Optional

from memory import sqlite_store as db

log = logging.getLogger("brain.authz")

_candado = threading.RLock()

ONCE = "once"
SESION = "session"
HASTA = "until"
RECHAZO = "reject"
# Alias con el nombre que usa OpenCode, para no tener dos vocabularios.
REJECT = RECHAZO
DECISIONES = (ONCE, SESION, HASTA, RECHAZO)

# `session` y `until` son las dos que se pueden recordar; `once` y `reject`
# no dejan rastro por construccion.
DECISIONES_RECORDABLES = (SESION, HASTA)

# Lo que se puede recordar. Lo que está a False nunca se autoriza "para
# siempre" ni por sesión, aunque se pida: son acciones de las que no hay vuelta
# atrás o que tocan el equipo entero. Se comparan por palabra clave, no por
# nombre exacto de herramienta: si el criterio fuera el nombre, bastaría con
# llamar "instalar_paquete" en vez de "instalar_dependencia" para esquivarlo.
PALABRAS_CRITICAS = ("instalar", "desinstalar", "uninstall", "docker",
                     "shell", "bash", "cmd", "powershell", "borrar", "delete",
                     "remove", "rm", "formatear", "reboot", "reiniciar",
                     "restart", "shutdown", "apagar", "system", "firewall",
                     "registro", "registry", "sudo", "admin", "systemd")

# Palabras que sí se pueden recordar (lectura y trabajo sin efectos).
PALABRAS_PERMITIDAS = ("leer", "read", "listar", "buscar", "search", "editar",
                       "edit", "escribir", "write", "test", "diff", "status",
                       "analiza", "log")

# Se mantiene el mapa por compatibilidad con quien lo use como diccionario.
PERMITEN_RECORDAR = {
    "leer": True, "escribir": True, "editar": True, "listar": True,
    "buscar": True, "ejecutar_tests": True, "git_diff": True, "git_status": True,
    "instalar": False, "desinstalar": False, "docker": False, "shell": False,
    "borrar": False, "system": False, "reiniciar": False,
}


def _es_critica(tipo: str) -> str:
    """Devuelve la palabra crítica que aparece, o "" si no es crítica.

    Ante la duda manda el lado prudente: si aparece una palabra crítica, es
    crítica; y si no encaja con ninguna de las dos listas, también (no se
    autoconcede).
    """
    t = _normaliza(tipo)
    if not t:
        return "sin_descripcion"
    for p in PALABRAS_CRITICAS:
        if p in t:
            return p
    for p in PALABRAS_PERMITIDAS:
        if p in t:
            return ""
    return "desconocida"

# Alcance por defecto cuando se concede una autorización de tipo 'session' o
# 'until': la CATEGORÍA exacta, nunca un comodín.
def alcance_por_defecto(tipo_operacion: str) -> str:
    return f"tipo:{tipo_operacion}"


def _fila(f) -> dict:
    d = dict(f)
    for campo in ("recursos", "restricciones"):
        if isinstance(d.get(campo), str) and d[campo]:
            try:
                d[campo] = json.loads(d[campo])
            except (ValueError, TypeError):
                d[campo] = [] if campo == "recursos" else {}
    return d


# --------------------------------------------------------------------------- #
def concede(alcance: str, decision: str = SESION, *, tipo_operacion: str = "",
            recursos: Optional[list] = None, restricciones: Optional[dict] = None,
            segundos: int = 0, session_id: str = "", actor: str = "",
            caducidad_unix: float = 0) -> dict:
    """Registra una autorización. Devuelve {ok, id, ...} o {ok:false, error}."""
    if decision not in DECISIONES:
        return {"ok": False, "error": f"Decisión no válida: {decision}"}
    if not (alcance or "").strip():
        return {"ok": False, "error": "Falta el alcance: no se autoriza 'a algo'."}

    ahora = time.time()
    expira = 0.0
    if decision == SESION:
        # La sesión vive mientras el proceso viva: se marca con la sesión, no
        # con un reloj. Si el JARVIS se cierra, caduca sola.
        expira = 0.0
    elif decision == HASTA:
        # El suelo es 1 segundo, no 60: si alguien pide una autorización de
        # diez segundos, quedarse con un minuto es más largo de lo pedido.
        expira = float(caducidad_unix or (ahora + max(1, int(segundos))))
    elif decision in (ONCE, RECHAZO):
        # Estas no se guardan: por definición no dejan rastro.
        _audita(tipo_operacion or alcance, decision, actor,
                {"alcance": alcance, "recursos": recursos or []})
        return {"ok": True, "decision": decision, "guardado": False,
                "nota": "esta decisión no se recuerda: se vuelve a preguntar"}

    # Comprobación de qué se puede recordar.
    tipo = (tipo_operacion or alcance or "")
    critica = _es_critica(tipo)
    if critica:
        _audita(tipo, "rechazada_para_recordar", actor,
                {"motivo": critica, "alcance": alcance})
        return {"ok": False,
                "error": f"«{tipo}» toca «{critica}»: no se autoriza para "
                         f"recordar. Preguntaré cada vez."}

    registro = {
        "id": f"authz-{int(ahora * 1000) % 10_000_000:07d}",
        "alcance": alcance[:200],
        "tipo_operacion": (tipo_operacion or alcance)[:120],
        "recursos": json.dumps(recursos or [], ensure_ascii=False),
        "restricciones": json.dumps(restricciones or {}, ensure_ascii=False),
        "decision": decision, "creada": ahora, "expira": expira,
        "session_id": session_id or "", "creada_por": actor or "",
    }
    with _candado:
        with db.get_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO autorizaciones (id, alcance, "
                "tipo_operacion, recursos, restricciones, decision, creada, "
                "expira, revocada, session_id, creada_por) "
                "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                (registro["id"], registro["alcance"], registro["tipo_operacion"],
                 registro["recursos"], registro["restricciones"], decision, ahora,
                 expira, registro["session_id"], registro["creada_por"]))
            c.commit()
    _audita(tipo, f"autorizada:{decision}", actor,
            {"alcance": alcance, "recursos": recursos or [], "id": registro["id"]})
    return {"ok": True, "id": registro["id"], "decision": decision,
            "expira": expira or None, "guardado": True}


def revoca(alcance: str = "", authz_id: str = "", actor: str = "") -> dict:
    """Revoca. Efecto inmediato: la siguiente comprobación ya no la ve."""
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            if authz_id:
                c.execute("UPDATE autorizaciones SET revocada=1, revocada_at=?, "
                          "revocada_por=? WHERE id=?", (ahora, actor, authz_id))
            elif alcance:
                # Solo el alcance EXACTO. "read" no revoca "read_all".
                c.execute("UPDATE autorizaciones SET revocada=1, revocada_at=?, "
                          "revocada_por=? WHERE alcance=? AND revocada=0",
                          (ahora, actor, alcance))
            else:
                c.execute("UPDATE autorizaciones SET revocada=1, revocada_at=?, "
                          "revocada_por=? WHERE revocada=0", (ahora, actor))
            c.commit()
            n = c.execute("SELECT changes()").fetchone()[0]
    _audita(alcance or authz_id or "*", "revocada", actor, {"cuantas": n})
    return {"ok": True, "revocadas": n, "inmediato": True}


def esta_autorizado(alcance: str, session_id: str = "") -> dict:
    """¿Hay una autorización vigente que cubra EXACTAMENTE este alcance?

    Devuelve el detalle de cuál es, para poder decir "te lo concediste hasta
    las 15:30" en vez de un sí mudo.
    """
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM autorizaciones WHERE alcance=? AND revocada=0 "
                "ORDER BY creada DESC", (alcance,)).fetchall()
    validas = []
    for f in filas:
        d = _fila(f)
        if d["decision"] == RECHAZO:
            return {"ok": False, "motivo": "denegada", "detalle": d}
        if d["decision"] == HASTA and d["expira"] and d["expira"] < ahora:
            continue       # caducada: no cuenta
        if d["decision"] == SESION and session_id and d["session_id"] and \
                d["session_id"] != session_id:
            # Autorización de OTRA sesión: no vale aquí. Es lo que impide que
            # sobreviva a un reinicio de JARVIS.
            continue
        validas.append(d)
    if not validas:
        return {"ok": False, "motivo": "sin autorizacion vigente"}
    mejor = validas[0]
    with _candado:
        with db.get_conn() as c:
            c.execute("UPDATE autorizaciones SET veces_usada = veces_usada + 1 "
                      "WHERE id=?", (mejor["id"],))
            c.commit()
    return {"ok": True, "detalle": mejor, "decision": mejor["decision"],
            "caduca": mejor["expira"] or None}


def limpiar_caducadas() -> int:
    """Las `until` que ya pasaron y las de sesiones antiguas."""
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM autorizaciones WHERE decision='until' "
                "AND expira > 0 AND expira < ?", (ahora,)).fetchone()[0]
            c.execute("UPDATE autorizaciones SET revocada=1, revocada_at=? "
                      "WHERE decision='until' AND expira > 0 AND expira < ?",
                      (ahora, ahora))
            c.commit()
    return n


def activas(session_id: str = "") -> list[dict]:
    """Las autorizaciones vigentes, para verlas y revocarlas desde el panel."""
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM autorizaciones WHERE revocada=0 ORDER BY creada DESC"
            ).fetchall()
    salida = []
    for f in filas:
        d = _fila(f)
        caducada = bool(d["expira"] and d["expira"] < ahora)
        if caducada:
            continue
        if d["decision"] == SESION and session_id and d["session_id"] and \
                d["session_id"] != session_id:
            continue
        d["caduca_en_s"] = int(d["expira"] - ahora) if d["expira"] else None
        salida.append(d)
    return salida


def estado() -> dict:
    with _candado:
        with db.get_conn() as c:
            total = c.execute("SELECT COUNT(*) FROM autorizaciones").fetchone()[0]
            revocadas = c.execute(
                "SELECT COUNT(*) FROM autorizaciones WHERE revocada=1").fetchone()[0]
    return {"total": total, "revocadas": revocadas,
            "activas": len(activas()), "caducadas": limpiar_caducadas(),
            "politica": {
                "por_defecto": "once (no se recuerda nada)",
                "recordables": list(DECISIONES_RECORDABLES),
                "nunca_recordable": [k for k, v in PERMITEN_RECORDAR.items()
                                     if not v],
            }}


def _audita(accion: str, resultado: str, actor: str, payload: dict) -> None:
    try:
        db.add_audit(f"authz.{accion}", payload, "low", None, resultado, actor or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude auditar la autorización: %s", exc)


# --------------------------------------------------------------------------- #
def _normaliza(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip().lower())


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    print("AUTORIZACIONES · prueba rápida")
    print(" once   :", concede("leer:notas", ONCE))
    print(" session:", concede("leer:notas", SESION, tipo_operacion="leer",
                              session_id="s1"))
    print(" consulta:", esta_autorizado("leer:notas", "s1")["ok"])
    print(" otro    :", esta_autorizado("leer:otro", "s1")["ok"])
    print(" otra ses:", esta_autorizado("leer:notas", "s2")["ok"])
    print(" borrar  :", concede("borrar_todo", SESION, tipo_operacion="borrar_todo"))
    print(" revocar :", revoca(alcance="leer:notas"))
    print(" tras rev:", esta_autorizado("leer:notas", "s1")["ok"])