"""Capa de datos: SQLite (mensajes, auditoría, settings).

Esquema acorde a la sección 12.1 de la especificación.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from config import Config

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
  content TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_type TEXT NOT NULL,
  payload TEXT,
  risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','sensitive')),
  confirmed BOOLEAN,
  result TEXT CHECK(result IN ('success','failed','cancelled','timeout')),
  details TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        import os
        os.makedirs(Config.BRAIN_DIR / "db", exist_ok=True)
        _conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        with _lock:
            _conn.executescript(SCHEMA)
            _conn.commit()
    return _conn


# ---------- messages ----------
def add_message(role: str, content: str) -> int:
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)", (role, content)
        )
        conn.commit()
        return cur.lastrowid


def get_recent_messages(limit: int = None) -> list[dict]:
    conn = get_conn()
    limit = limit or Config.CONTEXT_WINDOW_MESSAGES
    rows = conn.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def search_messages(keyword: str, limit: int = 10) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{keyword}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- auditoría ----------
def add_audit(action_type: str, payload: dict, risk_level: str,
              confirmed: Optional[bool], result: str, details: str = "") -> int:
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO audit_log (action_type, payload, risk_level, confirmed, result, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action_type, json.dumps(payload, ensure_ascii=False), risk_level,
             confirmed, result, details),
        )
        conn.commit()
        return cur.lastrowid


def list_audit(limit: int = 50) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def rotate_audit(days: int = None) -> int:
    """Borra auditoría mayor a N días (rotación automática de 30 días)."""
    days = days or Config.AUDIT_LOG_RETENTION_DAYS
    conn = get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        cur = conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


# ---------- settings ----------
def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()