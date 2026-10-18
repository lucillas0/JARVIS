"""Los endpoints de la API tienen que FUNCIONAR, no solo existir.

Por que este fichero existe: se comprobo que las 28 rutas nuevas estaban
montadas en la app y la matriz dio verde. Pero estar montada no es funcionar:
`PUT /memoria/recuerdos/{id}` estaba montada, aparecía en el recuento y
reventaba con un 500, porque el modelo de Pydantic usaba `Optional` sin
importarlo. Aquí se llama a los endpoints de verdad y se mira qué contestan.

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
    print(f"    · {'OK' if cond else 'FALLA'}  {texto}")
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
            # obligatorios: aquí lo que se comprueba es que el TIPO se ha
            # podido resolver, no que el cuerpo sea válido.
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
            detalle = "" if bien else f"contestó {str(r)[:90]}"
        except Exception as exc:  # noqa: BLE001
            bien = False
            detalle = f"{type(exc).__name__}: {str(exc)[:110]}"
        comprobar(bien, f"services.{nombre}() responde {detalle}")

    # --- 4. Lo que se escribe en la base se puede volver a leer ---------
    # `list_llm_usage` llamaba a un `_fila()` que no existía: reventaba con
    # NameError y quien lo llamaba lo silenciaba, así que las métricas del
    # gateway se leían SIEMPRE de memoria y nunca del disco. Sin historial.
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
                  "una métrica guardada se puede volver a leer del disco")
        if encuentra:
            fila = encuentra[0]
            comprobar(fila.get("streaming") is True,
                      f"y vuelve como booleano de verdad (no 0/1): "
                      f"{fila.get('streaming')!r}")
            comprobar(fila.get("ok") is True,
                      f"igual que «ok»: {fila.get('ok')!r}")
            comprobar(fila.get("total_tokens") == 8,
                      f"y los tokens son los que se guardaron: "
                      f"{fila.get('total_tokens')!r}")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"la métrica no se puede guardar/leer: "
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
                    # El PUT no devuelve el registro entero: devuelve qué campos
                    # cambiaron. Por eso el "se guardó" se comprueba yendo a
                    # por él después, que es lo que de verdad importa.
                    edi_cuerpo = edi.json()
                    comprobar(edi_cuerpo.get("ok") is True
                              and "contenido" in (edi_cuerpo.get("cambios") or []),
                              f"y dice qué cambió: {edi_cuerpo.get('cambios')}")
                    ver = cliente.get(f"/api/cmd/memoria/recuerdos/{rid}",
                                      headers=cabeceras)
                    comprobar(ver.status_code == 200
                              and "Editado" in ver.text,
                              f"y al releerlo ya está editado "
                              f"({ver.status_code})")
            else:
                comprobar(False, f"la API no devolvió id: {str(cuerpo)[:90]}")
    except ImportError:
        comprobar(False, "no hay httpx: no se puede probar la API de verdad")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"la prueba de la API reventó: {type(exc).__name__}: "
                         f"{str(exc)[:140]}")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS de {n[0]}:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print(f"ENDPOINTS VIVOS: {n[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())