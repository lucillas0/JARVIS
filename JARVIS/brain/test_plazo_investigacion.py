"""Cuánto puede tardar UNA conversación de investigación, de verdad.

Aquí no se sustituye el motor de búsqueda entero: se deja correr el de
verdad y lo único que se apaga es la RED (`_simple_fetch` falla al instante,
como cuando no hay salida). Así se ven las esperas que hay de verdad entre
reintentos, que es justo lo que hace que el señor piense que JARVIS se ha
quedado colgado.

    python test_plazo_investigacion.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-plazo-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def _sin_red(demora: float = 0.0):
    """Apaga solo la red. Los reintentos y las esperas siguen siendo los de verdad."""
    import tools.web_tools as wt

    peticiones = {"n": 0}

    def _falla(url, timeout=20):
        peticiones["n"] += 1
        if demora:
            time.sleep(demora)
        raise TimeoutError("sin salida a internet (simulado)")

    wt._simple_fetch = _falla
    wt._SEARCH_CACHE.clear()
    wt._QUICK_SEARCH_CACHE.clear()
    return peticiones


def main_() -> int:
    print("PLAZO DE LA INVESTIGACION · el señor no debe esperar minutos")

    import tools.web_tools as wt

    # --- 1. El motor de búsqueda, con sus esperas entre reintentos --------
    p = _sin_red()
    t0 = time.monotonic()
    res = wt.web_search_results_handler({"query": "un tema raro"})
    motor = time.monotonic() - t0
    print(f"    motor de búsqueda sin red: {motor:.1f} s "
          f"({p['n']} intentos, {len(res.get('results', []))} resultados)")
    techo_motor = getattr(wt, "PLAZO_BUSQUEDA_S", 12)
    comprobar(motor <= techo_motor + 1,
              f"el motor no se pasa de {techo_motor} s (tardó {motor:.1f})")

    # --- 2. La investigación entera, cuando la web devuelve páginas malas -
    _sin_red()
    import main

    leidas = {"n": 0}

    def _vacia(params):
        leidas["n"] += 1
        time.sleep(0.05)
        return {"content": ""}          # la dejan leer, pero no hay texto

    wt.web_fetch_handler = _vacia

    t0 = time.monotonic()
    main._research("tema sin resultados buenos")
    inv = time.monotonic() - t0
    print(f"    investigación completa: {inv:.1f} s ({leidas['n']} páginas)")
    techo_inv = getattr(main, "PLAZO_INVESTIGACION_S", 30)
    comprobar(inv <= techo_inv + 1,
              f"la investigación no se pasa de {techo_inv} s (tardó {inv:.1f})")

    # --- 3. Y que nunca se quede sin decir nada aunque no haya nada --------
    r = main._research("tema sin resultados buenos")
    comprobar(isinstance(r, str) and len(r.strip()) > 10,
              f"y siempre contesta algo: {r[:70]!r}")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("EL PLAZO DE LA INVESTIGACION SE RESPETA.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())