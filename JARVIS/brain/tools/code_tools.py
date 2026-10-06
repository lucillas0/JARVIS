"""Código fácil: guardar y ejecutar pequeños scripts (sección 12).

Carpeta fija: DATA_DIR/scripts. Extensiones: .py/.js/.txt/.md/.bat/.ps1.
run_script solo ejecuta .py con el venv del brain (timeout 60s).
"""
import logging
import os
import re
import subprocess

log = logging.getLogger("tools.code")

_ALLOWED = {".py", ".js", ".txt", ".md", ".markdown", ".bat", ".ps1"}


def _scripts_dir() -> str:
    from config import DATA_DIR
    d = os.path.join(str(DATA_DIR), "scripts")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    base = os.path.basename((name or "").strip())
    base = re.sub(r"[^a-zA-Z0-9._\- áéíóúñü]", "_", base)
    if "." not in base:
        base += ".py"
    ext = os.path.splitext(base)[1].lower()
    if ext not in _ALLOWED:
        raise ValueError(f"Extensión no permitida ({ext}). Vale: {sorted(_ALLOWED)}")
    return base


def write_script_handler(params: dict) -> dict:
    name = _safe_name(params.get("name", ""))
    content = str(params.get("content", "") or "")
    if len(content) > 200_000:
        raise ValueError("Código demasiado largo (200k).")
    path = os.path.join(_scripts_dir(), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "name": name, "bytes": len(content.encode("utf-8"))}


def run_script_handler(params: dict) -> dict:
    import sys as _sys
    name = _safe_name(params.get("name", ""))
    if not name.lower().endswith(".py"):
        raise ValueError("Solo ejecuto .py, señor.")
    path = os.path.join(_scripts_dir(), name)
    if not os.path.isfile(path):
        raise ValueError(f"No existe {name} en scripts.")
    try:
        timeout = max(5, min(120, int(params.get("timeout", 60) or 60)))
    except (TypeError, ValueError):
        timeout = 60
    try:
        r = subprocess.run([_sys.executable, path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Tardó más de {timeout}s.")
    out = (r.stdout or "")[:4000]
    err = (r.stderr or "")[:800]
    return {"rc": r.returncode, "output": out, "error": err}


CODE_TOOLS = [
    {"name": "write_script",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"name": "str (fichero, ej. hola.py)", "content": "str (código)"},
     "description": "Guarda un script/código en la carpeta de JARVIS para reutilizarlo.",
     "label": "Guardando código",
     "handler": write_script_handler},
    {"name": "run_script",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str (fichero .py guardado)", "timeout": "int opcional 5-120"},
     "description": "Ejecuta un script .py guardado y devuelve su salida.",
     "label": "Ejecutando código",
     "handler": run_script_handler},
]
