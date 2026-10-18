"""Capa de datos: SQLite (mensajes, auditoría, settings).

Esquema acorde a la sección 12.1 de la especificación.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from config import Config

log = logging.getLogger("brain.sqlite")

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
  -- 'sensitive' sigue admitida: hay herramientas etiquetadas asi desde antes.
  -- 'high' es su equivalente canonico y 'critical' es el nivel nuevo.
  risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','critical','sensitive')),
  confirmed BOOLEAN,
  result TEXT CHECK(result IN ('success','failed','cancelled','timeout')),
  details TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Ciclo de desarrollo con OpenCode (idea -> plan -> ejecucion -> resultado).
-- El detalle grande (plan, riesgos, eventos, permisos, preguntas, cambios,
-- tests, tiempos) va en `payload` como JSON: son documentos que se leen y
-- escriben enteros, no se consultan por columna.
CREATE TABLE IF NOT EXISTS dev_tasks (
  id TEXT PRIMARY KEY,
  idea TEXT NOT NULL,
  status TEXT NOT NULL,
  project_path TEXT DEFAULT '',
  opencode_session_id TEXT DEFAULT '',
  opencode_model TEXT DEFAULT '',
  opencode_agent TEXT DEFAULT '',
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  updated_at REAL,
  payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_dev_tasks_created ON dev_tasks(created_at DESC);

-- Centro de mando: notificaciones, incidentes, decisiones y checkpoints.
-- Los cuatro nacen de hechos reales (el bus de eventos, el autocorrector, las
-- respuestas del señor y el estado de Git); no hay datos de ejemplo.
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  category TEXT DEFAULT 'sistema',
  title TEXT NOT NULL,
  body TEXT DEFAULT '',
  task_id TEXT DEFAULT '',
  seen INTEGER DEFAULT 0,
  dismissed_at REAL,
  muted INTEGER DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  impact TEXT DEFAULT '',
  diagnosis TEXT DEFAULT '',
  status TEXT DEFAULT 'abierto',
  task_id TEXT DEFAULT '',
  resolution TEXT DEFAULT '',
  occurrences INTEGER DEFAULT 1,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  resolved_at REAL
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, created_at DESC);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  mission_id TEXT DEFAULT '',
  question TEXT DEFAULT '',
  chosen TEXT NOT NULL,
  reason TEXT DEFAULT '',
  alternatives TEXT DEFAULT '[]',
  source TEXT DEFAULT 'usuario',
  created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at DESC);

CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  head TEXT DEFAULT '',
  dirty INTEGER DEFAULT 0,
  patch_path TEXT DEFAULT '',
  files INTEGER DEFAULT 0,
  mission_id TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON checkpoints(created_at DESC);

-- AI Gateway: una fila por peticion a un modelo. Los tokens son los que declara
-- el proveedor; si no los declara, quedan NULL (no se estiman). El coste queda
-- como texto porque Groq no lo devuelve y aqui no se inventa ningun precio.
CREATE TABLE IF NOT EXISTS llm_usage (
  id TEXT PRIMARY KEY,
  at REAL NOT NULL,
  provider TEXT DEFAULT '',
  model TEXT DEFAULT '',
  task TEXT DEFAULT '',
  ok INTEGER DEFAULT 1,
  input_tokens INTEGER,
  output_tokens INTEGER,
  total_tokens INTEGER,
  latency_ms REAL DEFAULT 0,
  retries INTEGER DEFAULT 0,
  error_kind TEXT DEFAULT '',
  streaming INTEGER DEFAULT 0,
  cost TEXT DEFAULT 'unknown',
  request_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_at ON llm_usage(at DESC);

-- --------------------------------------------------------------------- --
-- Memoria estructurada (FASE 4/5). Antes los recuerdos vivian sueltos: el
-- perfil en un JSON, lo semantico en Qdrant y los mensajes en su tabla. Aqui
-- hay un almacen con origen, tipo y asociaciones, que es lo que permite
-- responder "¿por que JARVIS recuerda esto y de donde salio?".
-- --------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS recuerdos (
  id TEXT PRIMARY KEY,
  contenido TEXT NOT NULL,
  -- hecho: lo dijo el señor o se comprobó. preferencia: así le gusta.
  -- inferencia: lo dedujo JARVIS, NO es un hecho. obsoleto: puede que ya no
  -- valga. La distincion importa para no presentar una suposicion como
  -- verdad, y para no pisar un hecho con una inferencia.
  tipo TEXT NOT NULL DEFAULT 'hecho' CHECK(tipo IN ('hecho','preferencia','inferencia','obsoleto')),
  categoria TEXT DEFAULT '',
  origen TEXT DEFAULT '',
  procedencia TEXT DEFAULT '',
  proyecto TEXT DEFAULT '',
  mission_id TEXT DEFAULT '',
  decision_id TEXT DEFAULT '',
  metadata TEXT DEFAULT '{}',
  relevancia REAL DEFAULT 0.5,
  estado TEXT NOT NULL DEFAULT 'activo' CHECK(estado IN ('activo','archivado','eliminado')),
  creado REAL NOT NULL,
  actualizado REAL NOT NULL,
  accessed REAL DEFAULT 0,
  veces_usado INTEGER DEFAULT 0,
  contradice TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_recuerdos_estado ON recuerdos(estado, relevancia DESC);
CREATE INDEX IF NOT EXISTS idx_recuerdos_proyecto ON recuerdos(proyecto, estado);
CREATE INDEX IF NOT EXISTS idx_recuerdos_mision ON recuerdos(mission_id, estado);

-- --------------------------------------------------------------------- --
-- Conocimiento categorizado (FASE 6). El indice vectorial sigue siendo el de
-- library.py; esta tabla guarda la categoria, el proyecto y si el documento
-- se pudo leer de verdad, que el indice vectorial no guarda.
-- --------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS knowledge_docs (
  ruta TEXT PRIMARY KEY,
  categoria TEXT NOT NULL DEFAULT 'documentacion',
  proyecto TEXT DEFAULT '',
  mtime REAL DEFAULT 0,
  hash TEXT DEFAULT '',
  estado TEXT NOT NULL DEFAULT 'ok' CHECK(estado IN ('ok','ilegible','error')),
  detalle TEXT DEFAULT '',
  indexado REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_knowledge_cat ON knowledge_docs(categoria, estado);

-- --------------------------------------------------------------------- --
-- Autorizaciones recordadas (FASE 7). Por defecto NO se recuerda nada: la
-- decision 'once' no deja rastro. Solo se guarda lo que el señor autoriza
-- expresamente, con alcance exacto y expiracion.
-- --------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS autorizaciones (
  id TEXT PRIMARY KEY,
  alcance TEXT NOT NULL,
  tipo_operacion TEXT DEFAULT '',
  recursos TEXT DEFAULT '[]',
  restricciones TEXT DEFAULT '{}',
  decision TEXT NOT NULL CHECK(decision IN ('once','session','until','reject')),
  creada REAL NOT NULL,
  expira REAL DEFAULT 0,
  revocada INTEGER DEFAULT 0,
  revocada_at REAL DEFAULT 0,
  revocada_por TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  creada_por TEXT DEFAULT '',
  veces_usada INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_autorizaciones_alcance ON autorizaciones(alcance, revocada);

-- --------------------------------------------------------------------- --
-- Cola de trabajos de agentes (FASE 1). Persiste para que un reinicio no
-- pierda lo que estaba encolado, y para saber qué quedó a medias.
-- --------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS agent_jobs (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  mission_id TEXT DEFAULT '',
  task_id TEXT DEFAULT '',
  prioridad INTEGER NOT NULL DEFAULT 50,
  estado TEXT NOT NULL DEFAULT 'en_cola'
      CHECK(estado IN ('en_cola','ejecutando','ok','fallo','cancelado','atascado')),
  payload TEXT DEFAULT '{}',
  resultado TEXT DEFAULT '',
  error TEXT DEFAULT '',
  creado REAL NOT NULL,
  inicia REAL DEFAULT 0,
  termina REAL DEFAULT 0,
  heartbeat REAL DEFAULT 0,
  intentos INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_agent_jobs_estado ON agent_jobs(estado, prioridad DESC);

-- --------------------------------------------------------------------- --
-- Decisiones del Resource Manager: por que se bajio un limite, y cuando.
-- --------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS resource_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  motivo TEXT NOT NULL,
  valor_anterior TEXT DEFAULT '',
  valor_nuevo TEXT DEFAULT '',
  estado TEXT DEFAULT '',
  metricas TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_resource_decisions_at ON resource_decisions(at DESC);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        import os
        # Esta carpeta es un resto de cuando todo vivia junto a los assets. En
        # Docker /app se copia como root y el contenedor corre con el PUID del
        # NAS, asi que aqui no se puede crear. Como el makedirs no llevaba
        # try/except, un PermissionError tumbaba el arranque entero. El .db de
        # verdad esta en DATA_DIR, que si es un volumen escribible.
        try:
            os.makedirs(Config.BRAIN_DIR / "db", exist_ok=True)
        except OSError as exc:
            # No es grave: el .db de verdad esta en DATA_DIR, que si es
            # escribible. Se anota para no perder la pista de por que existe
            # una carpeta que no se pudo crear.
            log.debug("no se pudo crear %s: %s", Config.BRAIN_DIR / "db", exc)
        _conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        # WAL + synchronous=NORMAL: antes cada mensaje se guardaba con
        # escritura sincronizada y eran 20 milisegundos por mensaje, el 36%
        # de todo lo que tardaba JARVIS en responder. Con el diario en WAL
        # las escrituras van a un fichero aparte y no bloquean la lectura.
        # Para un registro de conversación, perder como mucho el último
        # mensaje si se apaga el equipo no cambia nada.
        try:
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error as exc:
            # Sin WAL todo funciona igual, solo que mas lento y con mas riesgo de
            # "database is locked" bajo concurrencia. Anotado para que si
            # aparece ese error se sepa que el motor va en modo antiguo.
            log.debug("SQLite sin WAL ni ajustes de sincronizacion: %s", exc)
        with _lock:
            _conn.executescript(SCHEMA)
            _migrar_riesgos(_conn)
            _conn.commit()
    return _conn


def _migrar_riesgos(conn: sqlite3.Connection) -> None:
    """Da a `audit_log` los niveles nuevos aunque la tabla ya existiera.

    `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya está, así que en una
    instalación anterior el CHECK seguiría admitting solo
    low/medium/sensitive y el motor reventaría al auditar una acción de riesgo
    'high' o 'critical'. Se reconstruye la tabla conservando las filas.

    Es una migración de una vez: si el esquema nuevo ya está, no hace nada.
    """
    try:
        fila = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='audit_log'").fetchone()
        if not fila or not fila[0]:
            return
        sql = str(fila[0])
        if "'critical'" in sql:
            return                      # ya migrada
        filas = conn.execute(
            "SELECT id, action_type, payload, risk_level, confirmed, result, "
            "details, created_at FROM audit_log").fetchall()
        conn.execute("ALTER TABLE audit_log RENAME TO audit_log_vieja")
        conn.execute(SCHEMA)
        for f in filas:
            conn.execute(
                "INSERT INTO audit_log (id, action_type, payload, risk_level, "
                "confirmed, result, details, created_at) VALUES (?,?,?,?,?,?,?,?)",
                tuple(f))
        conn.execute("DROP TABLE audit_log_vieja")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_at "
                     "ON audit_log(created_at)")
        log.info("audit_log migrada a los niveles canónicos (%d filas)", len(filas))
    except sqlite3.Error as exc:
        log.debug("no pude migrar audit_log: %s", exc)


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


# ---------- tareas de desarrollo con OpenCode ----------
def add_dev_task(task: dict) -> None:
    """Guarda una tarea nueva del ciclo de desarrollo. Idempotente por id."""
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO dev_tasks (id, idea, status, project_path, opencode_session_id,"
            " opencode_model, opencode_agent, created_at, started_at, finished_at,"
            " updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (task["id"], task.get("idea", ""), task.get("status", "DRAFT"),
             task.get("project_path", ""), task.get("opencode_session_id", ""),
             task.get("opencode_model", ""), task.get("opencode_agent", ""),
             float(task.get("created_at") or 0.0), task.get("started_at"),
             task.get("finished_at"), float(task.get("updated_at") or 0.0),
             json.dumps(task.get("payload") or {}, ensure_ascii=False)),
        )
        conn.commit()


def update_dev_task(task_id: str, task: dict) -> None:
    """Vuelca la tarea completa. El detalle va entero en payload."""
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE dev_tasks SET idea = ?, status = ?, project_path = ?,"
            " opencode_session_id = ?, opencode_model = ?, opencode_agent = ?,"
            " started_at = ?, finished_at = ?, updated_at = ?, payload = ?"
            " WHERE id = ?",
            (task.get("idea", ""), task.get("status", "DRAFT"),
             task.get("project_path", ""), task.get("opencode_session_id", ""),
             task.get("opencode_model", ""), task.get("opencode_agent", ""),
             task.get("started_at"), task.get("finished_at"),
             float(task.get("updated_at") or 0.0),
             json.dumps(task.get("payload") or {}, ensure_ascii=False), task_id),
        )
        conn.commit()


def get_dev_task(task_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM dev_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    tarea = dict(row)
    try:
        tarea["payload"] = json.loads(tarea.get("payload") or "{}")
    except (TypeError, ValueError):
        tarea["payload"] = {}
    return tarea


def list_dev_tasks(limit: int = 40) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM dev_tasks ORDER BY created_at DESC LIMIT ?", (int(limit),)
    ).fetchall()
    tareas = []
    for row in rows:
        tarea = dict(row)
        try:
            tarea["payload"] = json.loads(tarea.get("payload") or "{}")
        except (TypeError, ValueError):
            tarea["payload"] = {}
        tareas.append(tarea)
    return tareas

# ---------- AI Gateway: uso real de los modelos ----------
def add_llm_usage(fila: dict) -> None:
    """Guarda una peticion a un modelo. Los tokens pueden venir a None."""
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO llm_usage (id, at, provider, model, task, ok,"
            " input_tokens, output_tokens, total_tokens, latency_ms, retries,"
            " error_kind, streaming, cost, request_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(fila.get("id") or ""), float(fila.get("at") or 0.0),
             str(fila.get("provider") or ""), str(fila.get("model") or ""),
             str(fila.get("task") or ""), 1 if fila.get("ok") else 0,
             fila.get("input_tokens"), fila.get("output_tokens"),
             fila.get("total_tokens"), float(fila.get("latency_ms") or 0),
             int(fila.get("retries") or 0), str(fila.get("error_kind") or ""),
             1 if fila.get("streaming") else 0, str(fila.get("cost") or "unknown"),
             str(fila.get("request_id") or "")),
        )
        conn.commit()


def list_llm_usage(desde: float = 0.0, limite: int = 50) -> list[dict]:
    """Peticiones mas recientes que `desde` (epoch), las mas nuevas al final."""
    conn = get_conn()
    filas = conn.execute(
        "SELECT * FROM llm_usage WHERE at >= ? ORDER BY at DESC LIMIT ?",
        (float(desde or 0), int(limite)),
    ).fetchall()
    salida = [_fila(f) for f in filas]
    salida.reverse()
    return salida
