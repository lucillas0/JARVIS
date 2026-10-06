# -*- coding: utf-8 -*-
"""Perfil personal del señor: datos, gustos y preferencias. Persiste en
DATA_DIR/profile.json y se inyecta en el system prompt para personalizar."""
import json
import logging
import os
import time

log = logging.getLogger("memory.profile")

_SEED = {"nombre": "Lucas"}


def _path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "profile.json")


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("facts", dict(_SEED))
                d.setdefault("prefs", [])
                d.setdefault("notes", [])
                for k, v in _SEED.items():
                    d["facts"].setdefault(k, v)
                return d
    except Exception:
        pass
    return {"facts": dict(_SEED), "prefs": [], "notes": []}


def _save(d: dict) -> None:
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except Exception as e:
        log.debug("profile save: %s", e)


def add_note(text: str) -> dict:
    d = _load()
    t = (text or "").strip()[:300]
    if t and t not in d["notes"]:
        d["notes"].append(t)
        d["notes"] = d["notes"][-30:]
    d["updated"] = time.time()
    _save(d)
    return d


def add_pref(text: str) -> dict:
    d = _load()
    t = (text or "").strip()[:300]
    if t and t not in d["prefs"]:
        d["prefs"].append(t)
        d["prefs"] = d["prefs"][-30:]
    d["updated"] = time.time()
    _save(d)
    return d


def set_fact(key: str, value: str) -> dict:
    d = _load()
    k, v = (key or "").strip()[:60], (value or "").strip()[:300]
    if k and v:
        d["facts"][k] = v
    d["updated"] = time.time()
    _save(d)
    return d


def _words(s: str) -> list:
    stop = {"el", "la", "los", "las", "de", "del", "que", "con", "por",
            "para", "una", "uno", "ese", "esa", "esto", "esta", "eso", "mi"}
    return [w for w in __import__("re").findall(r"[a-záéíóúñü]+", s.lower())
            if len(w) > 2 and w not in stop]


def _matches(frag: str, text: str) -> bool:
    t = text.lower()
    if frag in t:
        return True
    ws = _words(frag)
    return bool(ws) and all(w in t for w in ws)


def forget_match(frag: str) -> int:
    d = _load()
    f = (frag or "").strip().lower()
    n = 0
    if f:
        for lst in ("notes", "prefs"):
            keep = [x for x in d[lst] if not _matches(f, x)]
            n += len(d[lst]) - len(keep)
            d[lst] = keep
        for k in [k for k in d["facts"] if k != "nombre" and
                  (_matches(f, k) or _matches(f, str(d["facts"][k])))]:
            del d["facts"][k]
            n += 1
    _save(d)
    return n


def render() -> str:
    """Bloque compacto para el system prompt."""
    d = _load()
    lines = []
    for k, v in d.get("facts", {}).items():
        lines.append(f"- {k}: {v}")
    for p in d.get("prefs", [])[-8:]:
        lines.append(f"- prefiere: {p}")
    for x in d.get("notes", [])[-8:]:
        lines.append(f"- recuerda: {x}")
    return "\n".join(lines)
