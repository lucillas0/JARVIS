# -*- coding: utf-8 -*-
"""Memoria de proyectos: qué es cada proyecto, dónde vive y qué se hizo.
Persiste en DATA_DIR/projects.json. Lo usa 'continúa el proyecto X'."""
import json
import logging
import os
import time

log = logging.getLogger("memory.projects")


def _path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "projects.json")


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except Exception as e:
        log.debug("projects save: %s", e)


def remember(name: str, path: str = "", notes: str = "") -> dict:
    d = _load()
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("Falta el nombre del proyecto.")
    cur = d.get(key, {})
    if path:
        cur["path"] = os.path.abspath(os.path.expandvars(path.strip().strip('"')))
    if notes:
        prev = cur.get("notes", "")
        cur["notes"] = (prev + "\n" + notes.strip()).strip()[-2000:]
    cur["name"] = (name or "").strip()
    cur["updated"] = time.time()
    d[key] = cur
    _save(d)
    return cur


def recall(name: str) -> dict | None:
    d = _load()
    q = (name or "").strip().lower()
    if q in d:
        return d[q]
    for k, v in d.items():
        if q and (q in k or k in q):
            return v
    return None


def list_all() -> list:
    d = _load()
    return [{"name": v.get("name", k), "path": v.get("path", ""),
             "notes": (v.get("notes", "") or "")[:200]} for k, v in d.items()]


def touch(name: str, note: str = "") -> None:
    try:
        d = _load()
        q = (name or "").strip().lower()
        for k in list(d):
            if k == q or (q and (q in k or k in q)):
                d[k]["updated"] = time.time()
                if note:
                    d[k]["notes"] = ((d[k].get("notes", "") + "\n" + note).strip())[-2000:]
                _save(d)
                return
    except Exception as e:
        log.debug("projects touch: %s", e)
