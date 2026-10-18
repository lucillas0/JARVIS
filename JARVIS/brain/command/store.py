"""Persistencia del centro de mando: notificaciones, incidentes, decisiones
y checkpoints.

Cuatro tablas, cuatro hechos reales:

  · notificacion  <- un evento del bus (permiso, pregunta, error, fin...)
  · incidente     <- un error de verdad (autocorrector, tarea fallida, agente)
  · decision      <- lo que decidió el señor (con su motivo y alternativas)
  · checkpoint    <- el estado de Git antes de tocar algo

Nada aquí inventa nada: si no hay hecho, no hay fila.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Optional

from memory import sqlite_store as db

log = logging.getLogger("brain.cmd.store")
_lock = threading.RLock()


def _nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:10]}"


def _fila(fila: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(fila) if fila is not None else None


def _todos(sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in db.get_conn().execute(sql, args).fetchall()]


# --------------------------------------------------------------------------- #
# Notificaciones
# --------------------------------------------------------------------------- #
def crear_notificacion(kind: str, title: str, body: str = "", category: str = "sistema",
                       task_id: str = "", dedupe_key: str = "",
                       ventana: float = 300.0) -> Optional[dict]:
    """Guarda una notificación. `dedupe_key` evita repetir la misma en la ventana.

    Devuelve la fila creada, o None si ya había una equivalente reciente.
    """
    ahora = time.time()
    if dedupe_key:
        with _lock:
            previa = db.get_conn().execute(
                "SELECT * FROM notifications WHERE category = ? AND title = ? "
                "AND dismissed_at IS NULL AND created_at > ? LIMIT 1",
                (category, title, ahora - ventana)).fetchone()
            if previa is not None:
                return None
    fila = {"id": _nuevo_id("ntf"), "kind": kind, "category": category,
            "title": title[:180], "body": body[:800], "task_id": task_id,
            "seen": 0, "dismissed_at": None, "muted": 0, "created_at": ahora}
    try:
        with _lock:
            db.get_conn().execute(
                "INSERT INTO notifications (id, kind, category, title, body, task_id,"
                " seen, dismissed_at, muted, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fila["id"], fila["kind"], fila["category"], fila["title"],
                 fila["body"], fila["task_id"], 0, None, 0, ahora))
            db.get_conn().commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo guardar la notificación: %s", exc)
        return None
    return fila


def listar_notificaciones(limite: int = 40, incluir_descartadas: bool = False) -> list[dict]:
    sql = "SELECT * FROM notifications"
    if not incluir_descartadas:
        sql += " WHERE dismissed_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ?"
    return _todos(sql, (int(limite),))


def contar_notificaciones() -> dict:
    ahora = time.time()
    try:
        fila = db.get_conn().execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN seen = 0 THEN 1 ELSE 0 END) AS sin_leer "
            "FROM notifications WHERE dismissed_at IS NULL AND muted = 0 AND created_at > ?",
            (ahora - 7 * 86400,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.debug("conteo de notificaciones: %s", exc)
        return {"total": 0, "sin_leer": 0}
    return {"total": int(fila["total"] or 0), "sin_leer": int(fila["sin_leer"] or 0)}


def marcar_notificacion(notif_id: str, accion: str) -> bool:
    """read | dismiss | mute (mute silencia la categoría entera)."""
    ahora = time.time()
    try:
        with _lock:
            conn = db.get_conn()
            if accion == "read":
                cur = conn.execute("UPDATE notifications SET seen = 1 WHERE id = ?", (notif_id,))
            elif accion == "dismiss":
                cur = conn.execute("UPDATE notifications SET dismissed_at = ? WHERE id = ?",
                                   (ahora, notif_id))
            elif accion == "mute":
                cur = conn.execute("UPDATE notifications SET muted = 1, dismissed_at = ? "
                                   "WHERE category = (SELECT category FROM notifications "
                                   "WHERE id = ?)", (ahora, notif_id))
            else:
                return False
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo actualizar la notificación: %s", exc)
        return False


def limpiar_notificaciones(dias: int = 30) -> int:
    limite = time.time() - dias * 86400
    try:
        with _lock:
            cur = db.get_conn().execute(
                "DELETE FROM notifications WHERE created_at < ?", (limite,))
            db.get_conn().commit()
            return int(cur.rowcount)
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo limpiar notificaciones: %s", exc)
        return 0


# --------------------------------------------------------------------------- #
# Incidentes
# --------------------------------------------------------------------------- #
def abrir_incidente(severity: str, source: str, title: str, description: str = "",
                    impact: str = "", diagnosis: str = "", task_id: str = "",
                    dedupe_key: str = "", ventana: float = 1800.0) -> Optional[dict]:
    """Un incidente abierto. Si el mismo fallo se repite, se refuerza el que hay."""
    ahora = time.time()
    if dedupe_key:
        with _lock:
            conn = db.get_conn()
            previa = conn.execute(
                "SELECT * FROM incidents WHERE source = ? AND title = ? "
                "AND status = 'abierto' AND created_at > ? LIMIT 1",
                (source, title[:180], ahora - ventana)).fetchone()
            if previa is not None:
                conn.execute("UPDATE incidents SET occurrences = occurrences + 1, "
                             "updated_at = ? WHERE id = ?", (ahora, previa["id"]))
                conn.commit()
                return dict(previa)
    fila = {"id": _nuevo_id("inc"), "severity": severity, "source": source,
            "title": title[:180], "description": description[:900],
            "impact": impact[:400], "diagnosis": diagnosis[:400],
            "status": "abierto", "task_id": task_id, "resolution": "",
            "occurrences": 1, "created_at": ahora, "updated_at": ahora,
            "resolved_at": None}
    try:
        with _lock:
            db.get_conn().execute(
                "INSERT INTO incidents (id, severity, source, title, description, impact,"
                " diagnosis, status, task_id, resolution, occurrences, created_at,"
                " updated_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(fila[k] for k in ("id", "severity", "source", "title", "description",
                                        "impact", "diagnosis", "status", "task_id",
                                        "resolution", "occurrences", "created_at",
                                        "updated_at", "resolved_at")))
            db.get_conn().commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo abrir el incidente: %s", exc)
        return None
    return fila


def listar_incidentes(limite: int = 30, solo_abiertos: bool = False) -> list[dict]:
    sql = "SELECT * FROM incidents"
    args: tuple = ()
    if solo_abiertos:
        sql += " WHERE status = 'abierto'"
    sql += " ORDER BY created_at DESC LIMIT ?"
    args = (int(limite),)
    return _todos(sql, args)


def obtener_incidente(inc_id: str) -> Optional[dict]:
    with _lock:
        return _fila(db.get_conn().execute(
            "SELECT * FROM incidents WHERE id = ?", (inc_id,)).fetchone())


def cerrar_incidente(inc_id: str, resolucion: str = "") -> bool:
    ahora = time.time()
    try:
        with _lock:
            cur = db.get_conn().execute(
                "UPDATE incidents SET status = 'resuelto', resolved_at = ?, "
                "resolution = ?, updated_at = ? WHERE id = ?",
                (ahora, (resolucion or "")[:400], ahora, inc_id))
            db.get_conn().commit()
            return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo cerrar el incidente: %s", exc)
        return False


def contar_incidentes() -> dict:
    try:
        fila = db.get_conn().execute(
            "SELECT COUNT(*) AS abiertos, SUM(CASE WHEN severity IN ('critical','alto')"
            " THEN 1 ELSE 0 END) AS graves FROM incidents WHERE status = 'abierto'"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.debug("conteo de incidentes: %s", exc)
        return {"abiertos": 0, "graves": 0}
    return {"abiertos": int(fila["abiertos"] or 0), "graves": int(fila["graves"] or 0)}


# --------------------------------------------------------------------------- #
# Decisiones
# --------------------------------------------------------------------------- #
def registrar_decision(chosen: str, question: str = "", reason: str = "",
                       alternatives: list | None = None, mission_id: str = "",
                       source: str = "usuario") -> dict:
    fila = {"id": _nuevo_id("dec"), "mission_id": mission_id,
            "question": question[:400], "chosen": str(chosen)[:400],
            "reason": reason[:600], "alternatives": json.dumps(alternatives or [],
                                                              ensure_ascii=False),
            "source": source[:40], "created_at": time.time()}
    try:
        with _lock:
            db.get_conn().execute(
                "INSERT INTO decisions (id, mission_id, question, chosen, reason,"
                " alternatives, source, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (fila["id"], fila["mission_id"], fila["question"], fila["chosen"],
                 fila["reason"], fila["alternatives"], fila["source"], fila["created_at"]))
            db.get_conn().commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo registrar la decisión: %s", exc)
    return fila


def listar_decisiones(limite: int = 40, mission_id: str = "") -> list[dict]:
    if mission_id:
        return _todos("SELECT * FROM decisions WHERE mission_id = ? "
                      "ORDER BY created_at DESC LIMIT ?", (mission_id, int(limite)))
    return _todos("SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (int(limite),))


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #
def crear_checkpoint(path: str, head: str, patch_path: str, files: int,
                     mission_id: str = "", note: str = "", dirty: bool = True) -> dict:
    fila = {"id": _nuevo_id("cp"), "path": path, "head": head, "dirty": 1 if dirty else 0,
            "patch_path": patch_path, "files": int(files), "mission_id": mission_id,
            "note": note[:200], "created_at": time.time()}
    try:
        with _lock:
            db.get_conn().execute(
                "INSERT INTO checkpoints (id, path, head, dirty, patch_path, files,"
                " mission_id, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (fila["id"], fila["path"], fila["head"], fila["dirty"], fila["patch_path"],
                 fila["files"], fila["mission_id"], fila["note"], fila["created_at"]))
            db.get_conn().commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo guardar el checkpoint: %s", exc)
    return fila


def obtener_checkpoint(cp_id: str) -> Optional[dict]:
    with _lock:
        return _fila(db.get_conn().execute(
            "SELECT * FROM checkpoints WHERE id = ?", (cp_id,)).fetchone())


def listar_checkpoints(path: str = "", limite: int = 20) -> list[dict]:
    if path:
        return _todos("SELECT * FROM checkpoints WHERE path = ? "
                      "ORDER BY created_at DESC LIMIT ?", (path, int(limite)))
    return _todos("SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT ?", (int(limite),))


def borrar_checkpoint(cp_id: str) -> bool:
    fila = obtener_checkpoint(cp_id)
    try:
        with _lock:
            cur = db.get_conn().execute("DELETE FROM checkpoints WHERE id = ?", (cp_id,))
            db.get_conn().commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("no se pudo borrar el checkpoint: %s", exc)
        return False
    if fila and fila.get("patch_path"):
        try:
            import os
            os.remove(fila["patch_path"])
        except OSError as exc:
            # La fila ya esta borrada de la base; esto es solo el .patch de
            # apoyo. Si no se puede quitar, se avisa en vez de callar: si no,
            # el disco se llenaria de parches de checkpoints ya inexistentes.
            log.debug("no se pudo borrar el parche %s: %s", fila["patch_path"], exc)
    return bool(cur.rowcount)