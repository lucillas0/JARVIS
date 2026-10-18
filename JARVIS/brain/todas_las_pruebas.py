"""Corre TODAS las pruebas de JARVIS y dice el numero real de cada una.

    cd brain && python todas_las_pruebas.py

Cada suite corre en su propio proceso: si una se rompe al principio, las otras
siguen dando su resultado en vez de no decir nada.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent

SUITES = [
    ("Contexto", Path("test_context.py")),
    ("AI Gateway", Path("ai_gateway") / "test_ai.py"),
    ("Centro de mando", Path("command") / "test_cmd.py"),
    ("Ciclo de misiones", Path("orchestrator") / "test_ciclo.py"),
    ("Seguridad de la API", Path("test_seguridad.py")),
    ("Degradacion (FULL/LIGHT)", Path("test_degradacion.py")),
    ("Reconstruccion", Path("test_reconstruccion.py")),
    ("Cierre de huecos", Path("test_gap_closure.py")),
    ("Audio", Path("test_audio.py")),
    ("Conversacion de investigacion", Path("test_conversacion_investigacion.py")),
    ("Endpoints vivos", Path("test_endpoints_vivos.py")),
    ("Fuzz de conversacion", Path("test_fuzz_conversacion.py")),
    ("Plazo de investigacion", Path("test_plazo_investigacion.py")),
    ("Rutas cross-platform", Path("test_rutas_crossplatform.py")),
    ("Tiempos de respuesta", Path("test_tiempos_respuesta.py")),
    ("Fallo de modelos", Path("test_fallo_modelos.py")),
    ("Auditor de claves", Path("test_auditor_claves.py")),
    ("Idea a Live", Path("test_idea_a_live.py")),
]

# Cada suite cierra con su frase ("... VERIFICADO", "... VERIFICADA", "... PROTEGIDA").
CIERRES = ("VERIFICADO", "VERIFICADA", "PROTEGIDA")
fallos: list = []


def main() -> int:
    print("TODAS LAS PRUEBAS DE JARVIS\n" + "=" * 62)
    filas = []
    for nombre, relativo in SUITES:
        ruta = BRAIN / relativo
        if not ruta.is_file():
            filas.append((nombre, "NO EXISTE", 0, ""))
            fallos.append(f"{nombre}: no existe {relativo}")
            continue
        env = dict(os.environ)
        # El nombre de la suite va a un prefijo de carpeta, y con "/" ni "(" en
        # un nombre de carpeta Windows se rompe el tempfile.
        limpio = "".join(c if c.isalnum() else "-" for c in nombre)
        env["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix=f"jarvis-{limpio}-")
        env["PYTHONPATH"] = str(BRAIN)
        inicio = time.time()
        proc = subprocess.run([sys.executable, str(ruta)], env=env,
                              capture_output=True, text=True, timeout=900)
        tardanza = time.time() - inicio
        salida = (proc.stdout or "") + (proc.stderr or "")
        resumen = ""
        for linea in salida.splitlines():
            if any(c in linea for c in CIERRES):
                resumen = linea.strip()
        estado = "OK" if proc.returncode == 0 else "FALLA"
        if proc.returncode != 0:
            fallos.append(f"{nombre}: {proc.returncode}")
            # Se muestran las ultimas lineas de la salida para ver que fallo.
            ultimas = [x for x in salida.splitlines() if x.strip()][-6:]
            print(f"\n--- {nombre}: ultimas lineas ---")
            for u in ultimas:
                print("   ", u[:110])
        filas.append((nombre, estado, tardanza, resumen))
        print(f"  {nombre:26} {estado:5} {tardanza:6.1f}s  {resumen[:46]}")

    print("=" * 62)
    if fallos:
        print(f"\n{len(fallos)} suite(s) con fallos:")
        for f in fallos:
            print("  ·", f)
        return 1
    print("\nTODAS LAS SUITES EN VERDE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())