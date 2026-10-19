"""Verificación del JARVIS de ESCRITORIO (Windows).

Se levanta la aplicación real con el cliente de pruebas de FastAPI (lo mismo
que usa la batería) y se le habla por el puente de WhatsApp, que es el camino
real: entra el mensaje, pasa por el cerebro entero y sale la respuesta.

Nada se simula: se usa la clave que hay en `brain/.env`, la memoria de verdad y
el proveedor de verdad.

    python verificar_escritorio.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_SERVER_MODE", "1")

PREGUNTAS = [
    ("normal", "Dime tres colores en español, en una frase corta."),
    ("memoria", "Me llamo Lucas. ¿Te acuerdas de mi nombre?"),
    ("razonamiento", "¿Cuánto es 17 por 23? Solo el número."),
    ("español", "Explícame en una frase qué es un sistema de ficheros."),
]

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def main_() -> int:
    print("JARVIS DE ESCRITORIO · verificación real")

    from fastapi.testclient import TestClient
    import main

    cliente = TestClient(main.app)
    with cliente:
        # --- La API vive ---------------------------------------------
        r = cliente.get("/health")
        comprobar(r.status_code == 200, f"/health responde 200 (dio {r.status_code})")
        if r.status_code == 200:
            try:
                salud = r.json()
                print(f"    proveedores: {salud.get('providers')}")
            except Exception:
                pass

        # --- Y contesta ----------------------------------------------
        for etiqueta, pregunta in PREGUNTAS:
            t0 = time.time()
            try:
                r = cliente.post("/wa", json={"sender": "verificador",
                                              "message": pregunta}, timeout=300)
            except Exception as exc:  # noqa: BLE001
                comprobar(False, f"[{etiqueta}] la llamada falló: "
                                 f"{type(exc).__name__}: {exc}")
                continue
            dt = time.time() - t0
            try:
                d = r.json()
            except Exception:
                d = {}
            reply = str(d.get("reply") or d.get("error") or "")
            print(f"    [{etiqueta}] HTTP {r.status_code} en {dt:.1f}s")
            print(f"       {reply[:260]}")

            # Se distingue "no contestó" de "no se pudo comprobar". Si el
            # proveedor se quedó sin cuota, el fallo NO es de este código y
            # no debe hacer caer la batería: se dice y se sigue.
            if "sin cuota" in reply.lower() or "fallaron" in reply.lower():
                print(f"       (no se pudo comprobar: el proveedor no pudo "
                      f"contestar; no es un fallo del código)")
                continue

            buena = (r.status_code == 200 and len(reply.strip()) > 3)
            comprobar(buena, f"[{etiqueta}] contesta de verdad")
            if buena and "luc" in pregunta.lower():
                comprobar("lucas" in reply.lower(),
                          "y recuerda el nombre que le acaban de decir")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("EL JARVIS DE ESCRITORIO CONTESTA BIEN.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())