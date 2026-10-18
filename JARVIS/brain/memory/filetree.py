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


def find_fast(query: str, limit: int = 6, budget_sec: float = 4.0) -> list:
    """Busca en disco ahora mismo, sin esperar al índice.

    Primero el índice (instantáneo) y, si no acierta, recorre las carpetas
    personales con presupuesto de tiempo para responder enseguida.

    El presupuesto bajó de 8 a 4 segundos. Ocho segundos de silencio después de
    "busca el fichero X" es una eternidad para quien está delante, y el índice
    ya se ha consultado antes de llegar aquí: si en 4 segundos no aparece, no
    va a aparecer.
    """
    hits = search(query, limit=limit)
    if hits:
        base0 = os.path.basename((query or "").strip('"').strip("'")).lower()
        if base0:
            hits.sort(key=lambda p: (os.path.basename(p).lower() != base0,))
        return hits
    q = (query or "").strip().strip('"').strip("'")
    if not q:
        return []
    base = os.path.basename(q)
    low_q = q.lower()
    low_b = base.lower()
    has_ext = "." in base
    # OJO: estas rutas estaban escritas con una barra invertida pegada al
    # final del nombre. En Linux eso da "/root\Desktop", una sola palabra con
    # una barra dentro que no existe: `isdir` say No, la carpeta se descartaba
    # y "busca el fichero que tengo en el escritorio" dentro de Docker no
    # encontraba NADA, sin decir nada. Ahora se arman con `os.path.join`, que
    # pone el separador que toca, y se añaden las de Linux.
    home = os.path.expanduser("~")
    roots = [os.path.join(home, d) for d in
             ("Desktop", "Documents", "Downloads", "OneDrive")]
    if os.name != "nt":
        # En Linux las carpetas personales suelen venir por XDG, no con
        # nombre en inglés dentro de /root.
        for var in ("XDG_DESKTOP_DIR", "XDG_DOCUMENTS_DIR", "XDG_DOWNLOAD_DIR"):
            valor = os.environ.get(var, "")
            if valor:
                roots.append(valor)
        roots += [os.path.join(home, d) for d in ("Escritorio", "Documentos", "Descargas")]
    for d in list(get_roots()):
        if os.path.isdir(d) and d not in roots:
            roots.append(d)
    t0 = time.time()
    out = []
    for root in roots:
        if not os.path.isdir(root) or time.time() - t0 > budget_sec:
            continue
        for dp, dn, fns in os.walk(root):
            dn[:] = [d for d in dn
                      if d not in ("node_modules", "venv", ".git", "__pycache__",
                                   "AppData", "$Recycle.Bin", "System Volume Information")]
            if time.time() - t0 > budget_sec:
                break
            for fn in fns:
                lfn = fn.lower()
                if lfn == low_b or (has_ext and lfn.startswith(low_b)) \
                        or lfn == low_q or low_q in lfn:
                    p = os.path.join(dp, fn)
                    if p not in out:
                        out.append(p)
                        if len(out) >= limit:
                            return out
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
