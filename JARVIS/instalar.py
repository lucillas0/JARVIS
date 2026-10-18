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
    python instalar.py --reparar       dejar la instalacion al dia (solo si hace falta)
    python instalar.py --solo-claves   solo configurar las claves
    python instalar.py --estado        decir que version esta puesta
    python instalar.py --ayuda
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
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
    # uvicorn SIN [standard] a proposito. El extra "standard" arrastra PyYAML,
    # que uvicorn solo usa si le pasas un --log-config en YAML (no se lo pasamos
    # nunca) y que no tiene ruedas precompiladas para los Python mas nuevos:
    # pip cae a compilarlo desde el fuente, hace falta Visual Studio Build Tools
    # y, como PyYAML va en el mismo grupo que fastapi, un solo paquete sin rueda
    # tumbaba el servidor entero. Los extras que si aportan se ponen aparte,
    # uno a uno y sin que un fallo arraste (ver EXTRAS_UVICORN).
    ("servidor", [
        "fastapi>=0.115", "uvicorn>=0.30", "python-dotenv>=1.0",
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

# Lo que aporta uvicorn[standard] menos PyYAML. Son aceleradores: si alguno no
# tiene rueda para tu Python se pierde sin drama, y el servidor funciona igual.
EXTRAS_UVICORN = [
    ("httptools", "parser de HTTP mas rapido"),
    ("uvloop", "bucle de eventos, solo en Linux y macOS"),
    ("watchfiles", "recarga al guardar, solo el modo desarrollo"),
    ("websockets", "el WebSocket que usa JARVIS"),
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


# Cada clase de fallo que se da al instalar con pip en Windows, con lo que
# significa y lo que se puede hacer. Antes el instalador daba el mismo consejo
# para todo y acababa diciendo "revisa la red" cuando el problema era otro.
FALLOS: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("No matching distribution found", "Could not find a version"),
     "ese paquete no existe o no hay version para tu Python",
     ["Suele ser un problema de la lista de dependencias, no tuyo."]),

    (("CERTIFICATE_VERIFY_FAILED", "certificate verify failed", "SSLError",
      "SSL: WRONG_VERSION_NUMBER", "TLSV1_ALERT"),
     "la red esta inspeccionando las conexiones y el certificado no cuadra",
     ["Es habitual en redes de empresa o universidad.",
      "Prueba con:  pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <paquete>",
      "O configura el proxy:  set HTTPS_PROXY=http://servidor:puerto"]),

    (("ProxyError", "Failed to establish a new connection", "Read timed out",
      "Connection to", "Network is unreachable", "Temporary failure in name resolution",
      "SSLError: EOF occurred"),
     "no se puede llegar a PyPI",
     ["Revisa la conexion, el proxy o el cortafuegos.",
      "Si usas proxy:  set HTTPS_PROXY=http://servidor:puerto"]),

    (("Access is denied", "Permission denied", "WinError 5"),
     "Windows no deja escribir o ejecutar",
     ["Suele ser el antivirus bloqueando el entorno nuevo.",
      "Cierra el antivirus un momento o anade brain\\venv a las exclusiones.",
      "Prueba tambien a ejecutar la consola como administrador."]),

    (("No space left on device", "There is not enough space"),
     "no queda espacio en disco",
     ["Hacen falta unos 1,5 GB. Libera espacio y vuelve a intentarlo."]),

    (("Failed building wheel", "error: Microsoft Visual C\\+\\+ 14.0",
      "error: command 'cl.exe' failed", "Building wheel for",
      "requires a compiler", "error: metadata-generation-failed"),
     "ese paquete no trae ruedas para tu Python y hay que compilarlo",
     ["Casi siempre es que tu Python es demasiado nuevo para el paquete.",
      "La solucion no es instalar un compilador, es usar Python 3.12 o 3.13,",
      "que es dondeestan las ruedas de todo.",
      "Si solo quieres el servidor sin voz ni escritorio:  instalar.bat --rapido"]),

    (("ModuleNotFoundError", "No module named"),
     "falta un modulo de Python",
     ["Comprueba que el entorno se creo bien:  python instalar.py --rehacer"]),

    (("Access is denied", "cannot write", "Read-only file system"),
     "la carpeta destino no permite escribir",
     ["Mueve el proyecto a una carpeta donde tengas permisos."]),
]


def diagnosticar(salida: str) -> tuple[str, list[str]]:
    """Traduce la salida de pip a un motivo y un consejo. (motivo, consejos)"""
    baja = salida.lower()
    for disparadores, motivo, consejos in FALLOS:
        if any(d.lower() in baja for d in disparadores):
            return motivo, list(consejos)
    return "la instalacion de ese grupo ha fallado", []


def avisar_fallo(nombre: str, salida: str) -> None:
    """Informa de un grupo roto con un motivo util, no generico."""
    motivo, consejos = diagnosticar(salida)
    aviso(f"{nombre}: {motivo}")
    detalle(ultimo_error(salida))
    for c in consejos:
        detalle(c)


# --- Version y reparacion ----------------------------------------------------
# Que version hay ahora en el codigo. Se lee de JARVIS/VERSION, que es la
# unica fuente de verdad; si no esta, se cae al package.json de la app. Antes
# esto vivia repartido (4.0.0 en main.py, 4.1.0 en package.json) y por eso no
# se podia saber si una instalacion estaba vieja.
def version_actual() -> str:
    f = RAIZ / "VERSION"
    try:
        if f.is_file():
            v = f.read_text(encoding="utf-8").strip()
            if v:
                return v
    except OSError:
        pass
    try:
        pkg = json.loads((RAIZ / "app" / "package.json").read_text(encoding="utf-8"))
        return str(pkg.get("version") or "0.0.0")
    except Exception:  # noqa: BLE001 - el package.json no tiene que estar
        return "0.0.0"


def compara(a: str, b: str) -> int:
    """ -1 si a < b, 0 si iguales, 1 si a > b. Tolera 'v4.1.0' y '4.1'."""
    def partes(v: str) -> list:
        limpio = str(v or "").strip().lstrip("vV")
        return [int(x) if x.isdigit() else 0
                for x in limpio.split("-")[0].split(".")[:3]]

    pa, pb = partes(a), partes(b)
    pa += [0] * (3 - len(pa))
    pb += [0] * (3 - len(pb))
    return (pa > pb) - (pa < pb)


def huella_dependencias() -> str:
    """Huella de lo que hay que instalar. Si cambia, hay que reinstalar."""
    trocitos = []
    for nombre in ("requirements.txt", "requirements-server.txt",
                   "requirements-desktop.txt", "requirements-extras.txt"):
        f = BRAIN / nombre
        try:
            # Solo las lineas de paquete: los comentarios no cambian las cosas y
            # si no, cada retoque al fichero dispara una reinstalacion entera.
            lineas = [l.strip() for l in f.read_text(encoding="utf-8").splitlines()
                      if l.strip() and not l.strip().startswith("#")]
            trocitos.append(nombre + "\n" + "\n".join(lineas))
        except OSError:
            trocitos.append(nombre + "\n(ausente)")
    return hashlib.sha256("\n".join(trocitos).encode("utf-8")).hexdigest()[:16]


def sello(venv: Path) -> Path:
    """El testigo de 'que version esta instalada'. Vive DENTRO del venv: si se
    borra el venv, el testigo se va con el, que es justo lo que queremos."""
    return venv / "instalada.json"


def leer_sello(venv: Path) -> dict:
    try:
        return json.loads(sello(venv).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - un sello ilegible es lo mismo que no haberlo
        return {}


def escribir_sello(venv: Path, extras: list[str]) -> None:
    try:
        sello(venv).write_text(json.dumps({
            "version": version_actual(),
            "dependencias": huella_dependencias(),
            "extras": sorted(extras),
            "fecha": _dt.datetime.now().isoformat(timespec="seconds"),
            "python": platform.python_version(),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        aviso(f"No he podido guardar el testigo de version: {e}")


def entorno_sano(venv: Path) -> bool:
    """El venv existe y su Python arranca de verdad.

    Esto no es teoria: un venv cuyo interprete base se ha desinstalado (por
    ejemplo al actualizar Python de 3.12 a 3.13) sigue teniendo sus carpetas y
    sus Scripts/python.exe, pero no arranca. El entorno parece estar bien y
    todo falla luego con un error que no explica nada.
    """
    try:
        py = python_del_venv(venv)
    except RuntimeError:
        return False
    codigo, salida = ejecutar([str(py), "-c", "import sys; print(sys.version.split()[0])"], timeout=60)
    return codigo == 0 and bool(salida.strip())


# Importa solo lo del nucleo, sin tocar config.py ni el .env: es una pregunta
# barata que se puede hacer antes de instalar nada.
_NUCLEO_TEST = (
    "import sys\n"
    "faltan = []\n"
    "for m in ('fastapi', 'uvicorn', 'dotenv', 'requests', 'psutil'):\n"
    "    try:\n"
    "        __import__(m)\n"
    "    except Exception:\n"
    "        faltan.append(m)\n"
    "print(','.join(faltan))\n"
)


def entorno_completo(venv: Path) -> tuple[bool, list[str]]:
    """¿El entorno tiene lo que el cerebro necesita para arrancar?

    Antes no se miraba, y era un agujero gordo: un venv con el Python vivo pero
    sin ni el fastapi pasaba por sano. --reparar decia "ya esta al dia", no
    instalaba nada, y luego el cerebro no arrancaba con un ImportError. Ahora se
    pregunta de verdad.
    """
    try:
        py = python_del_venv(venv)
    except RuntimeError:
        return False, ["(no hay Python en el entorno)"]
    codigo, salida = ejecutar([str(py), "-c", _NUCLEO_TEST], timeout=120)
    if codigo != 0:
        return False, ["(el Python del entorno no responde)"]
    faltan = [x for x in (salida.strip().splitlines() or [""])[-1].split(",") if x]
    return (not faltan), faltan


def estado() -> int:
    """--estado: dice que hay puesto, sin tocar nada."""
    print()
    v = ruta_venv_existente()
    ahora = version_actual()
    print(f"  Version en el codigo:  {ahora}")
    if not v:
        aviso("No hay ningun entorno instalado. Ejecuta:  python instalar.py")
        return 1
    print(f"  Entorno:               {v}")
    s = leer_sello(v)
    puesta = str(s.get("version") or "")
    fecha = s.get("fecha", "")
    if puesta:
        print(f"  Version instalada:     {puesta}" + (f"  ({fecha})" if fecha else ""))
        print(f"  Extras:                {', '.join(s.get('extras') or []) or 'ninguno'}")
    else:
        print("  Version instalada:     sin testigo (se instalo antes de que lo hubiera)")
    print()

    # El estado del entorno va primero: da igual que la version cuadre si el
    # entorno no tiene lo que el cerebro necesita para arrancar.
    estado_venv, motivo = _estado_instalacion(v)
    if estado_venv == "roto":
        error(f"El entorno esta roto: {motivo}.")
        detalle("Arreglalo con:  python instalar.py --reparar")
        return 1
    if estado_venv == "incompleto":
        aviso(f"El entorno esta vivo pero incompleto: {motivo}.")
        detalle("Por eso el cerebro no arranca aunque el Python sí. Arreglalo con:")
        detalle("    python instalar.py --reparar")
        return 1

    if not puesta:
        ok(f"El entorno esta bien, pero no hay testigo de version ({ahora}).")
        detalle("Para sellarlo:  python instalar.py --reparar")
        return 0

    c = compara(puesta, ahora)
    if c < 0:
        aviso(f"Desactualizada: tienes la {puesta} y en el codigo hay la {ahora}.")
        detalle("Arreglalo con:  python instalar.py --reparar")
        return 1
    if s.get("dependencias") and s["dependencias"] != huella_dependencias():
        aviso("Las dependencias del codigo han cambiado desde la ultima vez.")
        detalle("Arreglalo con:  python instalar.py --reparar")
        return 1
    ok(f"Instalacion al dia (version {ahora}).")
    return 0


def _estado_instalacion(venv: Path) -> tuple[str, str]:
    """Diagnostica el entorno. Devuelve (estado, motivo).

    Estados: "roto" (el Python no arranca), "incompleto" (arranca pero le
    faltan paquetes) y "bien".
    """
    if not entorno_sano(venv):
        return "roto", "su Python no arranca"
    completo, faltan = entorno_completo(venv)
    if not completo:
        return "incompleto", "le faltan " + ", ".join(faltan)
    return "bien", ""


def reparar() -> int:
    """--reparar: deja la instalacion al dia, y no toca nada si ya lo esta.

    Cuatro cosas se miran, y solo se rehace lo que hace falta:
      · que el interprete del entorno arranque (puede haber muerto con una
        actualizacion de Python y dejar el venv con buena pinta pero inservible);
      · que esten los paquetes del nucleo, no solo un Python vivo;
      · la version, comparada con JARVIS/VERSION;
      · la huella de las dependencias, por si se anadio algo nuevo.
    """
    print()
    paso("Miro que hay puesto...")
    ahora = version_actual()
    v = ruta_venv_existente()

    if not v:
        aviso("No hay entorno instalado: hago la instalacion entera.")
        return instalar()

    s = leer_sello(v)
    puesta = str(s.get("version") or "")
    huella_ahora = huella_dependencias()
    estado_venv, motivo = _estado_instalacion(v)

    if estado_venv == "roto":
        error(f"El entorno de {v} esta roto: {motivo}.")
        detalle("Suele pasar tras actualizar Python: el venv sigue apuntando")
        detalle("al interprete viejo, que ya no esta. Lo rehago.")
        return _instalar_extras(True, _extras_del_sello(s))

    if estado_venv == "incompleto":
        aviso(f"El entorno esta vivo pero le falta cosido: {motivo}.")
        detalle("Instalo solo lo que falta; no rehago el entorno entero.")
        return _instalar_extras(False, _extras_del_sello(s))

    if not puesta:
        aviso("No hay testigo de version, pero el entorno esta bien.")
        ok(f"Sello la version {ahora} y no instalo nada.")
        escribir_sello(v, _extras_del_sello(s))
        return 0

    c = compara(puesta, ahora)
    extras = _extras_del_sello(s)
    # Los extras se comprueban SIEMPRE, aunque la version y la huella cuadren:
    # son la unica comprobacion que detecta que se desinstalo algo a mano o que
    # el testigo se escribio mal.
    extras_perdidos = extras_faltantes(v, extras)
    if extras_perdidos:
        aviso(f"Faltan grupos opcionales: {', '.join(extras_perdidos)}.")
        detalle("El testigo los da por instalados, pero no lo estan. Los instalo.")
        return _instalar_extras(False, extras)

    if c == 0 and s.get("dependencias") == huella_ahora:
        ok(f"Ya esta al dia (version {ahora}). No toco nada.")
        return 0

    if c < 0:
        paso(f"Actualizo: hay {ahora} en el codigo y tienes {puesta}.")
    if s.get("dependencias") and s["dependencias"] != huella_ahora:
        paso("Las dependencias han cambiado: las reinstalo.")
    return _instalar_extras(False, _extras_del_sello(s))


def _extras_del_sello(s: dict) -> list[str]:
    """Que grupos se instalaron la ultima vez, para no perderlos.

    Si no hay testigo (instalacion anterior a esto), se asumen los que una
    instalacion normal pondria: en Windows el de escritorio. Antes se devolvia
    una lista vacia y --reparar se comia el nucleo y nada mas, con lo que la
    vision se quejaba de que le faltaba mss cada minuto y el escritorio se
    quedaba sin automatizacion de ventanas.
    """
    extras = list(s.get("extras") or [])
    if not extras and ES_WINDOWS:  # sin testigo: se asume lo de escritorio
        extras = ["escritorio"]
    return extras


# Un modulo representativo de cada grupo opcional, para poder comprobar si esta
# de verdad instalado en vez de fiarse solo del testigo.
MODULOS_EXTRA = {
    "escritorio": ("win32api", "pywinauto", "comtypes", "mss"),
    "voz": ("sounddevice",),
}

_TEST_IMPORTS = (
    "import importlib.util\n"
    "faltan = []\n"
    "for m in {nombres!r}:\n"
    "    try:\n"
    "        if importlib.util.find_spec(m) is None:\n"
    "            faltan.append(m)\n"
    "    except Exception:\n"
    "        faltan.append(m)\n"
    "print(','.join(faltan))\n"
)


def extras_faltantes(venv: Path, extras: list[str]) -> list[str]:
    """Que grupos opcionales NO estan instalados de verdad.

    El testigo dice lo que se quiso instalar, no lo que esta. Y --reparar se
    limitaba a version y huella, con lo que daba "ya esta al dia" con el grupo
    de escritorio entero sin instalar: la vision se quejaba de que no encontraba
    mss cada minuto y la automatizacion de ventanas no existia. Ahora se
    comprueba el entorno de verdad.
    """
    faltan: list[str] = []
    try:
        py = python_del_venv(venv)
    except RuntimeError:
        return list(extras)
    for grupo in extras:
        nombres = MODULOS_EXTRA.get(grupo)
        if not nombres:
            continue
        codigo, salida = ejecutar(
            [str(py), "-c", _TEST_IMPORTS.format(nombres=list(nombres))], timeout=120)
        if codigo != 0:
            faltan.append(grupo)
            continue
        ausentes = [x for x in (salida.strip().splitlines() or [""])[-1].split(",") if x]
        if ausentes:
            faltan.append(grupo)
    return faltan


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
            avisar_fallo(nombre, salida)
            if nombre == "servidor" and obligatorio:
                motivo, _ = diagnosticar(salida)
                error(f"Sin el grupo del servidor JARVIS no arranca ({motivo}).")
                sys.exit(1)
    return fallidos


def instalar_extras_uvicorn(py: Path) -> list[str]:
    """Pone los aceleradores de uvicorn, uno a uno y sin que un fallo tumbe nada.

    Antes venian dentro de uvicorn[standard] y compartian grupo con fastapi, de
    modo que un solo paquete sin rueda para tu Python (PyYAML, tipicamente)
    tumbaba el servidor entero. Ahora se prueban por separado: los que no tienen
    rueda simplemente no se ponen, y JARVIS funciona igual de rapido casi.
    """
    paso("Aceleradores de uvicorn (opcionales)...")
    puestos: list[str] = []
    for paquete, para_que in EXTRAS_UVICORN:
        codigo, salida = ejecutar(
            [str(py), "-m", "pip", "install", "--no-warn-script-location",
             "--disable-pip-version-check", paquete],
            timeout=900,
        )
        if codigo == 0:
            puestos.append(paquete)
        else:
            detalle(f"{paquete}: sin rueda para Python {platform.python_version()} "
                    f"({para_que}). No es problema: JARVIS funciona igual.")
    if puestos:
        ok("Puestos: " + ", ".join(puestos))
    else:
        aviso("Ninguno de los aceleradores tiene rueda para tu Python. Da igual.")
    return puestos


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


def esperar_si_falla() -> None:
    """Si esto se ha abierto a doble clic, la consola se cierra al salir y el
    error se va sin leer. Con una entrada interactiva se espera a una tecla."""
    try:
        if os.name == "nt" and sys.stdin is not None and sys.stdin.isatty():
            input("\n  Pulsa Intro para cerrar...")
    except (EOFError, KeyboardInterrupt):
        pass


# --- Programa ----------------------------------------------------------------
def _instalar_extras(rehacer: bool, extras: list[str]) -> int:
    """Instala el nucleo y los grupos extra que se le pidan, y sella la version.

    Es idempotente a proposito: --reparar pasa por aqui sin tocar lo que ya esta
    bien, porque pip solo descarga lo que falta o ha cambiado.
    """
    venv = crear_entorno(rehacer)
    py = python_del_venv(venv)
    actualizar_pip(py)

    fallidos: list[str] = []
    print()
    paso("Dependencias")
    fallidos += instalar_grupos(py, GRUPOS_NUCLEO)
    instalar_extras_uvicorn(py)

    for grupo in extras:
        if grupo == "escritorio":
            if ES_WINDOWS:
                fallidos += instalar_grupos(py, GRUPOS_ESCRITORIO, obligatorio=False)
            else:
                detalle("El escritorio solo es de Windows; me lo salto.")
        elif grupo == "voz":
            fallidos += instalar_grupos(py, GRUPOS_VOZ, obligatorio=False)
            fallidos += instalar_grupos(py, GRUPOS_PESADOS, obligatorio=False)

    return _cierre(venv, py, fallidos, extras)


def _cierre(venv: Path, py: Path, fallidos: list[str], extras: list[str]) -> int:
    bien = verificar(py)
    # Solo se sella si la instalacion sirve para algo: sellar una instalacion
    # rota haria que --reparar dijera "ya esta al dia" sin que lo este.
    if bien:
        escribir_sello(venv, list(extras))

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
    print("  Ver si esto esta al dia:")
    print(f"    python {Path(__file__).name} --estado")
    print()
    # Codigo 0 solo si esta todo bien. Con avisos se devuelve 1 para que el
    # lanzador (.bat) lo diga y no cante un "instalacion terminada" en falso.
    return 0 if (bien and not fallidos) else 1


# Los grupos opcionales que se han pedido en esta ejecucion.
def instalar(rapido: bool = False, todo: bool = False, rehacer: bool = False) -> int:
    """La instalacion de siempre: nucleo + lo que se haya pedido."""
    extras: list[str] = [] if rapido else ["escritorio"]
    if todo:
        extras.append("voz")
    return _instalar_extras(rehacer, extras)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--rapido", action="store_true",
                    help="solo lo esencial, sin escritorio ni voz")
    ap.add_argument("--todo", action="store_true",
                    help="incluye voz y modelos locales (bastante pesado)")
    ap.add_argument("--sin-claves", action="store_true", help="no preguntar por las claves")
    ap.add_argument("--rehacer", action="store_true", help="rehacer el entorno desde cero")
    ap.add_argument("--reparar", action="store_true",
                    help="dejar la instalacion al dia; si ya lo esta, no hace nada")
    ap.add_argument("--solo-claves", action="store_true", help="solo configurar las claves")
    ap.add_argument("--estado", action="store_true",
                    help="decir que version esta puesta, sin tocar nada")
    ap.add_argument("--ayuda", "-h", action="store_true")
    args = ap.parse_args()

    if args.ayuda:
        print(__doc__)
        return 0

    print()
    print("  J.A.R.V.I.S.  ·  instalacion")
    print("  " + "-" * 40)
    print()

    if args.estado:
        return estado()

    comprobar_python()

    if args.solo_claves:
        preguntar_claves(forzar=True)
        return 0

    if args.reparar:
        return reparar()

    if not args.sin_claves:
        print()
        preguntar_claves()

    return instalar(rapido=args.rapido, todo=args.todo, rehacer=args.rehacer)


if __name__ == "__main__":
        try:
            _codigo = main()
        except KeyboardInterrupt:
            print()
            aviso("Cancelado por el usuario.")
            _codigo = 130
        # A doble clic la consola se cierra al salir y el error se va sin leer.
        if _codigo:
            esperar_si_falla()
        sys.exit(_codigo)