"""Ningún mensaje de verdad puede tardar medio segundo (ya calentado).

Por qué existe este fichero: el atajo de máquinas virtuales costaba 40 de los
44 segundos de una respuesta, y no se ve leyendo el código: era una llamada
correcta a `VBoxManage`, pero se repetía en CADA mensaje porque el fallo no se
cacheaba. Solo se encontró cronometrando.

La segunda mitad del motivo es igual de importante: hay que calentar ANTES de
medir. Un barrido en frío marca tres o cuatro segundos de falso positivo (la
primera vez que se toca cada camino monta cachés), y si uno de esos valores se
toma por un bug, se "arregla" una cosa que funcionaba.

    python test_tiempos_respuesta.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-tiempos-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


CASOS = [
    "qué hora es",
    "quién eres",
    "cuéntame un chiste",
    "resume mi día",
    "investiga la energía nuclear",
    "busca el precio del bitcoin",
    "qué es la entropía",
    "abre el navegador",
    "qué hora es en Madrid",
    "pon una alarma",
    "sí",
    "lee las fuentes",
    "no",
    "",
]

# Techo por mensaje una vez calentado. No es "lo que debería tardar": es un
# tope alto a propósito, para que salte lo que de verdad está roto y no lo que
# es razonablemente un poco de trabajo.
TOPE_S = 0.5


def comprobar_sin_powershell() -> bool:
    """Las consultas de Windows tienen que devolver vacío si no hay PowerShell.

    Docker es Linux: ahí `powershell` no existe. Sin esto salía un
    FileNotFoundError que subía hasta el conversacional, y "cómo va el
    equipo" era un error dentro del contenedor y una respuesta normal en el
    portátil.
    """
    import subprocess

    from tools import syshealth_tools as st

    real = subprocess.run

    def sin_powershell(*a, **k):
        raise FileNotFoundError("powershell")

    subprocess.run = sin_powershell
    try:
        st.perf_advisor_handler({})
        st.health_overview_handler({})
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      {type(exc).__name__}: {exc}")
        return False
    finally:
        subprocess.run = real


def main_() -> int:
    print("TIEMPOS DE RESPUESTA · calentado, sin disimular nada")
    import test_fuzz_conversacion as fz

    fz._sin_indexacion()
    import main

    main.brain.router = fz._todo_falla().router

    # --- Calentamiento: tres pasadas por todo. La primera vez que se toca
    # cada camino construye cachés e índices, y eso no es el coste del
    # mensaje: es el coste del arranque.
    for _ in range(3):
        for msg in CASOS:
            try:
                main.handle_user_message(msg, "text", broadcast=False)
            except Exception:
                pass

    filas = []
    for msg in CASOS:
        main.brain._pending_deep = None
        main.brain._pending_scan = None
        t0 = time.monotonic()
        try:
            main.handle_user_message(msg, "text", broadcast=False)
        except Exception:
            pass
        filas.append((time.monotonic() - t0, msg))

    filas.sort(reverse=True)
    print("    los más lentos (ya calentado):")
    for dt, msg in filas[:5]:
        marca = "   <-- TARDE" if dt > TOPE_S else ""
        print(f"      {dt * 1000:8.1f} ms  {msg[:40]!r}{marca}")

    lentos = [(d, m) for d, m in filas if d > TOPE_S]
    comprobar(not lentos,
              f"ningún mensaje pasa de {TOPE_S * 1000:.0f} ms "
              f"({len(lentos)} lentos de {len(filas)})")
    comprobar(filas and filas[-1][0] < TOPE_S,
              f"y el más rápido de todos está en {filas[-1][0] * 1000:.0f} ms")
    comprobar(comprobar_sin_powershell(),
              "las consultas de Windows devuelven vacío si no hay PowerShell "
              "(como en Docker) en vez de reventar")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print(f"RESPUESTAS EN SU PLAZO: {len(filas)} mensajes, "
          f"el más lento {filas[0][0] * 1000:.0f} ms.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())