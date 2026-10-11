#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instalador de JARVIS: codigo, dependencias y claves de API.

Un solo script para Windows, Linux y macOS. No necesita nada instalado mas alla
de Python 3.10.

Por que en Python y no en un .bat y un .sh: los dos fallaban. El .bat se
rompia con los caracteres no ASCII de la consola de Windows y con rutas que
tienen espacios, y ninguno de los dos comprobaba que la instalacion sirviera
para algo. Aqui las dependencias van por grupos, para que un paquete pesado que
falle no tire el resto, y al final se importa la aplicacion de verdad.

Uso:
    python instalar.py                 instalacion normal
    python instalar.py --rapido        solo lo esencial, sin voz ni escritorio
    python instalar.py --todo          ademas voz y modelos locales
    python instalar.py --sin-claves    no preguntar por las claves
    python instalar.py --rehacer       rehacer el entorno desde cero
    python instalar.py --solo-claves   solo configurar las claves
    python instalar.py --ayuda
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# --- Consola -----------------------------------------------------------------
# En Windows la consola suele ser cp1252 y revienta con los acentos y los
# simbolos. Se pasa a UTF-8 tolerante antes de escribir nada.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - versiones antiguas de Python
        pass

ES_WINDOWS = os.name == "nt"
RAIZ = Path(__file__).resolve().parent
BRAIN = RAIZ / "brain"

MIN_PYTHON = (3, 10)

VERDE = "\033[32m"
ROJO = "\033[31m"
AMARILLO = "\033[33m"
CIAN = "\033[36m"
GRIS = "\033[90m"
FIN = "\033[0m"

if ES_WINDOWS:
    # En la consola clasica de Windows el codigo de color sale como texto.
    VERDE = ROJO = AMARILLO = CIAN = GRIS = FIN = ""


def paso(texto: str) -> None:
    print(f"{CIAN}>{FIN} {texto}", flush=True)


def ok(texto: str) -> None:
    print(f"{VERDE}OK{FIN}  {texto}", flush=True)


def aviso(texto: str) -> None:
    print(f"{AMARILLO}!{FIN}  {texto}", flush=True)


def error(texto: str) -> None:
    print(f"{ROJO}x{FIN}  {texto}", flush=True, file=sys.stderr)


def detalle(texto: str) -> None:
    print(f"    {GRIS}{texto}{FIN}", flush=True)


# ---Grupos de dependencias ---------------------------------------------------
# Cada grupo se instala por separado. Si uno falla se avisa y se sigue: lo
# importante es que el nucleo quede puesto.
GRUPOS_NUCLEO = [
    ("servidor", [
        "fastapi>=0.115", "uvicorn[standard]>=0.30", "python-dotenv>=1.0",
        "requests>=2.32", "httpx>=0.27", "psutil>=5.9",
    ]),
    ("memoria y datos", [
        "numpy>=1.26", "Pillow>=10.0", "pypdf>=4.0", "qdrant-client>=1.9",
    ]),
    ("modelos", [
        "groq>=0.11", "openai>=1.40", "google-generativeai>=0.7",
    ]),
    ("red y herramientas", [
        "paramiko>=3.4", "geocoder>=1.38",
    ]),
]

GRUPOS_ESCRITORIO = [
    ("escritorio", [
        "pywin32>=306", "pywinauto>=0.6.9", "comtypes>=1.2", "mss>=9.0",
    ]),
]

GRUPOS_VOZ = [
    ("voz", [
        "sounddevice>=0.4", "miniaudio>=1.59", "imageio-ffmpeg>=0.5",
    ]),
    ("modelos locales de voz (pesado)", [
        "piper-tts>=1.2", "openwakeword>=0.6", "onnxruntime>=1.18",
        "transformers>=4.40", "sentencepiece>=0.2",
    ]),
]

GRUPOS_PESADOS = [
    ("torch (pesado)", ["torch>=2.2"]),
]


# --- Utilidades --------------------------------------------------------------
def ruta_venv_existente() -> Path | None:
    """Busca un entorno ya hecho: el del escritorio o el de una Lite."""
    candidatos = []
    if ES_WINDOWS:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidatos.append(Path(appdata) / "JARVIS" / "brain-data" / "venv")
    candidatos.append(BRAIN / "venv")
    for c in candidatos:
        if es_venv(c):
            return c
    return None


def es_venv(d: Path) -> bool:
    return (d / "Scripts" / "python.exe").is_file() or (d / "bin" / "python").is_file()


def python_del_venv(venv: Path) -> Path:
    """Devuelve el ejecutable de Python de dentro del entorno."""
    if ES_WINDOWS:
        p = venv / "Scripts" / "python.exe"
    else:
        p = venv / "bin" / "python"
        if not p.is_file():
            p = venv / "bin" / "python3"
    if not p.is_file():
        raise RuntimeError(f"El entorno en {venv} esta roto: no encuentro su Python.")
    return p


def ejecutar(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> tuple[int, str]:
    """Ejecuta sin shell y sin romper con la codificacion de la consola."""
    CreationFlags = 0
    if ES_WINDOWS and len(cmd) > 1:
        # CREATE_NO_WINDOW: que pip no abra una ventana negra de fondo.
        CreationFlags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CreationFlags,
        )
    except FileNotFoundError:
        return 127, f"No se encontro el ejecutable: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "Se paso del tiempo limite."
    salida = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, salida


def ultimo_error(salida: str) -> str:
    """Saca la ultima linea util de la salida de pip."""
    lineas = [l.strip() for l in salida.splitlines() if l.strip()]
    fallos = [l for l in lineas if l.startswith("ERROR") or "Error" in l]
    return (fallos or lineas or ["sin detalle"])[-1][:200]


# --- Pasos -------------------------------------------------------------------
def comprobar_python() -> None:
    v = sys.version_info
    if v[:2] < MIN_PYTHON:
        error(f"Necesito Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} o superior y esto es {v.major}.{v.minor}.")
        detalle("Descargalo de https://www.python.org/downloads/ y marca 'Add python.exe to PATH'.")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor} en {platform.system()} {platform.machine()}")


def crear_entorno(rehacer: bool) -> Path:
    if not rehacer:
        v = ruta_venv_existente()
        if v:
            ok(f"Reutilizo el entorno que ya habia: {v}")
            return v
    destino = BRAIN / "venv"
    if destino.exists() and rehacer:
        aviso("Borro el entorno anterior para empezar de cero.")
        shutil.rmtree(destino, ignore_errors=True)

    paso("Creo el entorno propio de Python...")
    codigo, salida = ejecutar([sys.executable, "-m", "venv", str(destino)])
    if codigo != 0 or not es_venv(destino):
        error("No se pudo crear el entorno virtual.")
        detalle(ultimo_error(salida))
        if not ES_WINDOWS:
            detalle("En Debian/Ubuntu suele faltar el modulo venv:")
            detalle("  sudo apt install python3-venv python3-pip")
        sys.exit(1)
    ok(f"Entorno creado en {destino}")
    return destino


def instalar_grupos(py: Path, grupos: list, obligatorio: bool = True) -> list:
    """Instala grupo a grupo. Devuelve los que fallaron."""
    fallidos = []
    for nombre, paquetes in grupos:
        paso(f"Instalo {nombre} ({len(paquetes)} paquetes)...")
        codigo, salida = ejecutar(
            [str(py), "-m", "pip", "install", "--no-warn-script-location",
             "--disable-pip-version-check", *paquetes],
            timeout=1800,
        )
        if codigo == 0:
            ok(f"{nombre}")
        else:
            fallidos.append(nombre)
            aviso(f"{nombre}: fallo la instalacion")
            detalle(ultimo_error(salida))
            if nombre == "servidor" and obligatorio:
                error("Sin el servidor no arranca nada. Revisa la red o el proxy.")
                sys.exit(1)
    return fallidos


def actualizar_pip(py: Path) -> None:
    paso("Actualizo pip...")
    codigo, salida = ejecutar(
        [str(py), "-m", "pip", "install", "--upgrade", "pip",
         "--disable-pip-version-check", "--no-warn-script-location"],
        timeout=900,
    )
    if codigo != 0:
        aviso("No he podido actualizar pip; sigo con el que hay.")
        detalle(ultimo_error(salida))
    else:
        ok("pip al dia")


# --- Claves ------------------------------------------------------------------
CLAVES = [
    ("GROQ_API_KEY", "Groq", "rapido y gratis. https://console.groq.com/keys"),
    ("GEMINI_API_KEY", "Google Gemini", "https://aistudio.google.com/apikey"),
    ("OPENROUTER_API_KEY", "OpenRouter", "muchos modelos con una clave"),
    ("DISCORD_BOT_TOKEN", "Discord", "token del bot, para hablarle por privado"),
    ("SPOTIFY_CLIENT_ID", "Spotify", "reproductor; necesita tambien el secreto"),
    ("SPOTIFY_CLIENT_SECRET", "Spotify (secreto)", ""),
    ("WEATHER_API_KEY", "Meteorologia", ""),
    ("VIRUSTOTAL_API_KEY", "VirusTotal", "analisis de ficheros"),
]


def ruta_env_destino() -> Path:
    return BRAIN / ".env"


def leer_env(ruta: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if not ruta.is_file():
        return valores
    try:
        texto = ruta.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return valores
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() and k.strip() not in valores:
            valores[k.strip()] = v.strip().strip('"')
    return valores


def escribir_env(ruta: Path, cambios: dict[str, str]) -> None:
    """Actualiza el .env conservando el resto y quitando duplicados."""
    lineas: list[str] = []
    vistas: set[str] = set()
    if ruta.is_file():
        try:
            lineas = ruta.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            lineas = []

    salida: list[str] = []
    for linea in lineas:
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in cambios:
                if k not in vistas:
                    salida.append(f"{k}={cambios[k]}")
                    vistas.add(k)
                continue
            if k in vistas:
                continue  # duplicado previo: se descarta
            vistas.add(k)
        salida.append(linea)

    for k, v in cambios.items():
        if k not in vistas:
            salida.append(f"{k}={v}")

    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(salida).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass  # en NTFS chmod no hace nada


def preguntar_claves(forzar: bool = False) -> None:
    destino = ruta_env_destino()
    actuales = leer_env(destino)
    cambios: dict[str, str] = {}

    paso("Las claves de API")
    if actuales.get("GROQ_API_KEY") or actuales.get("GEMINI_API_KEY") or actuales.get("OPENROUTER_API_KEY"):
        ok("Ya hay al menos una clave de modelo guardada.")
    if not forzar and any(actuales.get(k) for k, _, _ in CLAVES):
        detalle("No las vuelvo a preguntar. Usa --solo-claves si quieres cambiarlas.")

    print()
    for nombre, etiqueta, pista in CLAVES:
        actual = actuales.get(nombre, "")
        if actual:
            print(f"  {VERDE}·{FIN} {nombre:<22} ya puesta")
            continue
        if pista:
            detalle(pista)
        print(f"  {nombre} ({etiqueta})")
        try:
            # El rotulo va en su propia linea: si no, en una tuberia el prompt
            # se pega con lo siguiente y no se lee nada.
            respuesta = input("    valor [vacio = saltar]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            aviso("Cancelado. Se guarda lo que hubiera.")
            break
        if respuesta:
            cambios[nombre] = respuesta.strip('"')
            ok(f"{nombre} guardada")

    if cambios:
        escribir_env(destino, cambios)
        ok(f"Guardadas {len(cambios)} claves en {destino}")
        detalle("Va en claro: no lo subas a ningun sitio ni lo compartas.")
    else:
        detalle("Sin claves. JARVIS arranca, pero no podra responder.")


# --- Comprobacion final ------------------------------------------------------
CODIGO_PRUEBA = """
import sys
faltan = []
for mod in ("fastapi", "uvicorn", "dotenv", "requests", "psutil"):
    try:
        __import__(mod)
    except Exception as e:
        faltan.append(mod + " (" + type(e).__name__ + ")")
if faltan:
    print("FALTAN:" + ",".join(faltan))
    sys.exit(1)
try:
    sys.path.insert(0, {brain!r})
    import config
    claves = bool(config.Config.has_any_key())
    print("OK claves=" + ("si" if claves else "no"))
except Exception as e:
    print("CONFIG_ERROR:" + type(e).__name__ + ":" + str(e)[:160])
    sys.exit(2)
"""


def verificar(py: Path) -> bool:
    paso("Compruebo que la instalacion sirve para algo...")
    codigo, salida = ejecutar([str(py), "-c", CODIGO_PRUEBA.format(brain=str(BRAIN))], timeout=120)
    if codigo == 0:
        for l in salida.splitlines():
            if l.startswith("OK"):
                detalle(l)
        ok("El cerebro importa y encuentra su configuracion.")
        return True
    error("La instalacion no queda utilizable.")
    for l in salida.splitlines()[:6]:
        if l.strip():
            detalle(l.strip())
    return False


# --- Programa ----------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--rapido", action="store_true",
                    help="solo lo esencial, sin escritorio ni voz")
    ap.add_argument("--todo", action="store_true",
                    help="incluye voz y modelos locales (bastante pesado)")
    ap.add_argument("--sin-claves", action="store_true", help="no preguntar por las claves")
    ap.add_argument("--rehacer", action="store_true", help="rehacer el entorno desde cero")
    ap.add_argument("--solo-claves", action="store_true", help="solo configurar las claves")
    ap.add_argument("--ayuda", "-h", action="store_true")
    args = ap.parse_args()

    if args.ayuda:
        print(__doc__)
        return 0

    print()
    print("  J.A.R.V.I.S.  ·  instalacion")
    print("  " + "-" * 40)
    print()

    comprobar_python()

    if args.solo_claves:
        preguntar_claves(forzar=True)
        return 0

    venv = crear_entorno(args.rehacer)
    py = python_del_venv(venv)
    actualizar_pip(py)

    fallidos: list[str] = []
    print()
    paso("Dependencias")
    fallidos += instalar_grupos(py, GRUPOS_NUCLEO)

    if not args.rapido:
        if ES_WINDOWS:
            fallidos += instalar_grupos(py, GRUPOS_ESCRITORIO, obligatorio=False)
        else:
            detalle("El escritorio solo es de Windows; me lo salto.")
    if args.todo:
        fallidos += instalar_grupos(py, GRUPOS_VOZ, obligatorio=False)
        fallidos += instalar_grupos(py, GRUPOS_PESADOS, obligatorio=False)
    else:
        detalle("Me salto la voz y torch. Usa --todo si los quieres.")

    if not args.sin_claves:
        print()
        preguntar_claves()

    bien = verificar(py)

    print()
    if bien and not fallidos:
        print(f"{VERDE}  Instalacion completa.{FIN}")
    elif bien:
        print(f"{VERDE}  Instalacion_utilizable, con avisos.{FIN} Fallaron: {', '.join(fallidos)}")
    else:
        print(f"{ROJO}  La instalacion ha quedado incompleta.{FIN}")
    print()
    print("  Para arrancar el cerebro:")
    print(f"    {py} -m uvicorn main:app --host 127.0.0.1 --port 8000")
    print("  (desde la carpeta brain)")
    print()
    print("  Comprobacion de que responde:")
    print("    http://127.0.0.1:8000/health")
    print("  Poner o cambiar las claves:")
    print(f"    python {Path(__file__).name} --solo-claves")
    print()
    # Codigo 0 solo si esta todo bien. Con avisos se devuelve 1 para que el
    # lanzador (.bat) lo diga y no cante un "instalacion terminada" en falso.
    return 0 if (bien and not fallidos) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        aviso("Cancelado por el usuario.")
        sys.exit(130)