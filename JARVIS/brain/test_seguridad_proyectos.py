"""Una idea de desarrollo NO puede acabar escribiendo sobre el código de JARVIS.

Por qué existe este fichero: se pidió una web para llevar los gastos dentro
del contenedor y el plan que devolvió JARVIS decía, textual:

    1. Inicializar entorno y dependencias
    2. Definir modelo de gasto → en /app/brain/models.py
    3. Implementar API REST     → en /app/brain/routes.py
    4. Configurar aplicación    → /app/brain/app.py

Es decir: si el señor aprobaba, OpenCode escribía encima del cerebro que lo
está haciendo funcionar. La causa era `_ruta_por_defecto()`, que devolvía
`ROOT_DIR`: el propio JARVIS.

Aquí se comprueba que una idea corriente va a la carpeta de proyectos, que una
idea que SÍ habla de modificar JARVIS puede tocar su código, y que el
guardián salta cuando le pasan una ruta dentro del cerebro a propósito.

    python test_seguridad_proyectos.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
DATOS = tempfile.mkdtemp(prefix="jarvis-proy-")
os.environ["JARVIS_DATA_DIR"] = DATOS

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def main_() -> int:
    print("SEGURIDAD DE LOS PROYECTOS · una idea no escribe sobre el cerebro")
    from orchestrator.manager import (TaskManager, _a_carpeta_segura,
                                      _es_codigo_de_jarvis, _nombre_de_proyecto)
    import config

    raiz = str(config.BRAIN_DIR)

    # --- 1. Reconoce el código de JARVIS --------------------------------
    comprobar(_es_codigo_de_jarvis(raiz),
              f"reconoce su propio directorio ({raiz})")
    comprobar(_es_codigo_de_jarvis(os.path.join(raiz, "models.py")),
              "y un fichero dentro de él")
    comprobar(not _es_codigo_de_jarvis(DATOS),
              "y NO confunde la carpeta de proyectos con el cerebro")

    # --- 2. Una idea corriente NO puede acabar dentro del cerebro --------
    ruta_peligrosa = os.path.join(raiz, "models.py")
    for idea in ("desarrollame una web para llevar los gastos del mes",
                 "quiero una app de notas",
                 "hazme un programa que ordene fotos",
                 "construye una tienda online"):
        destino = _a_carpeta_segura(ruta_peligrosa, idea)
        comprobar(destino != ruta_peligrosa,
                  f"«{idea[:40]}»: una ruta dentro del cerebro se rechaza")

    # --- 3. Pero si la idea habla de JARVIS, sí se deja ------------------
    for idea in ("añade un plugin al cerebro de JARVIS",
                 "mejora el brain con una herramienta nueva",
                 "cambia el nucleo de jarvis para que hable más rápido"):
        comprobar(_a_carpeta_segura(ruta_peligrosa, idea) == ruta_peligrosa,
                  f"«{idea[:42]}»: sí puede tocar JARVIS (lo pidió)")

    # --- 4. Y una ruta de fuera no se toca ------------------------------
    externa = str(Path(DATOS) / "miproyecto")
    comprobar(_a_carpeta_segura(externa, "cualquier idea") == externa,
              "una ruta de fuera se respeta tal cual")

    # --- 5. La carpeta por defecto es de proyectos, NO el cerebro --------
    gestor = TaskManager()
    ruta = gestor._ruta_por_defecto("quiero una app de notas")
    comprobar(not _es_codigo_de_jarvis(ruta),
              f"la carpeta por defecto NO es el código de JARVIS ({ruta})")
    comprobar("proyectos" in ruta,
              f"sino una dentro de 'proyectos': {ruta}")
    comprobar(Path(ruta).is_dir(), "y existe de verdad en disco")

    # --- 6. Los nombres de carpeta son legibles --------------------------
    print()
    nombres = {
        "desarrollame una web para llevar los gastos del mes": "llevar-gastos-mes",
        "quiero una app de notas": "notas",
        "quiero una calculadora de propinas": "calculadora-propinas",
    }
    for idea, esperado in nombres.items():
        got = _nombre_de_proyecto(idea)
        comprobar(got == esperado,
                  f"«{idea[:40]}» -> «{got}» (esperado «{esperado}»)")

    # --- 7. Y una tarea creada de verdad va a la carpeta correcta ---------
    r = gestor.crear(idea="quiero una app de notas de cocina", project_path="")
    comprobar(r.get("ok") is True, "crea la tarea")
    tarea = r.get("task") or {}
    ruta_real = tarea.get("project_path", "")
    comprobar(not _es_codigo_de_jarvis(ruta_real),
              f"y la tarea trabaja FUERA del cerebro ({ruta_real})")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("LOS PROYECTOS NO TOCAN EL CÓDIGO DE JARVIS.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())