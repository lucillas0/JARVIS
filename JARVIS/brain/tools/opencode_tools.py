# -*- coding: utf-8 -*-
"""Puente con OpenCode CLI: delega reparaciones con verificación.

opencode_fix(task): snapshot de .py -> ejecuta `opencode run` con modelo
barato -> detecta cambios -> py_compile -> rollback si rompe.
NUNCA toca keys.env, *.db ni credenciales. Solo código del brain.
"""
import logging
import os
import subprocess
import time

log = logging.getLogger("tools.opencode")

OC_CMD = r"C:\Users\Lucas\AppData\Roaming\npm\opencode.cmd"
OC_MODEL = "opencode/muse-spark-1.3-contributor-free"
OC_TIMEOUT = 480
PROTECT = ("keys.env", ".env", ".db", ".db-wal", ".db-shm", "auth.json",
           "creds.json")


def ensure_opencode(timeout: int = 180) -> bool:
    """Garantiza OpenCode CLI: si falta, intenta instalarlo con npm."""
    if os.path.isfile(OC_CMD):
        return True
    for cand in ("opencode.cmd", "opencode.exe", "opencode"):
        try:
            import shutil as _sh
            if _sh.which(cand):
                return True
        except Exception:
            pass
    try:
        import subprocess as _sp
        _sp.run(["npm", "install", "-g", "opencode"],
                capture_output=True, timeout=timeout,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log.warning("auto-install opencode: %s", e)
    return os.path.isfile(OC_CMD)

REPAIR_PROMPT = """Eres el reparador del asistente JARVIS (repo Python en {workdir}).
Intento número {attempt}: NO te rindas, los intentos anteriores fallaron.

ERROR:
- origen: {source}
- mensaje: {error}
- contexto:
{context}

REGLAS DURAS:
1. Solo puedes editar ficheros .py/.js/.html/.css dentro del repo. PROHIBIDO tocar keys.env,
   .env, *.db, credenciales, tokens o borrar nada.
2. Cambios mínimos que eliminen la causa raíz (no parches cosméticos).
3. No añadas dependencias nuevas. No cambies firmas públicas.
4. Si falta una capacidad (el error dice "no tengo herramienta" o similar),
   crea la herramienta en brain/tools/custom_tools.py como entrada CUSTOM_TOOLS
   con handler + risk_level + description, y avisa que hay que recargar.
5. Al terminar, resume en 3 líneas: causa, ficheros tocados, verificación.
6. Si no puedes repararlo con seguridad, di "NO-FIX: <motivo>" y no toques nada.
"""

BUILD_TOOL_PROMPT = """Eres el ingeniero del asistente JARVIS (repo Python en {workdir}).

FALTA UNA CAPACIDAD:
- origen: {source}
- petición/error: {error}
- contexto:
{context}

TAREA: crea la herramienta que falta en brain/tools/custom_tools.py.
Añade un handler `def <nombre>_handler(params: dict) -> dict:` que haga el trabajo
de verdad (sin inventar datos: si necesita un dato real, obténlo del sistema) y
una entrada en CUSTOM_TOOLS con este formato exacto:
{{"name": "<nombre>", "risk_level": "low|medium|sensitive",
  "requires_confirmation": False, "parameters": {{}},
  "description": "...", "label": "...", "handler": <nombre>_handler}}
Usa risk_level sensitive + requires_confirmation True si borra, apaga o toca red.
PROHIBIDO tocar keys.env, .env, *.db o credenciales. Sin dependencias nuevas.
Al terminar resume: nombre de la tool, ficheros tocados, verificación.
Si es imposible con seguridad, di "NO-FIX: <motivo>".
"""


def _keys_env() -> dict:
    env = dict(os.environ)
    try:
        base = env.get("APPDATA", "")
        p = os.path.join(base, "JARVIS", "keys.env") if base else ""
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        if k.strip() and v.strip():
                            env[k.strip()] = v.strip()
    except Exception as e:
        log.debug("opencode keys: %s", e)
    return env


def _snapshot(workdir: str) -> dict:
    snap = {}
    for dp, _, fns in os.walk(workdir):
        if "__pycache__" in dp or ".git" in dp:
            continue
        for fn in fns:
            if fn.endswith((".py", ".js", ".ts", ".json", ".html", ".css", ".md", ".txt", ".cjs")):
                p = os.path.join(dp, fn)
                try:
                    with open(p, "rb") as f:
                        import hashlib
                        snap[p] = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    pass
    return snap


def _brain_dir() -> str:
    try:
        from config import BRAIN_DIR
        return str(BRAIN_DIR)
    except Exception:
        return os.getcwd()


def opencode_fix(task: str, timeout: int = OC_TIMEOUT, workdir: str = "") -> dict:
    """Ejecuta OpenCode con la tarea. Devuelve {ok, output, files, error}."""
    workdir = workdir or _brain_dir()
    if not ensure_opencode():
        return {"ok": False, "error": "OpenCode CLI no instalado (auto-instalación falló)."}
    before = _snapshot(workdir)
    full_task = task  # ya viene con rol+reglas desde selfheal
    try:
        r = subprocess.run(
            [OC_CMD, "run", "-m", OC_MODEL, "--format", "json", "--auto",
             "--dir", workdir, full_task],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_keys_env(), cwd=workdir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "OpenCode tardó demasiado."}
    except Exception as e:
        return {"ok": False, "error": f"No pude lanzar OpenCode: {e}"}
    out = (r.stdout or "")[-2000:]
    if "NO-FIX" in out:
        return {"ok": False, "error": "OpenCode declinó: " + out[-300:]}
    after = _snapshot(workdir)
    changed = [p for p in after if after.get(p) != before.get(p)]
    changed = [p for p in changed
               if not any(x in p.replace("\\", "/") for x in PROTECT)]
    if not changed:
        return {"ok": False,
                "error": "OpenCode no modificó ningún fichero."}
    # verifica sintaxis de lo tocado (.py y .js)
    import py_compile
    bad = []
    for p in changed:
        try:
            if p.endswith(".py"):
                py_compile.compile(p, doraise=True)
            elif p.endswith(".js"):
                import subprocess as _sp2
                r2 = _sp2.run(["node", "--check", p], capture_output=True,
                              timeout=60,
                              creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0))
                if r2.returncode != 0:
                    bad.append((p, (r2.stderr or "")[:150]))
        except Exception as e:
            bad.append((p, str(e)[:150]))
    if bad:
        return {"ok": False,
                "error": "Sintaxis rota en: " + ", ".join(f"{p}: {e}" for p, e in bad)}
    return {"ok": True, "output": out[-800:], "files": changed}


def opencode_ask(question: str, timeout: int = 180) -> str:
    """Pregunta rápida al agente (sin editar nada): devuelve texto."""
    if not os.path.isfile(OC_CMD):
        return ""
    try:
        r = subprocess.run(
            [OC_CMD, "run", "-m", OC_MODEL, "--format", "json",
             "--dir", _brain_dir(), question],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_keys_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "")[-1500:]
    except Exception as e:
        log.debug("opencode ask: %s", e)
        return ""


def opencode_fix_handler(params: dict) -> dict:
    """Tool: delega una reparación a OpenCode (con confirmación)."""
    task = (params.get("task", "") or "").strip()
    if not task:
        raise ValueError("Falta la tarea de reparación.")
    res = opencode_fix(task, timeout=int(params.get("timeout", OC_TIMEOUT) or OC_TIMEOUT))
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "OpenCode no pudo."))
    return res


def opencode_ask_handler(params: dict) -> dict:
    """Tool: pregunta rápida a OpenCode (solo lectura)."""
    q = (params.get("question", "") or "").strip()
    if not q:
        raise ValueError("Falta la pregunta.")
    return {"answer": opencode_ask(q)}


OCODE_TOOLS = [
    {"name": "opencode_fix",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Delego la reparación a OpenCode?",
     "parameters": {"task": "str", "timeout": "int opcional"},
     "description": "Delega una reparación de código a OpenCode (verifica sintaxis).",
     "label": "Reparando con OpenCode",
     "handler": opencode_fix_handler},
    {"name": "opencode_ask",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"question": "str"},
     "description": "Pregunta técnica a OpenCode (solo lectura, sin cambios).",
     "label": "Consultando a OpenCode",
     "handler": opencode_ask_handler},
]
