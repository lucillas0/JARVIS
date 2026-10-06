# -*- coding: utf-8 -*-
"""Árbol de archivos del PC: índice de nombres->rutas, incremental.
Raíces por defecto: perfil del usuario + E:. Se amplía con 'indexa X'.
Un hilo lo reescanea cada 30 min. Búsqueda difusa por subcadena."""
import json
import logging
import os
import threading
import time

log = logging.getLogger("memory.filetree")

_SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata",
              "$recycle.bin", "system volume information", "appdata\\local\\packages",
              "node_modules", ".git", "__pycache__", "inthax"}
_MAX_ENTRIES = 60000
_loop_started = False


def _manifest_path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "filetree.json")


def _load() -> dict:
    try:
        with open(_manifest_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("roots", [])
                d.setdefault("entries", {})
                return d
    except Exception:
        pass
    return {"roots": [], "entries": {}}


def _save(d: dict) -> None:
    try:
        tmp = _manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _manifest_path())
    except Exception as e:
        log.debug("tree save: %s", e)


def default_roots() -> list:
    home = os.path.expanduser("~")
    cands = [os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
             os.path.join(home, "Downloads"), "E:\\",
             r"C:\Windows\System32\drivers\etc"]
    return [p for p in cands if os.path.isdir(p)]


def get_roots() -> list:
    roots = [r for r in _load().get("roots", []) if isinstance(r, str)]
    if not roots:
        roots = default_roots()
        _save({"roots": roots, "entries": {}})
        return roots
    # migración: añade drivers\etc si falta
    etc = r"C:\Windows\System32\drivers\etc"
    if os.path.isdir(etc) and etc not in roots:
        roots.append(etc)
        d = _load()
        d["roots"] = roots
        _save(d)
    return roots


def add_root(path: str) -> list:
    p = os.path.abspath(os.path.expandvars((path or "").strip().strip('"')))
    if not os.path.isdir(p):
        raise ValueError(f"No es carpeta: {path}")
    d = _load()
    roots = d.get("roots", []) or default_roots()
    if p not in roots:
        roots.append(p)
    d["roots"] = roots
    _save(d)
    return roots


def _walk(roots: list) -> dict:
    entries = {}
    count = [0]

    def _skip(dirpath: str) -> bool:
        low = dirpath.lower()
        if "drivers\\etc" in low or "drivers/etc" in low:
            return False
        return any(s in low for s in _SKIP_DIRS)

    for root in roots:
        for dirpath, dirnames, files in os.walk(root):
            if _skip(dirpath):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d.lower() not in _SKIP_DIRS]
            for fn in files:
                if count[0] >= _MAX_ENTRIES:
                    return entries
                if fn.startswith("."):
                    continue
                key = fn.lower()
                fp = os.path.join(dirpath, fn)
                lst = entries.get(key)
                if lst is None:
                    entries[key] = [fp]
                elif fp not in lst and len(lst) < 12:
                    lst.append(fp)
                count[0] += 1
    return entries


def reindex(roots=None) -> dict:
    if roots is None:
        roots = get_roots()
    entries = _walk(roots)
    d = {"roots": roots, "entries": entries, "scanned_at": time.time()}
    _save(d)
    return {"roots": len(roots), "files": len(entries)}


def search(query: str, limit: int = 8) -> list:
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    entries = _load().get("entries", {})
    if not entries:
        return []
    exact, partial = [], []
    for name, paths in entries.items():
        if not isinstance(paths, list):
            continue
        if q == name or q == os.path.splitext(name)[0]:
            exact.extend(paths)
        elif q in name:
            partial.extend(paths)
        if len(exact) + len(partial) >= limit * 3:
            break
    out = []
    for p in exact + partial:
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


def stats() -> dict:
    d = _load()
    return {"roots": d.get("roots", []), "files": len(d.get("entries", {})),
            "scanned_at": d.get("scanned_at", 0)}


def reindex_background() -> bool:
    def _run():
        try:
            reindex()
        except Exception as e:
            log.debug("tree bg: %s", e)

    threading.Thread(target=_run, daemon=True, name="tree-index").start()
    return True


def start_tree_loop(every_sec: int = 1800) -> bool:
    global _loop_started
    if _loop_started:
        return True
    _loop_started = True

    def _loop():
        time.sleep(90)
        while True:
            try:
                reindex()
            except Exception as e:
                log.debug("tree loop: %s", e)
            time.sleep(every_sec)

    threading.Thread(target=_loop, daemon=True, name="tree-auto").start()
    return True
