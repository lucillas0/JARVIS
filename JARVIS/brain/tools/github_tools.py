# -*- coding: utf-8 -*-
"""Instalar programas desde un repositorio de GitHub.

El caso real es este: el señor ve un proyecto en GitHub y dice "instálame eso".
Lo que se hace es descargar la PUBLICACIÓN oficial del proyecto (no código
suelto de cualquiera), elegir el paquete que corresponde a su Windows y
instalarlo.

Dos decisiones importantes, por experiencia con esto:

1. Elegir bien el archivo. Las publicaciones traen veinte o treintaAdjuntos y
   se mezclan Windows, Linux, macOS, ARM y 32 bits. Bailando por el primero
   que "suene" a Windows se acaba instalando un .deb o un binario de Apple.
   Aquí se filtra por sistema y arquitectura antes de mirar la extensión, y si
   no hay nada válido para Windows x64 se dice, en vez de instalar cualquier
   cosa.

2. No compilar código. Construir desde el fuente exige cadena de compiladores y
   puede tardar media hora o fallar. Cuando no hay publicación con
   instalador, se informa del repositorio y de lo que sí hay, y se le ofrece
   clonar el código. Installar siempre la publicación oficial.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import threading
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("tools.github")

_UA = {"User-Agent": "JARVIS-installer", "Accept": "application/vnd.github+json"}
_TIMEOUT_API = 30
_TIMEOUT_DESCARGA = 20 * 60
_PROGRAMS = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
_DESCARGAS = Path(os.environ.get("TEMP", "")) / "jarvis-instalaciones"

_done_hooks: list = []


def set_done_hook(fn) -> None:
    _done_hooks.append(fn)


def _avisa(res: dict) -> None:
    for h in list(_done_hooks):
        try:
            h(res)
        except Exception as e:
            log.debug("hook github: %s", e)


# --------------------------------------------------------------------- #
# 1) Reconocer el repositorio
# --------------------------------------------------------------------- #
def parse_repo(texto: str):
    """Saca (propietario, repositorio) de lo que haya dicho el señor.
    Acepta la URL completa, 'owner/repo' o 'owner.github.com/repo'."""
    t = (texto or "").strip().strip("<>\"' ")
    t = re.sub(r"^https?://(www\.)?github\.com/", "", t, flags=re.I)
    t = re.sub(r"^github\.com/", "", t, flags=re.I)
    t = re.sub(r"^git@github\.com:", "", t, flags=re.I)
    t = re.sub(r"^www\.github\.com/", "", t, flags=re.I)
    t = t.split("?")[0].split("#")[0]
    m = re.match(r"^([A-Za-z0-9][\w.\-]*)/([A-Za-z0-9][\w.\-]*)", t)
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git")


# --------------------------------------------------------------------- #
# 2) La publicación oficial
# --------------------------------------------------------------------- #
def _api(url: str):
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=_TIMEOUT_API) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log.debug("api %s: %s", url, e)
        return None


def latest_release(owner: str, repo: str):
    return _api(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")


def repo_info(owner: str, repo: str):
    return _api(f"https://api.github.com/repos/{owner}/{repo}")


# --------------------------------------------------------------------- #
# 3) Elegir el paquete de Windows x64
# --------------------------------------------------------------------- #
_OTROS_SISTEMAS = re.compile(
    r"(\.deb$|\.rpm$|\.apk$|\.dmg$|\.pkg$|\.appimage$|darwin|apple|macos|osx|"
    r"linux|musl|android|freebsd|netbsd|openbsd|alpine|\.snap$)", re.I)
_NO_ES_PAQUETE = re.compile(
    r"(symbols|symbol|sbom|checksum|sha\d*|sha256sums|manifest|\.asc$|\.sig$|"
    r"\.sha256$|\.sha512$|\.md5$|\.txt$|\.json$|\.yml$|\.yaml$|debug|"
    r"source\s*code|\.tar\.gz$|\.tar\.xz$|\.tar\.bz2$|linux|darwin)", re.I)
_ARM = re.compile(r"(arm64|aarch64|armv7|armhf|\barm\b)", re.I)
_32BIT = re.compile(r"(i[3-6]86|ia32|^x86$|win32|32bit|32-bit)", re.I)
_64BIT = re.compile(r"(x86[_-]?64|amd64|win64|x64)", re.I)
_EXT_PESO = [(".msi", 100), (".msi.x64", 99), (".exe", 80), (".msix", 70),
             (".msixbundle", 69), (".appx", 68), (".appxbundle", 67),
             (".zip", 50), (".7z", 40), (".tar.xz", 30), (".tar.gz", 25)]
_SILENCIOS = ("setup", "installer", "install", "x64", "amd64", "win64", "win-")


def elegir_paquete_windows(assets: list) -> dict | None:
    """Devuelve el adjunto adecuado para Windows x64, o None si no hay ninguno."""
    aptos = []
    for a in assets or []:
        n = (a.get("name") or "").strip()
        if not n:
            continue
        bajo = n.lower()
        if _OTROS_SISTEMAS.search(bajo) or _NO_ES_PAQUETE.search(bajo):
            continue
        if not any(bajo.endswith(e) for e, _ in _EXT_PESO):
            continue
        apto = True
        if _ARM.search(bajo):
            apto = False
        elif _32BIT.search(bajo) and not _64BIT.search(bajo):
            apto = False
        if apto:
            aptos.append(a)
    if not aptos:
        return None
    # Si hay algo de 64 bits, se descarta cualquier resto dudoso.
    con64 = [a for a in aptos if _64BIT.search(a["name"].lower())]
    if con64:
        aptos = con64

    def peso(a):
        bajo = a["name"].lower()
        for ext, p in _EXT_PESO:
            if bajo.endswith(ext):
                # .msi gana siempre; entre .exe, manda el que parezca instalador
                if ext == ".exe" and not any(s in bajo for s in _SILENCIOS):
                    p -= 30
                return p
        return 0

    aptos.sort(key=lambda a: (-peso(a), a["name"]))
    return aptos[0]


# --------------------------------------------------------------------- #
# 4) Descarga
# --------------------------------------------------------------------- #
def descargar(url: str, nombre: str, progreso=None) -> Path:
    _DESCARGAS.mkdir(parents=True, exist_ok=True)
    destino = _DESCARGAS / nombre
    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_DESCARGA) as r:
        total = int(r.headers.get("Content-Length") or 0)
        leido = 0
        with open(destino, "wb") as f:
            while True:
                trozo = r.read(262144)
                if not trozo:
                    break
                f.write(trozo)
                leido += len(trozo)
                if progreso and total:
                    progreso(min(100, int(leido * 100 / total)))
    return destino


# --------------------------------------------------------------------- #
# 5) Instalar según el tipo de paquete
# --------------------------------------------------------------------- #
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _atajo(nombre: str, destino_exe: Path) -> str:
    """Crea un acceso directo en el menú Inicio para los portable."""
    try:
        carpeta = Path(os.environ.get("APPDATA", "")) / \
            "Microsoft/Windows/Start Menu/Programs"
        carpeta.mkdir(parents=True, exist_ok=True)
        lnk = carpeta / f"{nombre}.lnk"
        ps = ("$s=New-Object -ComObject WScript.Shell;"
              f"$l=$s.CreateShortcut('{lnk}');"
              f"$l.TargetPath='{destino_exe}';$l.Save()")
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                        "-Command", ps], capture_output=True, timeout=40,
                       creationflags=_NO_WINDOW)
        return str(lnk)
    except Exception as e:
        log.debug("atajo: %s", e)
        return ""


def _instalar_paquete(nombre: str, archivo: Path) -> dict:
    ext = archivo.suffix.lower()
    if ext == ".msi":
        # Silencioso y sin reiniciar. msiexec devuelve códigos raros, así que
        # se traducen: 0 bien, 3010 bien pero pide reiniciar, 1603 fallo.
        p = subprocess.run(["msiexec", "/i", str(archivo), "/qn", "/norestart"],
                           capture_output=True, timeout=30 * 60,
                           creationflags=_NO_WINDOW)
        code = p.returncode
        # msiexec devuelve de verdad en un proceso aparte, así que se consulta
        # el estado instalado del propio paquete.
        if code in (0, 1605, 3010):
            return {"ok": True, "metodo": "MSI", "reiniciar": code == 3010}
        return {"ok": False, "metodo": "MSI",
                "detalle": f"msiexec devolvió {code}. El instalador está en {archivo}"}
    if ext == ".exe":
        # Aquí no se inventan parámetros de silencio: cada instalador usa los
        # suyos y adivinar deja al señor con un proceso colgado. Se abre y se
        # le avisa para que lo termine él.
        try:
            subprocess.Popen([str(archivo)], close_fds=True)
            return {"ok": True, "metodo": "instalador visible",
                    "detalle": f"Se ha abierto el instalador: {archivo}. "
                               "Termine de instalarlo en la ventana que ha salido."}
        except Exception as e:
            return {"ok": False, "metodo": "exe", "detalle": str(e)}
    if ext in (".zip", ".7z", ".tar.gz", ".tar.xz"):
        destino = _PROGRAMS / nombre
        try:
            destino.mkdir(parents=True, exist_ok=True)
            if ext == ".zip":
                with zipfile.ZipFile(archivo) as z:
                    z.extractall(destino)
            elif ext == ".7z":
                seven = shutil.which("7z") or shutil.which("7z.exe")
                if not seven:
                    return {"ok": False, "metodo": "7z",
                            "detalle": f"Archivo descomprimido en {destino}, pero no hay "
                                       "7-Zip para abrir el paquete."}
                subprocess.run([seven, "x", "-y", f"-o{destino}", str(archivo)],
                               capture_output=True, timeout=600, creationflags=_NO_WINDOW)
            else:
                modo = "r:gz" if ext == ".tar.gz" else "r:xz"
                with tarfile.open(archivo, modo) as t:
                    t.extractall(destino, filter="data")
        except Exception as e:
            return {"ok": False, "metodo": ext, "detalle": str(e)}
        # Se busca el ejecutable principal y se pone acceso directo.
        exe = None
        for cand in sorted(destino.rglob("*.exe")):
            if not cand.name.lower().startswith(("unins", "setup", "vcredist")):
                exe = cand
                break
        atajo = _atajo(nombre, exe) if exe else ""
        return {"ok": True, "metodo": "portable", "ruta": str(destino),
                "ejecutable": str(exe) if exe else "",
                "atajo": atajo,
                "detalle": (f"Portable instalado en {destino}. "
                            + ("Acceso directo creado en el menú Inicio." if atajo else ""))}
    return {"ok": False, "metodo": ext, "detalle": f"No sé instalar {ext}"}


# --------------------------------------------------------------------- #
# 6) Flujo completo
# --------------------------------------------------------------------- #
def _buscar_repositorio(nombre: str) -> dict:
    """Busca repositorios en GitHub por nombre, para cuando el señor no ha dicho
    de quién es ("instala ripgrep de github").

    OJO, y es importante: aquí NO se elige por el señor. Se probó aceptar el
    más popular y era un desastre: buscando "python" salía TheAlgorithms/Python
    en lugar de python/cpython, porque tiene más estrellas. Instalar código del
    repositorio equivocado es justo lo que hay que evitar, así que siempre se
    enseñan los candidatos y que elija él.
    """
    q = (nombre or "").strip()
    if not q or " " in q or len(q) < 2:
        return {"__candidatos__": [], "__motivo__": "sin_busqueda"}
    data = _api("https://api.github.com/search/repositories?q="
                + urllib.parse.quote(q) + "&sort=stars&per_page=6")
    if data is None:
        return {"__candidatos__": [], "__motivo__": "limite"}
    items = data.get("items") or []
    return {"__candidatos__": [
        {"name": it.get("full_name", ""), "stars": it.get("stargazers_count", 0),
         "desc": (it.get("description") or "")[:90]}
        for it in items[:6]]}


def _repo_por_nombre(texto: str) -> tuple:
    """Devuelve (owner, repo) o (None, info_de_busqueda)."""
    par = parse_repo(texto)
    if par:
        return par, None
    info = _buscar_repositorio(texto)
    if not info:
        return None, None
    if "__candidatos__" in info:
        return None, info
    return (info.get("owner", {}).get("login"),
            info.get("name")), None


def consultar_github(texto: str) -> dict:
    """Decide QUÉ se instalaría, sin descargar nada. Sirve para responder,
    para el modelo y para comprobar que la elección es la correcta."""
    par, info = _repo_por_nombre(texto)
    if info and "__candidatos__" in info:
        cands = info["__candidatos__"]
        if info.get("__motivo__") == "limite":
            return {"status": "limite", "repo": "", "tag": "", "candidatos": [],
                    "detalle": ("GitHub me está limitando las consultas por "
                                "momento. Dígamelo en un rato o pásame el "
                                "dueño/repo y no lo necesito.")}
        if not cands:
            return {"status": "no_repo", "candidatos": [],
                    "detalle": ("No encuentro ningún repositorio con ese "
                                "nombre en GitHub. Pásame el dueño/repo "
                                "completo si me lo ha pasado.")}
        # Con candidatos pero sin dueño del repo: que elija el señor.
        return {"status": "ambiguous", "repo": "", "tag": "",
                "candidatos": cands,
                "detalle": ("Hay varios repositorios con ese nombre y no "
                            "quiero instalar el que no es. Dígame el "
                            "dueño/repo.") }
    if not par or not par[0] or not par[1]:
        return {"status": "no_repo",
                "detalle": "No he reconocido el repositorio."}
    owner, repo = par
    full = f"{owner}/{repo}"
    rel = latest_release(owner, repo)
    if rel is None:
        return {"status": "sin_acceso", "repo": full,
                "detalle": "No he podido consultar GitHub o el repositorio no existe."}
    assets = rel.get("assets") or []
    etiqueta = rel.get("tag_name", "")
    if not assets:
        return {"status": "sin_publicacion", "repo": full, "tag": etiqueta,
                "detalle": (f"{full} no tiene publicación oficial con archivos para "
                            "descargar. Habría que compilar el código, y eso no lo hago "
                            "solo: dígamelo y lo clonamos.")}
    elegido = elegir_paquete_windows(assets)
    if not elegido:
        return {"status": "sin_paquete_windows", "repo": full, "tag": etiqueta,
                "candidatos": [a.get("name", "") for a in assets[:8]],
                "detalle": (f"La publicación {etiqueta} de {full} no trae ningún "
                            "instalador para Windows.")}
    return {"status": "ready", "repo": full, "tag": etiqueta,
            "paquete": elegido["name"], "url": elegido["browser_download_url"],
            "tamano": elegido.get("size", 0)}


def start_install_github(texto: str) -> dict:
    """Resuelve, descarga e instala desde GitHub. Devuelve de inmediato: la
    descarga va en un hilo aparte para no dejar colgado al chat."""
    r = consultar_github(texto)
    if r.get("status") != "ready":
        return r
    full = r["repo"]
    nombre_paq = r["paquete"]
    carpeta = full.split("/")[-1]
    etiqueta = r["tag"]
    url = r["url"]
    def trabajo():
        try:
            archivo = descargar(url, nombre_paq)
        except Exception as e:
            _avisa({"status": "fallo_descarga", "repo": full, "paquete": nombre_paq,
                    "detalle": f"No se pudo descargar: {e}"})
            return
        res = _instalar_paquete(carpeta, archivo)
        _avisa({"status": "instalado", "repo": full, "tag": etiqueta,
                "paquete": nombre_paq, "url": url, **res})

    threading.Thread(target=trabajo, name="github-install", daemon=True).start()
    return {"status": "started", "repo": full, "tag": etiqueta,
            "paquete": nombre_paq, "url": url}


def github_install_handler(params: dict) -> dict:
    ref = params.get("repo") or params.get("url") or params.get("name")
    if not ref:
        raise ValueError("Falta el repositorio de GitHub")
    return start_install_github(ref)


TOOLS = [
    {"name": "github_install",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"repo": "str"},
     "description": ("Descarga e instala la publicación oficial de un programa "
                     "de GitHub (URL o 'dueño/repo'). Úsalo cuando el señor diga "
                     "que instale algo que ha visto en GitHub."),
     "label": "Instalando desde GitHub",
     "handler": github_install_handler},
]
