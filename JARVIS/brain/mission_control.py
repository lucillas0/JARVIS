"""JARVIS Server Mission Control — SOLO modo LIGHT (servidor).

Regla fundamental: en modo FULL (escritorio) estas rutas NO existen (404) y la
navegacion no las muestra. La puerta es _sin_escritorio(), la misma que ya
distingue servidor de escritorio para el prompt: JARVIS_SERVER_MODE=1 o
cualquier Linux/Docker sin sesion de escritorio.

FASE A: prototipo visual con datos de mentira para evaluar composicion,
camara, paneles y transiciones. Lo unico real ya cableado: la actividad
(system/events la sirve el frontend desde /api/live/events) y el tiempo
encendido (de /health). Todo lo demas lleva demo:true y la pagina lo marca
con una insignia de SIMULACION. Nada de lo mockeado se presenta como real.

FASE B conectara, en este orden: hardware (psutil, ya es dependencia),
Docker (solo si hay socket, que hoy NO se monta a proposito), servicios,
Sentinel, almacenamiento, red, agentes y acciones. Cada paso sustituye una
clave del grafo mock por el proveedor real, sin tocar el frontend.
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from auth import coincide, origen_permitido, token

log = logging.getLogger("brain.mission")
router = APIRouter()


def _mission_dir():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mission")


def habilitado() -> bool:
    """True solo en LIGHT. Se evalua por peticion: barata y sin estado."""
    try:
        from main import _sin_escritorio
        return bool(_sin_escritorio())
    except Exception:
        return False


def register(app) -> None:
    app.include_router(router)
    log.info("Mission Control montado en /mission (solo modo LIGHT)")


def _authorized(request: Request) -> bool:
    authorization = request.headers.get("authorization", "")
    candidate = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    candidate = candidate or request.headers.get("x-jarvis-token")
    return origen_permitido(request.headers.get("origin")) and coincide(candidate)


def _nope():
    return JSONResponse({"ok": False, "error": "Mission Control solo existe en modo servidor."},
                        status_code=404, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ grafo
def grafo_mock() -> dict:
    """Grafo de mentira con la FORMA del real. Cada nodo: id, tipo, nombre,
    estado y metricas. FASE B sustituye estas listas por proveedores."""
    ahora = time.time()
    return {
        "demo": True,
        "generado": ahora,
        "nodos": [
            {"id": "jarvis", "tipo": "core", "nombre": "JARVIS",
             "estado": "online", "detalle": "Cerebro del servidor"},
            {"id": "ag-prog", "tipo": "agent", "nombre": "Programador",
             "estado": "trabajando", "detalle": "Tarea #1842"},
            {"id": "ag-vig", "tipo": "agent", "nombre": "Vigilante",
             "estado": "inactivo", "detalle": "Sin tarea"},
            {"id": "c-jarvis", "tipo": "container", "nombre": "jarvis",
             "estado": "online", "detalle": "jarvis:local",
             "metricas": {"cpu": 12.4, "ram_gb": 0.4, "uptime": "3d 14h"}},
            {"id": "c-mem0", "tipo": "container", "nombre": "jarvis-mem0",
             "estado": "online", "detalle": "memoria persistente",
             "metricas": {"cpu": 4.1, "ram_gb": 0.7, "uptime": "3d 14h"}},
            {"id": "c-db", "tipo": "container", "nombre": "jarvis-memoria-db",
             "estado": "online", "detalle": "pgvector/pgvector",
             "metricas": {"cpu": 1.2, "ram_gb": 0.2, "uptime": "3d 14h"}},
            {"id": "c-media", "tipo": "container", "nombre": "jellyfin",
             "estado": "online", "detalle": "multimedia",
             "metricas": {"cpu": 8.8, "ram_gb": 2.1, "uptime": "12d 08h"}},
            {"id": "svc-asistente", "tipo": "service", "nombre": "Asistente",
             "estado": "online", "detalle": "chat + voz"},
            {"id": "svc-memoria", "tipo": "service", "nombre": "Memoria",
             "estado": "online", "detalle": "Mem0 + pgvector"},
            {"id": "hw-cpu", "tipo": "hardware", "nombre": "CPU",
             "estado": "normal", "detalle": "34%",
             "metricas": {"valor": 34}},
            {"id": "hw-ram", "tipo": "hardware", "nombre": "RAM",
             "estado": "normal", "detalle": "61%",
             "metricas": {"valor": 61}},
            {"id": "hw-disk", "tipo": "hardware", "nombre": "Almacenamiento",
             "estado": "normal", "detalle": "72% de 24 TB",
             "metricas": {"valor": 72}},
            {"id": "net-router", "tipo": "network", "nombre": "Router",
             "estado": "online", "detalle": "192.168.1.1"},
            {"id": "net-nas", "tipo": "network", "nombre": "NAS",
             "estado": "online", "detalle": "192.168.1.10"},
        ],
        "enlaces": [
            ["jarvis", "ag-prog"], ["jarvis", "ag-vig"],
            ["jarvis", "c-jarvis"], ["c-jarvis", "c-mem0"], ["c-mem0", "c-db"],
            ["jarvis", "c-media"], ["jarvis", "svc-asistente"], ["jarvis", "svc-memoria"],
            ["c-jarvis", "hw-cpu"], ["c-jarvis", "hw-ram"], ["c-db", "hw-disk"],
            ["jarvis", "net-router"], ["net-router", "net-nas"],
        ],
        "actividad": 0.45,
    }


def sentinel_mock() -> dict:
    return {
        "demo": True,
        "estado": "estable",
        "anomalias": 0,
        "sistemas": {"CPU": "normal", "Memoria": "normal", "Docker": "normal",
                     "Red": "normal", "Almacenamiento": "normal"},
        "detalle": [],
    }


# ------------------------------------------------------------------ rutas
@router.get("/mission")
@router.get("/mission/")
async def mission_page(request: Request):
    if not habilitado():
        raise HTTPException(status_code=404, detail="No disponible en este modo")
    if not origen_permitido(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Origen no autorizado")
    import os
    index = os.path.join(_mission_dir(), "index.html")
    if not os.path.isfile(index):
        raise HTTPException(status_code=503, detail="Mission Control no instalado")
    with open(index, "r", encoding="utf-8") as f:
        html = f.read()
    bootstrap = "<script>window.JARVIS_TOKEN=" + json.dumps(token()) + ";</script>"
    html = html.replace("</head>", bootstrap + "</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/mission/{asset:path}")
async def mission_asset(asset: str, request: Request):
    if not habilitado():
        raise HTTPException(status_code=404, detail="No disponible en este modo")
    if not origen_permitido(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Origen no autorizado")
    import os
    base = os.path.realpath(_mission_dir())
    destino = os.path.realpath(os.path.join(base, asset))
    if (not destino.startswith(base + os.sep) and destino != base) \
            or not os.path.isfile(destino):
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    return FileResponse(destino, headers={"Cache-Control": "no-cache"})


@router.get("/api/mission/graph")
async def mission_graph(request: Request):
    if not habilitado():
        return _nope()
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "Sin sesion."}, status_code=401,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(grafo_mock(), headers={"Cache-Control": "no-store"})


@router.get("/api/mission/sentinel")
async def mission_sentinel(request: Request):
    if not habilitado():
        return _nope()
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "Sin sesion."}, status_code=401,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(sentinel_mock(), headers={"Cache-Control": "no-store"})
