"""Los endpoints de la API tienen que FUNCIONAR, no solo existir.

Por que este fichero existe: se comprobo que las 28 rutas nuevas estaban
montadas en la app y la matriz dio verde. Pero estar montada no es funcionar:
`PUT /memoria/recuerdos/{id}` estaba montada, aparecÃ­a en el recuento y
reventaba con un 500, porque el modelo de Pydantic usaba `Optional` sin
importarlo. AquÃ­ se llama a los endpoints de verdad y se mira quÃ© contestan.

    python test_endpoints_vivos.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-endp-"))

fallos: list = []
n = [0]


def comprobar(cond, texto):
    n[0] += 1
    print(f"    Â· {'OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def main_() -> int:
    print("ENDPOINTS VIVOS · no basta con que existan")
    from command import routes

    # --- 1. Todos los modelos de Pydantic se pueden construir -------------
    import pydantic

    for nombre in dir(routes):
        obj = getattr(routes, nombre)
        if not (isinstance(obj, type) and issubclass(obj, pydantic.BaseModel)):
            continue
        if obj is pydantic.BaseModel:
            continue                      # la clase base no se instancia
        try:
            # Se llama con `model_construct` para no fallar por campos
            # obligatorios: aquÃ­ lo que se comprueba es que el TIPO se ha
            # podido resolver, no que el cuerpo sea vÃ¡lido.
            obj.model_construct()
            bien = True
            detalle = ""
        except Exception as exc:  # noqa: BLE001
            bien = False
            detalle = f"{type(exc).__name__}: {str(exc)[:110]}"
        comprobar(bien, f"el modelo {nombre} se puede construir {detalle}")

    # --- 2. Los modelos con anotaciones se validan de verdad -------------
    try:
        m = routes.RecuerdoCambio(contenido="hola", tipo="preferencia",
                                  proyecto="demo", relevancia=0.5,
                                  metadata={"a": 1}, estado="activo")
        comprobar(m.contenido == "hola" and m.relevancia == 0.5,
                  "RecuerdoCambio valida los campos que ofrece el endpoint")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"RecuerdoCambio no valida: {type(exc).__name__}: "
                         f"{str(exc)[:110]}")

    # --- 3. Los servicios que usan las rutas no revientan ---------------
    from command import services

    for nombre, args in (("decisiones", ("", 5)),
                         ("recuerdos", ()),
                         ("autorizaciones", ())):
        fn = getattr(services, nombre, None)
        if fn is None:
            continue
        try:
            r = fn(*args)
            bien = isinstance(r, dict) and r.get("ok") is not False
            detalle = "" if bien else f"contestÃ³ {str(r)[:90]}"
        except Exception as exc:  # noqa: BLE001
            bien = False
            detalle = f"{type(exc).__name__}: {str(exc)[:110]}"
        comprobar(bien, f"services.{nombre}() responde {detalle}")

    # --- 4. Lo que se escribe en la base se puede volver a leer ---------
    # `list_llm_usage` llamaba a un `_fila()` que no existÃ­a: reventaba con
    # NameError y quien lo llamaba lo silenciaba, asÃ­ que las mÃ©tricas del
    # gateway se leÃ­an SIEMPRE de memoria y nunca del disco. Sin historial.
    import time as _time

    from memory import sqlite_store as store

    try:
        store.add_llm_usage({"id": "prueba-viva", "at": _time.time(),
                             "provider": "groq", "model": "m", "task": "general",
                             "ok": True, "input_tokens": 3, "output_tokens": 5,
                             "total_tokens": 8, "latency_ms": 120.0,
                             "retries": 0, "error_kind": "", "streaming": True,
                             "cost": "unknown", "request_id": "x"})
        leidas = store.list_llm_usage(0, 10)
        encuentra = [f for f in leidas if f.get("id") == "prueba-viva"]
        comprobar(bool(encuentra),
                  "una mÃ©trica guardada se puede volver a leer del disco")
        if encuentra:
            fila = encuentra[0]
            comprobar(fila.get("streaming") is True,
                      f"y vuelve como booleano de verdad (no 0/1): "
                      f"{fila.get('streaming')!r}")
            comprobar(fila.get("ok") is True,
                      f"igual que Â«okÂ»: {fila.get('ok')!r}")
            comprobar(fila.get("total_tokens") == 8,
                      f"y los tokens son los que se guardaron: "
                      f"{fila.get('total_tokens')!r}")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"la mÃ©trica no se puede guardar/leer: "
                         f"{type(exc).__name__}: {str(exc)[:120]}")

    # --- 5. Y el endpoint de verdad, de punta a punta -------------------
    try:
        from fastapi.testclient import TestClient
        from main import app

        cliente = TestClient(app)
        token = None
        try:
            import auth
            token = auth.token()
        except Exception:
            token = None
        cabeceras = {"X-Jarvis-Token": token} if token else {}

        # Crear un recuerdo y luego editarlo por la ruta que estaba rota.
        alta = cliente.post("/api/cmd/memoria/recuerdos",
                            json={"contenido": "Prueba de endpoint vivo",
                                  "tipo": "preferencia", "proyecto": "test-endp"},
                            headers=cabeceras)
        comprobar(alta.status_code in (200, 201, 401, 403),
                  f"crear recuerdo responde {alta.status_code} (no 500)")

        if alta.status_code in (200, 201):
            cuerpo = alta.json()
            rid = (cuerpo.get("recuerdo") or {}).get("id") or cuerpo.get("id")
            if rid:
                edi = cliente.put(f"/api/cmd/memoria/recuerdos/{rid}",
                                  json={"contenido": "Editado por el endpoint"},
                                  headers=cabeceras)
                comprobar(edi.status_code != 500,
                          f"EDITAR un recuerdo responde {edi.status_code} "
                          f"(era 500: modelo sin Optional importado)")
                if edi.status_code == 200:
                    # El PUT no devuelve el registro entero: devuelve quÃ© campos
                    # cambiaron. Por eso el "se guardÃ³" se comprueba yendo a
                    # por Ã©l despuÃ©s, que es lo que de verdad importa.
                    edi_cuerpo = edi.json()
                    comprobar(edi_cuerpo.get("ok") is True
                              and "contenido" in (edi_cuerpo.get("cambios") or []),
                              f"y dice quÃ© cambiÃ³: {edi_cuerpo.get('cambios')}")
                    ver = cliente.get(f"/api/cmd/memoria/recuerdos/{rid}",
                                      headers=cabeceras)
                    comprobar(ver.status_code == 200
                              and "Editado" in ver.text,
                              f"y al releerlo ya estÃ¡ editado "
                              f"({ver.status_code})")
            else:
                comprobar(False, f"la API no devolviÃ³ id: {str(cuerpo)[:90]}")

        # --- 6. Y ahora los que se llaman REPETIDOS, que es lo que hace Live
        # Un endpoint que funciona la primera vez y revienta la segunda pasa
        # desapercibido en cualquier prueba que lo llame una sola vez. Y Live
        # lo refresca cada pocos segundos: es justo el caso que se rompe.
        # Este fue un TypeError real en la red de `centers.recursos()`.
        print()
        print("ENDPOINTS QUE SE LLAMAN REPETIDOS")
        for i in range(3):
            e = cliente.get("/api/cmd/estado", headers=cabeceras)
            comprobar(e.status_code != 500,
                      f"/api/cmd/estado en la llamada n.Âº {i + 2}: "
                      f"HTTP {e.status_code}")
            if e.status_code == 500:
                break
    except ImportError:
        comprobar(False, "no hay httpx: no se puede probar la API de verdad")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"la prueba de la API reventÃ³: {type(exc).__name__}: "
                         f"{str(exc)[:140]}")

    # --- 7. La red de recursos, dos veces seguidas -----------------------
    try:
        from command import centers
        for i in range(3):
            r = centers.recursos(forzar=True)
            comprobar(isinstance(r, dict) and r.get("red") is not None,
                      f"centers.recursos() n.Âº {i + 1} mide la red sin reventar")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"centers.recursos() falla a la segunda: "
                         f"{type(exc).__name__}: {str(exc)[:110]}")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS de {n[0]}:")
        for f in fallos:
            print(f"  Â· {f}")
        return 1
    print(f"ENDPOINTS VIVOS: {n[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
