#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pasa el historial de JARVIS a la memoria persistente de Mem0.

NO se ejecuta solo. Es un script a proposito: convertir meses de
conversacion en recuerdos es una decision del señor, no algo que deba
pasar por sorpresa al instalar.

Qué se migra, y qué no
-----------------------
Se cogen los mensajes del señor que contienen datos que duran (preferencias,
configuracion, proyectos, nombres) y se pasan como recuerdos. Los mensajes de
JARVIS NO: si no, la memoria se llena de las frases del propio asistente y
luego el modelo se recuerda a si mismo.

El filtro es el mismo que usa memory.manager, para que lo que entra por aqui
y lo que se guarda hablando sean coherentes.

Uso
---
    # Ver qué se migraría, sin escribir nada:
    python scripts/migrate_memory.py --dry-run

    # Migrar de verdad:
    python scripts/migrate_memory.py --limite 500

    # Solo un tramo, por fechas:
    python scripts/migrate_memory.py --desde 2026-01-01 --hasta 2026-06-30

Mem0 tiene que estar levantado. Si no lo esta, el script lo dice y sale.
"""
import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent / "brain"
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "memory"))


def _args():
    p = argparse.ArgumentParser(description="Migrar el historial a Mem0")
    p.add_argument("--limite", type=int, default=300,
                   help="máximo de mensajes a mirar (defecto 300)")
    p.add_argument("--desde", default="", help="AAAA-MM-DD")
    p.add_argument("--hasta", default="", help="AAAA-MM-DD")
    p.add_argument("--dry-run", action="store_true",
                   help="muestra lo que se migraría y no escribe nada")
    p.add_argument("--todos", action="store_true",
                   help="migrar aunque el servicio no esté levantado")
    return p.parse_args()


def _db_path() -> Path:
    # La misma regla que config.py: JARVIS_DATA_DIR si está, si no brain/db.
    d = os.environ.get("JARVIS_DATA_DIR", "").strip()
    return (Path(d) / "jarvis.db") if d else (RAIZ / "db" / "jarvis.db")


def _leer_mensajes(ruta: Path, limite: int, desde: str, hasta: str) -> list:
    if not ruta.is_file():
        raise SystemExit(f"No encuentro la base de datos: {ruta}")
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    q = "SELECT id, role, content, created_at FROM messages WHERE role='user'"
    cond, args = [], []
    if desde:
        cond.append("created_at >= ?"); args.append(desde)
    if hasta:
        cond.append("created_at <= ?"); args.append(hasta + " 23:59:59")
    if cond:
        q += " AND " + " AND ".join(cond)
    q += " ORDER BY id ASC LIMIT ?"; args.append(max(1, limite))
    filas = [dict(r) for r in con.execute(q, args)]
    con.close()
    return filas


def main() -> int:
    a = _args()
    print("Migracion del historial de JARVIS a Mem0\n")

    # El filtro y la deteccion de secretos son los de memory.manager: si aqui
    # se copiaran, migrarian cosas distintas de las que JARVIS guardaria luego.
    try:
        from memory.manager import manager
    except Exception as e:
        print(f"No puedo cargar la memoria: {e}")
        return 1
    m = manager()

    if not a.dry_run and not a.todos:
        h = m.health()
        if not h.get("alcanzable"):
            print(f"Mem0 no responde ({h.get('motivo', 'sin respuesta')}).")
            print("Levantalo con  docker compose up -d mem0 postgres")
            print("o usa  --todos  para migrar de todos modos (fallara al guardar).")
            return 1
        print(f"Mem0 disponible en {m.estado().get('url')}")
    print()

    ruta = _db_path()
    print(f"Historial: {ruta}")
    filas = _leer_mensajes(ruta, a.limite, a.desde, a.hasta)
    print(f"Mensajes del senor mirados: {len(filas)}\n")

    guardados = omitidos = secretos = 0
    for f in filas:
        txt = (f.get("content") or "").strip()
        ok, motivo = m.merece_recordarse(txt)
        if not ok:
            omitidos += 1
            if "clave" in motivo or "contrasena" in motivo:
                secretos += 1
            continue
        marca = f"[{f.get('created_at', '')[:10]}]"
        if a.dry_run:
            print(f"  [migraria] {marca} {txt[:90]}")
        else:
            r = m.add(f"{marca} {txt}")
            if r.get("ok"):
                guardados += 1
                print(f"  [guardado] {marca} {txt[:70]}")
            else:
                omitidos += 1
                print(f"  [fallo]    {r.get('motivo', '')[:70]}")
        if not a.dry_run:
            import time
            time.sleep(0.2)   # no reventar la API con 300 peticiones seguidas

    print()
    print(f"{'Se migrarian' if a.dry_run else 'Migrados'}: "
          f"{len(filas) - omitidos}")
    print(f"Omitidos (no duran): {omitidos}")
    print(f"  de ellos, secretos que NO se guardan: {secretos}")
    if a.dry_run:
        print("\nEra un ensayo. Quita --dry-run para escribir de verdad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
