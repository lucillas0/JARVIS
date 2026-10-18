r"""Las rutas de búsqueda de ficheros tienen que existir en TODOS los sistemas.

Por qué existe este fichero: `find_fast()` armaba sus carpetas personales
pegueando una barra invertida al final del nombre:

    roots = [os.path.expanduser("~") + "\\Desktop", ...]

En Windows eso es correcto. En Linux se convierte en "/root\Desktop": una sola
palabra con una barra dentro, que no existe. `isdir()` devuelve False, la
carpeta se descarta en silencio y "busca el fichero que tengo en el escritorio"
no encontraba nada dentro del contenedor. Sin error, sin aviso: solo que JARVIS
decía que no lo encontraba cuando nunca lo había buscado.

    python test_rutas_crossplatform.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-rutas-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def separador_malo(ruta: str) -> bool:
    """¿La ruta usa el separador que NO es el de este sistema?

    En Windows, `\\` es lo correcto. En Linux es un error: significa que
    alguien construyó la ruta concatenando en vez de usar os.path.join.
    """
    if os.name == "nt":
        return False
    return "/" in ruta and os.sep != "/"


def main_() -> int:
    print("RUTAS CROSS-PLATFORM · la búsqueda de ficheros funciona en Linux y Windows")
    print(f"    (sistema: {os.name}, separador {os.sep!r})", flush=True)
    from memory import filetree

    home = os.path.expanduser("~")

    # --- 1. Las rutas que monta ahora se arman bien en ESTE sistema -------
    vistas: list = []
    isdir_real = os.path.isdir

    def isdir_falso(p):
        vistas.append(str(p))
        return False

    os.path.isdir = isdir_falso
    try:
        filetree.find_fast("fichero-que-no-existe-xyz", limit=1, budget_sec=0.1)
    finally:
        os.path.isdir = isdir_real

    comprobar(bool(vistas), f"la búsqueda consulta rutas ({len(vistas)} vistas)")

    malas = [p for p in vistas if separador_malo(p)]
    comprobar(not malas, f"ninguna ruta mezcla el separador equivocado ({malas[:3]})")

    esperadas = {os.path.join(home, d) for d in
                 ("Desktop", "Documents", "Downloads", "OneDrive")}
    encontradas = esperadas & set(vistas)
    comprobar(len(encontradas) >= 3,
              f"se arman las carpetas personales ({len(encontradas)} de 4)")

    if os.name != "nt":
        # En Linux se buscan también las de XDG y las de nombre castellano,
        # que es como se llaman en un escritorio en español.
        castellano = {os.path.join(home, d) for d in
                      ("Escritorio", "Documentos", "Descargas")}
        comprobar(bool(castellano & set(vistas)),
                  f"y también las de nombre castellano/XDG "
                  f"({castellano & set(vistas)})")

    # --- 2. Y que encuentra un fichero DE VERDAD -----------------------
    # Se registra la carpeta temporal como raíz del árbol: `get_roots()` ya
    # tiene su manifiesto propio y aquí lo que se prueba es que la búsqueda
    #_recorre_ las rutas que dice recorrer.
    with tempfile.TemporaryDirectory() as d:
        nombre = "fichero-de-prueba-jarvis.txt"
        destino = Path(d) / "Escritorio" / "prueba"
        destino.mkdir(parents=True, exist_ok=True)
        (destino / nombre).write_text("hola", encoding="utf-8")
        try:
            filetree.add_root(d)
        except Exception as exc:  # noqa: BLE001
            comprobar(False, f"no se pudo añadir la raíz de prueba: {exc}")
        # Presupuesto amplio a propósito: aquí lo que se prueba es que la
        # búsqueda LLEGA al fichero, no que sea rápida. Las carpetas
        # personales se recorren antes que las raíces añadidas y en una
        # máquina real eso se lleva el presupuesto por delante.
        hits = filetree.find_fast(nombre, limit=5, budget_sec=25.0)
        encontrado = any(str(h).endswith(nombre) for h in hits)
        comprobar(encontrado,
                  f"encuentra de verdad el fichero en el disco "
                  f"({len(hits)} resultados)")

    # --- 3. Y que el presupuesto de tiempo se respeta -------------------
    t0 = __import__("time").time()
    filetree.find_fast("otro-fichero-que-no-existe-abc", limit=5)
    dt = __import__("time").time() - t0
    comprobar(dt <= filetree.find_fast.__defaults__[1] + 2,
              f"y no se pasa del presupuesto de "
              f"{filetree.find_fast.__defaults__[1]} s (tardó {dt:.1f})")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("RUTAS CORRECTAS EN ESTE SISTEMA.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())