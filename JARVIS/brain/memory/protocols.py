# -*- coding: utf-8 -*-
"""Protocolos del señor: atajos con funciones (estilo Google).
Cada protocolo: nombre, disparadores (substrings) y acciones
[{tool, params}] + texto final. Persiste en DATA_DIR/protocols.json."""
import json
import logging
import os
import time

log = logging.getLogger("memory.protocols")


def _path() -> str:
    from config import DATA_DIR
    return os.path.join(str(DATA_DIR), "protocols.json")


def _load() -> list:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except Exception as e:
        log.debug("protocols save: %s", e)


def list_all() -> list:
    return _load()


def add(name: str, triggers, actions, say: str = "") -> dict:
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split("|") if t.strip()]
    if isinstance(actions, str):
        actions = json.loads(actions)
    proto = {"name": (name or "").strip()[:60] or "protocolo",
             "triggers": [str(t).strip().lower()[:80] for t in (triggers or []) if str(t).strip()],
             "actions": actions if isinstance(actions, list) else [],
             "say": (say or "").strip()[:500],
             "updated": time.time()}
    if not proto["triggers"] or not proto["actions"]:
        raise ValueError("Faltan disparadores o acciones.")
    items = [p for p in _load() if p.get("name") != proto["name"]]
    items.append(proto)
    _save(items)
    return proto


def remove(name: str) -> bool:
    items = _load()
    left = [p for p in items if p.get("name") != (name or "").strip()]
    if len(left) == len(items):
        return False
    _save(left)
    return True


def match(text: str):
    t = (text or "").lower()
    for p in _load():
        for trig in p.get("triggers", []):
            if trig and trig in t:
                return p
    return None
