"""Enumera TODAS las rutas de una app, incluidas las montadas por include_router.

FastAPI reciente ya no copia cada ruta al app.routes: guarda un objeto
`_IncludedRouter` que las lleva dentro. Cualquier comprobacion que filtre por
`isinstance(ruta, APIRoute)` se queda sin las de /api/cmd, /api/dev, /api/ai
y /live, que es justo lo que paso en el test de seguridad de este mismo pase.

    python brain/rutas_reales.py            # listado
    python brain/rutas_reales.py --json     # para otras herramientas
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="rutas-"))


def _internas(ruta) -> tuple[list, str]:
    """Saca (rutas internas, prefijo) de un _IncludedRouter.

    En FastAPI 0.143 `include_router` ya no copia las rutas en app.routes:
    deja un `_IncludedRouter` con `original_router` (el APIRouter que se
    incluyó) e `include_context` (con su prefijo). Cualquier comprobación que
    filtre app.routes por `isinstance(r, APIRoute)` se queda sin /api/cmd,
    /api/dev, /api/ai y /live, que es lo que pasó en este mismo pase.
    """
    original = getattr(ruta, "original_router", None)
    if original is not None:
        contexto = getattr(ruta, "include_context", None)
        prefijo = getattr(contexto, "prefix", "") or ""
        return list(getattr(original, "routes", [])), prefijo
    # Formato antiguo: las venia en `routes`.
    valor = getattr(ruta, "routes", None)
    if isinstance(valor, list):
        return valor, ""
    interno = getattr(getattr(ruta, "router", None), "routes", None)
    if isinstance(interno, list):
        return interno, ""
    return [], ""


def todas_las_rutas(app) -> list[dict]:
    """Devuelve {metodo, ruta, tipo} de TODAS las rutas, sin saltarse ninguna."""
    from fastapi.routing import APIRoute, APIWebSocketRoute

    salida: list[dict] = []
    vistas: set[int] = set()

    def visitar(rutas, prefijo=""):
        for r in rutas:
            if id(r) in vistas:
                continue
            vistas.add(id(r))
            camino = getattr(r, "path", None)
            if isinstance(r, APIRoute) and camino:
                metodo = ",".join(sorted(getattr(r, "methods", {"GET"})))
                salida.append({"metodo": metodo, "ruta": prefijo + camino,
                               "tipo": "http", "endpoint": r.endpoint.__name__})
            elif isinstance(r, APIWebSocketRoute) and camino:
                salida.append({"metodo": "WS", "ruta": prefijo + camino,
                               "tipo": "ws", "endpoint": r.endpoint.__name__})
            else:
                # _IncludedRouter y compañía: se baja dentro.
                internas, sub_prefijo = _internas(r)
                if internas:
                    visitar(internas, prefijo + sub_prefijo)

    visitar(app.routes)
    salida.sort(key=lambda x: (x["ruta"], x["metodo"]))
    return salida


def main() -> int:
    import main

    rutas = todas_las_rutas(main.app)
    if "--json" in sys.argv:
        print(json.dumps(rutas, ensure_ascii=False, indent=2))
        return 0

    print(f"{len(rutas)} rutas registradas en la app\n")
    for r in rutas:
        print(f"  {r['metodo']:12} {r['ruta']:46} ({r['endpoint']})")

    api = [r for r in rutas if r["ruta"].startswith("/api/")
           or r["ruta"].startswith("/automatizaciones/")]
    auto = [r for r in rutas if r["tipo"] == "ws"]
    print(f"\n  de las que son de API: {len(api)}")
    print(f"  websockets: {len(auto)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())