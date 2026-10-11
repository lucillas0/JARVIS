# -*- coding: utf-8 -*-
"""Instalar aplicaciones del ordenador con winget.

Por qué existe: "instálame LibreOffice" no es un problema de modelo, es una
consulta exacta a un catálogo. Antes de este módulo, esa pregunta ida al LLM,
que necesitaba una llamada a internet para averiguar el nombre correcto del
paquete y acababa muchas veces sin installing nada útil.

Aquí se resuelve en local y en milisegundos:

1. Catálogo propio con los identificadores de winget VERIFICADOS uno a uno.
   No se fía de winget search para lo conocido, porque searching devuelve
   cualquier cosa: buscando "VLC" salía un puente de Jellyfin, y buscando
   "Python" un constructor de interfaces. Instalar eso habría sido un
   desastre, así que lo conocido va a mano y verificado.
2. Si no está en el catálogo, se pregunta a winget, pero SOLO se instala si la
   coincidencia es segura. Si hay duda, se ofrecen los candidatos y que el
   señor elija: instalar la aplicación equivocada es peor que preguntar.
3. Antes de instalar se comprueba si ya está presente, para no repetir.
"""
import logging
import re
import subprocess
import threading

log = logging.getLogger("tools.install")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIMEOUT_BUSQUEDA = 40
_TIMEOUT_INSTALACION = 15 * 60

# --------------------------------------------------------------------- #
# Catálogo verificado: alias -> (nombre legible, id de winget)
# --------------------------------------------------------------------- #
CATALOGO = {
    "platformtools": ("Platform Tools de Android (adb, para móviles)",
                      "Google.PlatformTools"),
    "adb": ("Platform Tools de Android (adb, para móviles)",
            "Google.PlatformTools"),
    "libreoffice": ("LibreOffice", "TheDocumentFoundation.LibreOffice"),
    "libre": ("LibreOffice", "TheDocumentFoundation.LibreOffice"),
    "onlyoffice": ("ONLYOFFICE", "ONLYOFFICE.DesktopEditors"),
    "brave": ("Brave", "Brave.Brave"),
    "wireshark": ("Wireshark", "WiresharkFoundation.Wireshark"),
    "wiresharkkiwi": ("Wireshark", "WiresharkFoundation.Wireshark"),
    "7zip": ("7-Zip", "7zip.7zip"),
    "sietezip": ("7-Zip", "7zip.7zip"),
    "vlc": ("VLC media player", "VideoLAN.VLC"),
    "vlcmediaplayer": ("VLC media player", "VideoLAN.VLC"),
    "chrome": ("Google Chrome", "Google.Chrome"),
    "googlechrome": ("Google Chrome", "Google.Chrome"),
    "firefox": ("Mozilla Firefox", "Mozilla.Firefox"),
    "mozilla": ("Mozilla Firefox", "Mozilla.Firefox"),
    "vscode": ("Visual Studio Code", "Microsoft.VisualStudioCode"),
    "code": ("Visual Studio Code", "Microsoft.VisualStudioCode"),
    "visualstudiocode": ("Visual Studio Code", "Microsoft.VisualStudioCode"),
    "notepadplusplus": ("Notepad++", "Notepad++.Notepad++"),
    "notepadpp": ("Notepad++", "Notepad++.Notepad++"),
    # "notepad++" al normalizarse se queda en "notepad", así que también se
    # registra plano. El Bloc de notas de Windows ya viene de fábrica, así que
    # cuando piden instalar "notepad" lo razonable es Notepad++.
    "notepad": ("Notepad++", "Notepad++.Notepad++"),
    "gimp": ("GIMP", "GIMP.GIMP"),
    "obs": ("OBS Studio", "OBSProject.OBSStudio"),
    "obsstudio": ("OBS Studio", "OBSProject.OBSStudio"),
    "audacity": ("Audacity", "Audacity.Audacity"),
    "powertoys": ("PowerToys", "Microsoft.PowerToys"),
    "docker": ("Docker Desktop", "Docker.DockerDesktop"),
    "dockerdesktop": ("Docker Desktop", "Docker.DockerDesktop"),
    "git": ("Git", "Git.Git"),
    "python": ("Python 3.12", "Python.Python.3.12"),
    "python3": ("Python 3.12", "Python.Python.3.12"),
    "node": ("Node.js", "OpenJS.NodeJS"),
    "nodejs": ("Node.js", "OpenJS.NodeJS"),
    "zoom": ("Zoom", "Zoom.Zoom"),
    "telegram": ("Telegram", "Telegram.TelegramDesktop"),
    "discord": ("Discord", "Discord.Discord"),
    "steam": ("Steam", "Valve.Steam"),
    "spotify": ("Spotify", "Spotify.Spotify"),
    "putty": ("PuTTY", "PuTTY.PuTTY"),
    "winscp": ("WinSCP", "WinSCP.WinSCP"),
    "heidisql": ("HeidiSQL", "HeidiSQL.HeidiSQL"),
    "dbeaver": ("DBeaver", "DBeaver.DBeaver.Community"),
    "mysql": ("MySQL", "Oracle.MySQL"),
    "mariadb": ("MariaDB", "MariaDB.Server"),
    "postgres": ("PostgreSQL 16", "PostgreSQL.PostgreSQL.16"),
    "postgresql": ("PostgreSQL 16", "PostgreSQL.PostgreSQL.16"),
    "mongodb": ("MongoDB", "MongoDB.Server"),
    "androidstudio": ("Android Studio", "Google.AndroidStudio"),
    "sublime": ("Sublime Text 4", "SublimeHQ.SublimeText.4"),
    "sublimetext": ("Sublime Text 4", "SublimeHQ.SublimeText.4"),
    "sharex": ("ShareX", "ShareX.ShareX"),
    "rufus": ("Rufus", "Rufus.Rufus"),
    "everything": ("Everything", "voidtools.Everything"),
    "ditto": ("Ditto", "Ditto.Ditto"),
    "sumatra": ("SumatraPDF", "SumatraPDF.SumatraPDF"),
    "sumatrapdf": ("SumatraPDF", "SumatraPDF.SumatraPDF"),
    "calibre": ("calibre", "calibre.calibre"),
    "zotero": ("Zotero", "DigitalScholar.Zotero"),
    "anki": ("Anki", "Anki.Anki"),
    "teamviewer": ("TeamViewer", "TeamViewer.TeamViewer"),
    "winrar": ("WinRAR", "RARLab.WinRAR"),
    "crystaldiskinfo": ("CrystalDiskInfo", "CrystalDewWorld.CrystalDiskInfo"),
    "foobar2000": ("foobar2000", "PeterPawlowski.foobar2000"),
    "visualstudio": ("Visual Studio 2022", "Microsoft.VisualStudio.2022.Community"),
    "java": ("OpenJDK 21", "Oracle.JDK.21"),
    "jdk": ("OpenJDK 21", "Oracle.JDK.21"),
    "openjdk": ("OpenJDK 21", "Oracle.JDK.21"),
    "claude": ("Claude", "Anthropic.Claude"),
    "claudedesktop": ("Claude", "Anthropic.Claude"),
    "intellij": ("IntelliJ IDEA", "JetBrains.IntelliJIDEA.Community"),
    "idea": ("IntelliJ IDEA", "JetBrains.IntelliJIDEA.Community"),
    "postman": ("Postman", "Postman.Postman"),
    "virtualbox": ("VirtualBox", "Oracle.VirtualBox"),
    "virtualboxoracle": ("VirtualBox", "Oracle.VirtualBox"),
}

_done_hooks: list = []


def set_done_hook(fn) -> None:
    """main.py se registra aquí para que avise cuando la instalación acaba."""
    _done_hooks.append(fn)


def _avisa(result: dict) -> None:
    for h in list(_done_hooks):
        try:
            h(result)
        except Exception as e:
            log.debug("hook de instalación: %s", e)


def _normaliza(texto: str) -> str:
    """Minúsculas, sin acentos y sin puntuación: 'OnlyOffice' -> 'onlyoffice'."""
    t = (texto or "").lower().strip()
    t = re.sub(r"[áàä]", "a", t)
    t = re.sub(r"[éèë]", "e", t)
    t = re.sub(r"[íìï]", "i", t)
    t = re.sub(r"[óòö]", "o", t)
    t = re.sub(r"[úùü]", "u", t)
    t = re.sub(r"[ñ]", "n", t)
    return re.sub(r"[^a-z0-9]", "", t)


def _winget(args: list, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(["winget"] + args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "winget no está instalado en este equipo."
    except subprocess.TimeoutExpired:
        return 124, "Se agotó el tiempo de espera."
    except Exception as e:
        return 1, str(e)


def ya_instalada(winget_id: str) -> bool:
    code, _ = _winget(["list", "--id", winget_id, "--exact",
                       "--source", "winget", "--accept-source-agreements"], 90)
    return code == 0


def _buscar_en_winget(consulta: str) -> list[dict]:
    code, txt = _winget(["search", "--name", consulta, "--source", "winget",
                         "--accept-source-agreements"], _TIMEOUT_BUSQUEDA)
    if code != 0:
        return []
    out = []
    for ln in txt.splitlines():
        ln = ln.rstrip()
        if not ln.strip() or set(ln.strip()) <= set("- "):
            continue
        if ln.split()[0].lower() in ("name", "nombre"):
            continue
        m = re.search(r"\s([A-Za-z0-9][\w.\-]*\.[\w.\-]+)\s+(\S+)\s*$", ln)
        if m:
            out.append({"name": ln[:m.start()].strip(), "id": m.group(1)})
    return out[:6]


def resolver(nombre: str) -> dict:
    """Descubre qué aplicación es 'nombre' y con qué paquete se instala.

    Devuelve {"status", "app", "id", "candidatos"}:
      resolved   -> se sabe exactamente cuál es
      ambiguous  -> hay varias parecidas: que el señor elija
      unknown    -> winget no lo encuentra
    """
    limpio = (nombre or "").strip(" .¡!¿?\"'")
    if not limpio:
        return {"status": "unknown", "candidatos": []}
    key = _normaliza(limpio)
    if not key:
        return {"status": "unknown", "candidatos": []}

    # 1) Catálogo verificado: respuesta instantánea y sin sorpresas.
    if key in CATALOGO:
        app, wid = CATALOGO[key]
        return {"status": "resolved", "app": app, "id": wid, "candidatos": []}
    # Coincidencia parcial única dentro del catálogo ("brave browser" -> Brave).
    parciales = {k: v for k, v in CATALOGO.items()
                 if len(key) >= 4 and (key in k or k in key)}
    if len(parciales) == 1:
        app, wid = list(parciales.values())[0]
        return {"status": "resolved", "app": app, "id": wid, "candidatos": []}

    # 2) winget, pero solo si la coincidencia es segura.
    cands = _buscar_en_winget(limpio)
    if not cands:
        cands = _buscar_en_winget(key)
    if not cands:
        return {"status": "unknown", "candidatos": []}

    seguros = []
    for c in cands:
        cn = _normaliza(c["name"])
        if cn == key or key in cn or cn in key:
            seguros.append(c)
    if len(seguros) == 1:
        c = seguros[0]
        return {"status": "resolved", "app": c["name"], "id": c["id"], "candidatos": []}
    return {"status": "ambiguous", "app": "", "id": "",
            "candidatos": [{"name": c["name"], "id": c["id"]} for c in cands[:5]]}


def _instalar(winget_id: str) -> dict:
    code, txt = _winget([
        "install", "--id", winget_id, "--exact", "--source", "winget",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity",
    ], _TIMEOUT_INSTALACION)
    bajo = (txt or "").lower()
    if code == 0 or "ya está instalado" in bajo or "already installed" in bajo:
        return {"ok": True, "id": winget_id, "detalle": txt[-400:]}
    if code == 124:
        return {"ok": False, "id": winget_id,
                "detalle": "Se pasó del tiempo de espera. Puede que siga instalándose."}
    return {"ok": False, "id": winget_id, "detalle": (txt or "")[-500:]}


def _hilo_instalar(app: str, winget_id: str) -> None:
    def trabajo():
        res = _instalar(winget_id)
        if res["ok"]:
            # Para que "abre X" funcione de inmediato sin reiniciar nada.
            try:
                from tools.system_tools import refresh_index
                refresh_index()
            except Exception as e:
                log.debug("refresco de índice: %s", e)
        _avisa({"app": app, "id": winget_id, **res})
    threading.Thread(target=trabajo, name="instalar-app", daemon=True).start()


def start_install(nombre: str) -> dict:
    """Resuelve y arranca la instalación en segundo plano. No bloquea: una
    instalación puede tardar minutos y el hilo del chat no debe quedarse colgado.
    """
    r = resolver(nombre)
    if r["status"] != "resolved":
        return r
    app, wid = r["app"], r["id"]
    if ya_instalada(wid):
        r["status"] = "already"
        r["ya_tenia"] = True
        return r
    _hilo_instalar(app, wid)
    r["status"] = "started"
    return r


def install_app_handler(params: dict) -> dict:
    """Herramienta para el modelo: instala una aplicación por su nombre."""
    nombre = params.get("app_name") or params.get("name") or params.get("app")
    if not nombre:
        raise ValueError("Falta el nombre de la aplicación")
    return start_install(nombre)


INSTALL_TOOLS = [
    {"name": "install_app",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"app_name": "str"},
     "description": ("INSTALA una aplicación en el ordenador por su nombre "
                     "(LibreOffice, ONLYOFFICE, Brave, Wireshark...). Usa esto "
                     "cuando el señor pida instalar o poner un programa. "
                     "No abras la página de descargas: esto instala de verdad."),
     "label": "Instalando aplicación",
     "handler": install_app_handler},
]

TOOLS = INSTALL_TOOLS
