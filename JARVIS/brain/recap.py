"""Resumen semanal de la actividad de JARVIS.

Calcula lo que Coucou enseña en su 'recap': sesiones, tiempo, ficheros,
comandos y de mas. Sale de la tabla `messages`, que es lo unico que hay.

Endpoint: GET /api/recap  -> { ok, ...datos }
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from fastapi.responses import JSONResponse


def _datos_dir():
    base = Path(__file__).resolve().parent.parent
    venv = Path.home() / "AppData" / "Roaming" / "JARVIS" / "brain-data"
    if venv.exists():
        return venv
    return base


def _db_path() -> Path:
    return _datos_dir() / "jarvis.db"


# Una sesion es un rato de actividad followed by un hueco de 20 min o mas.
HUCO_SESION_S = 20 * 60
RUTA_RE = re.compile(
    r"(?<![\w])([A-Za-z]:\\[^\s\"'<>|]+|(?<![\w])/?[\w.\-]+/[\w.\-/]+)"
)
# JARVIS no recibe ordenes de terminal sino frases ('abre el marca'), asi que
# para el recuento de acciones se miran los verbos de intentanza.
ORDENES_RE = re.compile(r"^\s*(/[a-z\-]+|\$ |powershell|cmd |git |npm |node )", re.I)
ACCIONES = (
    (re.compile(r"^\s*(abre|abrir|open)\b", re.I), "abrir"),
    (re.compile(r"^\s*(crea|crear|genera|generar|make)\b", re.I), "crear"),
    (re.compile(r"^\s*(lee|leer|read|abre y lee)\b", re.I), "leer"),
    (re.compile(r"^\s*(escribe|escribir|write|guarda)\b", re.I), "escribir"),
    (re.compile(r"^\s*(busca|buscar|search|encuentra)\b", re.I), "buscar"),
    (re.compile(r"^\s*(instala|instalar|install)\b", re.I), "instalar"),
    (re.compile(r"^\s*(compila|compilar|build|construye)\b", re.I), "compilar"),
    (re.compile(r"^\s*(analiza|analizar|revisa|revisar|mira)\b", re.I), "analizar"),
    (re.compile(r"^\s*(resume|resumir)\b", re.I), "resumir"),
    (re.compile(r"^\s*(descarga|descargar|download)\b", re.I), "descargar"),
    (re.compile(r"^\s*(copia|copiar|copy)\b", re.I), "copiar"),
    (re.compile(r"^\s*(sustituye|reemplaza|rename)\b", re.I), "renombrar"),
)


def _a_epoch(valor) -> float:
    """Acepta marca de tiempo o fecha en texto ('2026-09-14 21:46:52')."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    try:
        return float(texto)
    except ValueError:
        pass
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(texto[:19], formato))
        except ValueError:
            continue
    return 0.0


def resumen(dias: int = 7) -> dict:
    db = _db_path()
    if not db.exists():
        return {"ok": False, "reason": "no hay base de datos"}
    corte = _a_epoch(time.strftime("%Y-%m-%d %H:%M:%S")) - dias * 86400
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        filas = con.execute(
            "select role, content, created_at from messages "
            "where created_at >= ? order by created_at asc",
            (corte,),
        ).fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    if not filas:
        return {
            "ok": True, "vacio": True, "dias": dias, "sesiones": 0,
            "mensajes": 0, "comandos": 0, "ficheros": 0,
        }

    sesiones = 1
    ultimo = _a_epoch(filas[0][2])
    primero_ts = ultimo
    comandos: Counter[str] = Counter()
    ficheros: Counter[str] = Counter()
    del_usuario = 0
    del_jarvis = 0
    ultimo_usuario: str = ""

    for rol, contenido, ts in filas:
        momento = _a_epoch(ts) or ultimo
        if momento - ultimo > HUCO_SESION_S:
            sesiones += 1
        ultimo = momento

        texto = str(contenido or "")
        if rol == "user":
            del_usuario += 1
            if texto.strip():
                ultimo_usuario = texto.strip()[:180]
            pedido = texto.strip()
            m = ORDENES_RE.match(pedido)
            if m:
                comandos[m.group(1).strip().lstrip("/")] += 1
            else:
                for patron, nombre in ACCIONES:
                    if patron.match(pedido):
                        comandos[nombre] += 1
                        break
            for ruta in RUTA_RE.findall(pedido):
                limpio = ruta.rstrip("\\/.,:;)]}")
                if len(limpio) > 2 and "/" in limpio or limpio[1:3] == ":\\":
                    ficheros[Path(limpio).name or limpio] += 1
        elif rol in ("assistant", "jarvis"):
            del_jarvis += 1

    return {
        "ok": True,
        "vacio": False,
        "dias": dias,
        "desde": primero_ts,
        "hasta": ultimo,
        "sesiones": sesiones,
        "mensajes": len(filas),
        "del_usuario": del_usuario,
        "del_jarvis": del_jarvis,
        "segundos": int(ultimo - primero_ts),
        "comandos": [
            {"nombre": n, "veces": c} for n, c in comandos.most_common(8)
        ],
        "ficheros": [
            {"nombre": n, "veces": c} for n, c in ficheros.most_common(8)
        ],
        "ultima_peticion": ultimo_usuario,
    }


def registrar(app) -> None:
    @app.get("/api/recap")
    async def api_recap(dias: int = 7):
        try:
            datos = resumen(max(1, min(30, int(dias))))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
            )
        return JSONResponse(content=datos)