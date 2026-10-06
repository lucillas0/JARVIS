# -*- coding: utf-8 -*-
"""Git fácil: estado, historial, cambios y commits (sección 15)."""
import logging
import os
import subprocess

log = logging.getLogger("tools.git")


def _git(path: str, *args: str, timeout: int = 60) -> str:
    r = subprocess.run(["git", "-C", path, *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(((r.stderr or r.stdout) or "").strip().splitlines()[:3])
    return (r.stdout or "").strip()


def _repo(path: str) -> str:
    p = os.path.abspath(os.path.expandvars((path or "").strip().strip('"') or "."))
    if not os.path.isdir(os.path.join(p, ".git")):
        # ¿subdirectorio de un repo? pregunta a git
        try:
            top = _git(p, "rev-parse", "--show-toplevel")
            return top
        except Exception:
            raise ValueError(f"No es un repo git: {p}")
    return p


def git_status_handler(params: dict) -> dict:
    p = _repo(params.get("path", ""))
    short = _git(p, "status", "--short", "--branch")[:2000]
    return {"repo": p, "status": short or "limpio"}


def git_log_handler(params: dict) -> dict:
    p = _repo(params.get("path", ""))
    try:
        n = max(1, min(20, int(params.get("n", 5) or 5)))
    except (TypeError, ValueError):
        n = 5
    out = _git(p, "log", f"-{n}", "--pretty=%h %ad %s", "--date=short")[:2000]
    return {"repo": p, "log": out}


def git_diff_handler(params: dict) -> dict:
    p = _repo(params.get("path", ""))
    out = _git(p, "diff", "--stat")[:1500]
    det = _git(p, "diff")[:3000]
    return {"repo": p, "stat": out, "diff": det}


def git_commit_handler(params: dict) -> dict:
    p = _repo(params.get("path", ""))
    msg = (params.get("message", "") or "").strip()
    if not msg:
        raise ValueError("Falta el mensaje del commit.")
    _git(p, "add", "-A")
    out = _git(p, "commit", "-m", msg)[:500]
    return {"repo": p, "commit": out}


GIT_TOOLS = [
    {"name": "git_status",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str (carpeta del repo)"},
     "description": "Estado git: rama y ficheros cambiados.",
     "label": "Mirando git",
     "handler": git_status_handler},
    {"name": "git_log",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str", "n": "int opcional"},
     "description": "Historial de commits.",
     "label": "Leyendo historial",
     "handler": git_log_handler},
    {"name": "git_diff",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str"},
     "description": "Cambios sin commitear (resumen + detalle).",
     "label": "Viendo cambios",
     "handler": git_diff_handler},
    {"name": "git_commit",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Hago commit de todos los cambios?",
     "parameters": {"path": "str", "message": "str"},
     "description": "Añade todo y hace commit con el mensaje dado.",
     "label": "Haciendo commit",
     "handler": git_commit_handler},
]
