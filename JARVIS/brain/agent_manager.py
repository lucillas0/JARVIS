"""Agent Manager: quién hace qué, en qué orden y cuántos a la vez.

**No es un segundo Task Manager.** El Task Manager de
`orchestrator/manager.py` sigue siendo quien lanza las misiones y habla con
OpenCode. Este módulo hace lo que aquel no hacía: llevar la **cola de trabajos
de agentes** con prioridad, decidir cuántos pueden correr a la vez, y acordarse
de qué se quedó a medias al reiniciar.

La diferencia, dicha sin rodeos:

  - Task Manager  = una misión y su sesión de OpenCode.
  - Agent Manager = el reparto del trabajo entre agentes y su cola.

Los agentes que gestiona son los REALES que el sistema detecta en el equipo
(`apps.py` mira procesos de verdad) más un agente interno de JARVIS. No se
inventa ninguno: si un agente no está en la máquina, figura como no disponible.

Estados, y lo que significa cada uno:

  registrado   el sistema lo conoce (puede no estar instalado)
  disponible   instalado y detectable
  ocupado      tiene un trabajo suyo ahora mismo
  bloqueado    lleva demasiado tiempo sin avanzar (no contesta)
  no disponible no está en este equipo

Un trabajo (`agent_jobs`) pasa por: en_cola → ejecutando → ok | fallo |
cancelado | atascado. Todo se guarda en SQLite, así que un reinicio no pierde
la cola ni los trabajos a medias.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Callable, Optional

from memory import sqlite_store as db

log = logging.getLogger("brain.agents")

_candado = threading.RLock()

# --------------------------------------------------------------------------- #
# Agentes
# --------------------------------------------------------------------------- #
# El agente interno: JARVIS ejecutando trabajo suyo (no una app externa).
AGENTE_JARVIS = "jarvis"

# Definición de los agentes. Los externos se descubren con apps.py, que mira
# procesos reales; aquí solo está su papel. `deteccion` dice contra qué proceso
# se comprueba que están vivos.
CATALOGO = {
    AGENTE_JARVIS: {"nombre": "JARVIS", "tipo": "interno",
                    "deteccion": (), "descripcion": "El propio JARVIS."},
    "agent_opencode": {"nombre": "OpenCode", "tipo": "externo",
                       "deteccion": ("opencode",),
                       "descripcion": "Agente de programación."},
    "integration_claude": {"nombre": "Claude / editor", "tipo": "externo",
                           "deteccion": ("code", "devenv"),
                           "descripcion": "Editor con agentes."},
    "agent_codex": {"nombre": "Codex", "tipo": "externo",
                    "deteccion": ("codex",), "descripcion": "Agente de programación."},
    "agent_cursor": {"nombre": "Cursor", "tipo": "externo",
                     "deteccion": ("cursor",), "descripcion": "Editor."},
    "agent_copilot": {"nombre": "Copilot", "tipo": "externo",
                      "deteccion": ("copilot",), "descripcion": "Agente de programación."},
}

# --------------------------------------------------------------------------- #
# Límites y prioridades
# --------------------------------------------------------------------------- #
# Prioridad: MAYOR número va ANTES. 100 es lo más urgente.
PRIORIDAD_URGENTE = 100
PRIORIDAD_ALTA = 75
PRIORIDAD_NORMAL = 50
PRIORIDAD_BAJA = 25
PRIORIDADES = (PRIORIDAD_URGENTE, PRIORIDAD_ALTA, PRIORIDAD_NORMAL, PRIORIDAD_BAJA)

# Tope por defecto de trabajos a la vez. El Resource Manager puede bajarlo
# cuando aprieta la memoria, pero nunca sube de aquí: ese es el límite que el
# señor puso, no una decisión automática.
MAXIMO_CONCURRENTE = 3

# Cuánto puede estar un trabajo sin avanzar antes de deemirlo atascado.
# Generoso a propósito: un agente de verdad puede estar pensando un rato sin
# decir nada, y declararlo atascado sería interrumpir trabajo bueno.
SEGUNDOS_SIN_HEARTBEAT = 900

ESTADOS_TRABAJO = ("en_cola", "ejecutando", "ok", "fallo", "cancelado", "atascado")

# Who callbacks (para tests y para ejecución real)
_emit: Optional[Callable] = None
_concurrency_hook: Optional[Callable[[], int]] = None


def conectar_bus(emit: Callable) -> None:
    global _emit
    _emit = emit


def limite_por_defecto() -> int:
    return MAXIMO_CONCURRENTE


# --------------------------------------------------------------------------- #
# Inventario
# --------------------------------------------------------------------------- #
def _detecta_vivos() -> set:
    """Nombres de proceso que hay ahora mismo. Datos reales, de apps.py."""
    try:
        import apps

        return {n.lower() for n in apps._procesos_vivos()}
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude detectar procesos: %s", exc)
        return set()


def inventario() -> list[dict]:
    """Todos los agentes conocidos y en qué estado están de verdad."""
    vivos = _detecta_vivos()
    trabajos = trabajos_activos()
    por_agente = {}
    for t in trabajos:
        por_agente.setdefault(t["agent_id"], []).append(t)

    salida = []
    for agent_id, ficha in CATALOGO.items():
        deteccion = ficha["deteccion"]
        if ficha["tipo"] == "interno":
            disponible = True          # JARVIS está porque esto está corriendo
        else:
            disponible = any(d in vivos for d in deteccion)
        suyos = por_agente.get(agent_id, [])
        if not disponible:
            estado = "no disponible"
        elif any(t["estado"] == "atascado" for t in suyos):
            estado = "bloqueado"
        elif suyos:
            estado = "ocupado"
        else:
            estado = "disponible"
        salida.append({
            "id": agent_id,
            "nombre": ficha["nombre"],
            "tipo": ficha["tipo"],
            "descripcion": ficha["descripcion"],
            "estado": estado,
            "registrado": True,
            "disponible": disponible,
            "trabajos": len(suyos),
            "trabajo_actual": (suyos[0]["id"] if suyos else ""),
        })
    return salida


def estado() -> dict:
    agentes = inventario()
    return {
        "agentes": agentes,
        "total": len(agentes),
        "disponibles": sum(1 for a in agentes if a["disponible"]),
        "ocupados": sum(1 for a in agentes if a["estado"] == "ocupado"),
        "bloqueados": sum(1 for a in agentes if a["estado"] == "bloqueado"),
        "no_disponibles": sum(1 for a in agentes if not a["disponible"]),
        "cola": resumen_cola(),
        "concurrencia": {
            "maximo_configurado": MAXIMO_CONCURRENTE,
            "en_vuelo": len(trabajos_activos()),
            "limite_efectivo": limite_efectivo(),
        },
    }


def limite_efectivo() -> int:
    """El tope real ahora mismo, después de que el Resource Manager opinara."""
    if _concurrency_hook is None:
        return MAXIMO_CONCURRENTE
    try:
        valor = int(_concurrency_hook())
    except Exception as exc:  # noqa: BLE001
        log.debug("límite dinámico no disponible: %s", exc)
        return MAXIMO_CONCURRENTE
    # Nunca por encima del tope que puso el señor.
    return max(1, min(MAXIMO_CONCURRENTE, valor))


def conectar_limite(hook: Callable[[], int]) -> None:
    """El Resource Manager se engancha aquí para poder bajar el tope."""
    global _concurrency_hook
    _concurrency_hook = hook


# --------------------------------------------------------------------------- #
# Cola
# --------------------------------------------------------------------------- #
def encolar(agent_id: str, payload: Optional[dict] = None, *,
            prioridad: int = PRIORIDAD_NORMAL, mission_id: str = "",
            task_id: str = "") -> dict:
    """Mete un trabajo en la cola."""
    if agent_id not in CATALOGO:
        return {"ok": False, "error": f"Agente desconocido: {agent_id}"}
    if not (0 <= int(prioridad) <= 100):
        return {"ok": False, "error": "La prioridad va de 0 a 100."}
    ahora = time.time()
    trabajo = {
        "id": "job-" + uuid.uuid4().hex[:10],
        "agent_id": agent_id,
        "mission_id": mission_id, "task_id": task_id,
        "prioridad": int(prioridad),
        "estado": "en_cola",
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "creado": ahora,
    }
    with _candado:
        with db.get_conn() as c:
            c.execute(
                "INSERT INTO agent_jobs (id, agent_id, mission_id, task_id, "
                "prioridad, estado, payload, creado) VALUES (?,?,?,?,?,?,?,?)",
                (trabajo["id"], agent_id, mission_id, task_id, int(prioridad),
                 "en_cola", trabajo["payload"], ahora))
            c.commit()
    _publica("agent.queued", trabajo)
    return {"ok": True, "id": trabajo["id"], "estado": "en_cola",
            "posicion": posicion(trabajo["id"])}


def posicion(job_id: str) -> int:
    """Sitio en la cola: 1 es el primero en salir."""
    with _candado:
        with db.get_conn() as c:
            trabajo = c.execute("SELECT prioridad, creado FROM agent_jobs "
                                "WHERE id=?", (job_id,)).fetchone()
            if trabajo is None:
                return 0
            antes = c.execute(
                "SELECT COUNT(*) FROM agent_jobs WHERE estado='en_cola' AND "
                "(prioridad > ? OR (prioridad = ? AND creado < ?))",
                (trabajo["prioridad"], trabajo["prioridad"],
                 trabajo["creado"])).fetchone()[0]
    return antes + 1


def cola(limite: int = 20) -> list[dict]:
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM agent_jobs WHERE estado IN ('en_cola','ejecutando') "
                "ORDER BY CASE estado WHEN 'ejecutando' THEN 0 ELSE 1 END, "
                "prioridad DESC, creado ASC LIMIT ?", (limite,)).fetchall()
    return [_fila(f) for f in filas]


def resumen_cola() -> dict:
    with _candado:
        with db.get_conn() as c:
            cuentas = {r[0]: r[1] for r in c.execute(
                "SELECT estado, COUNT(*) FROM agent_jobs GROUP BY estado").fetchall()}
    for e in ESTADOS_TRABAJO:
        cuentas.setdefault(e, 0)
    cuentas["pendientes"] = cuentas.get("en_cola", 0) + cuentas.get("ejecutando", 0)
    return cuentas


def trabajos_activos() -> list[dict]:
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM agent_jobs WHERE estado IN ('en_cola','ejecutando')"
            ).fetchall()
    return [_fila(f) for f in filas]


def obtener(job_id: str) -> Optional[dict]:
    with _candado:
        with db.get_conn() as c:
            fila = c.execute("SELECT * FROM agent_jobs WHERE id=?",
                             (job_id,)).fetchone()
    return _fila(fila) if fila else None


def _fila(f) -> dict:
    d = dict(f)
    if isinstance(d.get("payload"), str):
        try:
            d["payload"] = json.loads(d["payload"])
        except (ValueError, TypeError):
            d["payload"] = {}
    return d


def _actualizar(job_id: str, **campos) -> None:
    if not campos:
        return
    sets = ", ".join(f"{k}=?" for k in campos)
    with _candado:
        with db.get_conn() as c:
            c.execute(f"UPDATE agent_jobs SET {sets} WHERE id=?",
                      tuple(campos.values()) + (job_id,))
            c.commit()


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #
def siguiente(agente_id: str = "") -> Optional[dict]:
    """El primer trabajo de la cola, si se puede empezar ahora.

    Se llama cuando el agente queda libre. Devuelve el trabajo o None si la
    cola está vacía o si no caben más trabajos a la vez.
    """
    with _candado:
        en_vuelo = trabajos_activos()
        ejecutandose = [t for t in en_vuelo if t["estado"] == "ejecutando"]
        if len(ejecutandose) >= limite_efectivo():
            return None
        if ejecutandose:
            agentes_ocupados = {t["agent_id"] for t in ejecutandose}
            if agente_id and agente_id in agentes_ocupados:
                return None      # ese agente ya tiene uno
        cond = "estado='en_cola'"
        vals: list = []
        if agente_id:
            cond += " AND agent_id=?"
            vals.append(agente_id)
        with db.get_conn() as c:
            fila = c.execute(
                f"SELECT * FROM agent_jobs WHERE {cond} "
                f"ORDER BY prioridad DESC, creado ASC LIMIT 1", tuple(vals)
            ).fetchone()
        if fila is None:
            return None
        ahora = time.time()
        _actualizar(fila["id"], estado="ejecutando", inicia=ahora,
                    termina=0, heartbeat=ahora, intentos=int(fila["intentos"] or 0) + 1)
        trabajo = obtener(fila["id"])
    _publica("agent.started", trabajo or {})
    return trabajo


def terminar(job_id: str, ok: bool = True, resultado=None, error: str = "") -> dict:
    """Termina un trabajo y libera al agente."""
    trabajo = obtener(job_id)
    if trabajo is None:
        return {"ok": False, "error": "Ese trabajo no existe."}
    _actualizar(job_id,
                estado="ok" if ok else "fallo",
                resultado=json.dumps(resultado, ensure_ascii=False)[:2000]
                if resultado is not None else "",
                error=error[:400], termina=time.time(), heartbeat=0)
    # Al terminar, el agente puede cogir el siguiente de la cola.
    siguiente_trabajo = siguiente(trabajo["agent_id"])
    _publica("agent.finished" if ok else "agent.error",
             obtener(job_id) or {})
    return {"ok": True, "estado": "ok" if ok else "fallo",
            "siguiente": siguiente_trabajo["id"] if siguiente_trabajo else ""}


def heartbeat(job_id: str) -> dict:
    """El agente dice que sigue vivo. Sin esto, se le acabaría el tiempo."""
    trabajo = obtener(job_id)
    if trabajo is None or trabajo["estado"] != "ejecutando":
        return {"ok": False, "error": "Ese trabajo no está ejecutándose."}
    _actualizar(job_id, heartbeat=time.time())
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Cancelación
# --------------------------------------------------------------------------- #
def cancelar(job_id: str, motivo: str = "") -> dict:
    """Cancela un trabajo, esté en cola o en marcha.

    Un trabajo cancelado no vuelve a la cola: si se quería, se encola otro.
    Así una cancelación es definitiva y no se puede reviving sola.
    """
    trabajo = obtener(job_id)
    if trabajo is None:
        return {"ok": False, "error": "Ese trabajo no existe."}
    if trabajo["estado"] in ("ok", "fallo", "cancelado"):
        return {"ok": False,
                "error": f"Ese trabajo ya terminó ({trabajo['estado']})."}
    _actualizar(job_id, estado="cancelado", termina=time.time(), heartbeat=0,
                error=motivo[:400] or "cancelado por el usuario")
    # Si estaba corriendo, su agente queda libre y puede seguir con la cola.
    if trabajo["estado"] == "ejecutando":
        siguiente(trabajo["agent_id"])
    _publica("agent.cancelled", obtener(job_id) or {})
    return {"ok": True, "estado": "cancelado"}


def cancelar_mision(mission_id: str, motivo: str = "misión cancelada") -> dict:
    """Cancela todos los trabajos de una misión. La cancelación no se queda a medias."""
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT id FROM agent_jobs WHERE mission_id=? AND estado IN "
                "('en_cola','ejecutando')", (mission_id,)).fetchall()
    cancelados = []
    for (job_id,) in filas:
        r = cancelar(job_id, motivo)
        if r.get("ok"):
            cancelados.append(job_id)
    return {"ok": True, "cancelados": cancelados, "cuantos": len(cancelados)}


# --------------------------------------------------------------------------- #
# Bloqueos
# --------------------------------------------------------------------------- #
def detectar_atascados() -> list[dict]:
    """Trabajos que llevan demasiado sin decir nada.

    No se cancelan: se MARCAN. Que JARVIS no los mate por su cuenta es
    importante: un agente pensando no es un agente colgado, y matarlo sería
    tirar trabajo. Quien lo cancela es el señor, desde el centro de mando.
    """
    ahora = time.time()
    atascados = []
    for t in trabajos_activos():
        if t["estado"] != "ejecutando":
            continue
        ultimo = float(t.get("heartbeat") or t.get("inicia") or t["creado"])
        if ahora - ultimo > SEGUNDOS_SIN_HEARTBEAT:
            atascados.append({**t, "sin_heartbeat": round(ahora - ultimo)})
    for t in atascados:
        _actualizar(t["id"], estado="atascado")
    if atascados:
        for t in atascados:
            _publica("agent.stuck", t)
    return atascados


def recuperar_al_arrancar() -> dict:
    """Al arrancar, qué se quedó a medias.

    Lo que estaba EN COLA se queda: su cola sigue viva. Lo que estaba
    EJECUTANDO pasa a 'atascado', porque el proceso que lo hacía ya no está y
    no se puede dar por bueno. No se relanza solo: sería duplicar trabajo.
    """
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            en_ejecucion = [f["id"] for f in c.execute(
                "SELECT id FROM agent_jobs WHERE estado='ejecutando'").fetchall()]
            c.execute("UPDATE agent_jobs SET estado='atascado', termina=?, "
                      "heartbeat=0 WHERE estado='ejecutando'", (ahora,))
            c.commit()
    for job_id in en_ejecucion:
        _actualizar(job_id, error="interrumpido por un reinicio de JARVIS")
    return {"ok": True, "recuperados_en_cola": resumen_cola().get("en_cola", 0),
            "marcados_atascados": len(en_ejecucion),
            "nota": "los atascados NO se relanzan solos: se relanza si lo pides"}


# --------------------------------------------------------------------------- #
def historial(limite: int = 30) -> list[dict]:
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM agent_jobs ORDER BY creado DESC LIMIT ?",
                (max(1, min(200, limite)),)).fetchall()
    return [_fila(f) for f in filas]


def _publica(tipo: str, trabajo: dict) -> None:
    if _emit is None:
        return
    try:
        _emit({"type": tipo, "agent_id": trabajo.get("agent_id", ""),
               "job_id": trabajo.get("id", ""),
               "mission_id": trabajo.get("mission_id", ""),
               "task_id": trabajo.get("task_id", ""),
               "estado": trabajo.get("estado", "")})
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude publicar %s: %s", tipo, exc)