"""El auditor tiene que ver las claves de proveedor, y no chillar por los dobles.

Por qué existe este fichero: el patrón de secretos exigía una asignación con
comillas (`api_key = "..."`). Una clave pegada sola en una línea, que es
justo como se cuelan cuando alguien copia un ejemplo, se le escapaba.

Ahora hay un segundo patrón para los prefijos reconocibles (gsk_, sk-, AIza,
ghp_, xox…-). El riesgo de ese patrón es el contrario: que se queje de los
dobles de prueba del propio repo, que son deliberadamente realistas
(`gsk_clave_de_prueba_no_valida`). Aquí se comprueban las dos mitades: que
pille una clave de verdad y que deje en paz a los dobles.

    python test_auditor_claves.py
"""
from __future__ import annotations

import io
import contextlib
import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-audit-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


# Formato real de una clave de Groq: gsk_ seguido de 52 caracteres.
FAKE = "gsk_" + "A1b2C3d4E5f6G7h8I9j0" * 2 + "KLMNOPQRSTUV"


def main_() -> int:
    print("AUDITOR DE CLAVES · pilla las de verdad, deja los dobles")
    import hardening_audit as ha

    # --- 1. Reconoce el formato de cada proveedor -----------------------
    print("\n1. Reconoce los prefijos de los proveedores")
    for nombre, clave in (
            ("Groq", FAKE),
            ("OpenAI", "sk-" + "a" * 48),
            ("Anthropic", "sk-ant-" + "b" * 48),
            ("Google", "AIza" + "c" * 35),
            ("GitHub", "ghp_" + "d" * 36),
            ("Slack", "xoxb-" + "e" * 40)):
        comprobar(ha.PREFIJOS.search(clave) is not None,
                  f"reconoce una clave de {nombre}")

    # --- 2. Y NO se dispara con los dobles del repo ---------------------
    print("\n2. Los dobles del propio repo no se marcan")
    # Se recorre el directorio, no se llama a `git`: en el contenedor de Docker
    # no hay git instalado, y una prueba que depende de una herramienta que
    # allí no existe no está probando el código sino el entorno.
    con_gsk = []
    for p in BRAIN.rglob("*.py"):
        if any(d in ("venv", "__pycache__", "node_modules", ".git")
               for d in p.parts):
            continue
        try:
            if "gsk_" in p.read_text(encoding="utf-8", errors="replace"):
                con_gsk.append(p)
        except OSError:
            pass
    ha.hallazgos.clear()
    ha.auditar_secretos()
    de_proveedor = [h for h in ha.hallazgos
                    if h["categoria"] == "clave-de-proveedor-en-codigo"]
    comprobar(not de_proveedor,
              f"ningún doble salta ({len(de_proveedor)} hallazgos en "
              f"{len(con_gsk)} ficheros con gsk_)")

    # --- 3. Y sí salta con una clave de verdad --------------------------
    print("\n3. Pero una clave de verdad sí salta")
    with tempfile.TemporaryDirectory() as d:
        carpeta = Path(d)
        (carpeta / "desastre.py").write_text(
            "# comentario innocuous\n"
            f"CLAVE = '{FAKE}'\n", encoding="utf-8")
        (carpeta / "doble.py").write_text(
            f"CLAVE = 'gsk_clave_de_prueba_no_valida'\n", encoding="utf-8")
        ha.hallazgos.clear()
        ha.auditar_secretos([carpeta / "desastre.py", carpeta / "doble.py"])
        findings = [h for h in ha.hallazgos
                    if h["categoria"] == "clave-de-proveedor-en-codigo"]
        comprobar(len(findings) == 1,
                  f"solo salta la de verdad (saltan {len(findings)})")
        if findings:
            h = findings[0]
            comprobar(h["severidad"] == "CRITICAL", "y sale como CRITICAL")
            comprobar(h["fichero"].endswith("desastre.py"),
                      f"y señala el fichero correcto: {h['fichero']}")
            # Lo importante: el informe NO puede llevar la clave dentro.
            spoils = FAKE in repr(ha.hallazgos)
            comprobar(not spoils,
                      "y el informe NO lleva la clave dentro, solo el prefijo")

    # --- 4. Informe completo sin ruido ----------------------------------
    print("\n4. El informe completo no inventa problemas")
    ha.hallazgos.clear()      # los del punto 3 eran de ficheros temporales
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ha.main()
    salida = buf.getvalue()
    comprobar("clave-de-proveedor-en-codigo" not in salida,
              "cero claves de proveedor en el repo")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("AUDITOR DE CLAVES CORRECTO.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())