"""Tools para la Raspberry Pi "lucillas" y su Nextcloud.

SSH: raspberry_exec / raspberry_read / raspberry_write.
Nextcloud: nextcloud_list / nextcloud_read / nextcloud_open.
Credenciales SOLO en .env (RASPBERRY_*/NEXTCLOUD_*).
"""
import logging

log = logging.getLogger("tools.raspberry")


def raspberry_exec_handler(params: dict) -> dict:
    from integrations.raspberry import ssh_exec
    command = (params.get("command") or "").strip()
    if not command:
        raise RuntimeError("Sin comando que ejecutar.")
    try:
        timeout = int(params.get("timeout") or 60)
    except Exception:
        timeout = 60
    return ssh_exec(command, timeout=timeout)


def raspberry_read_handler(params: dict) -> dict:
    from integrations.raspberry import ssh_read
    path = (params.get("path") or "").strip()
    if not path:
        raise RuntimeError("Sin ruta que leer.")
    return ssh_read(path)


def raspberry_write_handler(params: dict) -> dict:
    from integrations.raspberry import ssh_write
    path = (params.get("path") or "").strip()
    content = params.get("content") or ""
    if not path:
        raise RuntimeError("Sin ruta donde escribir.")
    return ssh_write(path, content)


def nextcloud_list_handler(params: dict) -> dict:
    from integrations.raspberry import nextcloud_list
    return nextcloud_list(params.get("path") or "")


def nextcloud_read_handler(params: dict) -> dict:
    from integrations.raspberry import nextcloud_read
    path = (params.get("path") or "").strip()
    if not path:
        raise RuntimeError("Sin ruta que leer en Nextcloud.")
    return nextcloud_read(path)


def nextcloud_open_handler(params: dict) -> dict:
    """Abre la interfaz web de Nextcloud (o una ruta) en el navegador."""
    import webbrowser
    path = (params.get("path") or "").strip("/")
    cfg = {}
    try:
        from config import Config
        cfg = {"url": Config.NEXTCLOUD_URL,
               "user": Config.NEXTCLOUD_USER,
               "pw": Config.NEXTCLOUD_PASSWORD}
    except Exception:
        pass
    url = ((cfg.get("url") or "https://lucillas.ddns.net/nextcloud")
           .rstrip("/") + ("/" + path if path else ""))
    webbrowser.open(url, new=2)
    return {"opened": url, "web": True}


RASPBERRY_TOOLS = [
    {"name": "raspberry_exec",
     "risk_level": "medium",
     "requires_confirmation": True,
     "parameters": {"command": "str", "timeout": "int opcional 10-600"},
     "description": "Ejecuta un comando por SSH en la Raspberry Pi (lucillas.ddns.net, usuario lucillas).",
     "label": "Ejecutando en la Raspberry",
     "handler": raspberry_exec_handler},
    {"name": "raspberry_read",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str ruta absoluta dentro de la Raspberry"},
     "description": "Lee un fichero de la Raspberry Pi por SSH.",
     "label": "Leyendo fichero de la Raspberry",
     "handler": raspberry_read_handler},
    {"name": "raspberry_write",
     "risk_level": "medium",
     "requires_confirmation": True,
     "parameters": {"path": "str", "content": "str"},
     "description": "Escribe (o sobrescribe) un fichero en la Raspberry Pi por SFTP.",
     "label": "Escribiendo en la Raspberry",
     "handler": raspberry_write_handler},
    {"name": "nextcloud_list",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str opcional, directorio en Nextcloud (vacío = raíz)"},
     "description": "Lista carpetas y archivos de Nextcloud del usuario Lucillas.",
     "label": "Mirando Nextcloud",
     "handler": nextcloud_list_handler},
    {"name": "nextcloud_read",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str ruta de archivo en Nextcloud"},
     "description": "Lee el contenido de un archivo de Nextcloud (si es texto).",
     "label": "Leyendo archivo de Nextcloud",
     "handler": nextcloud_read_handler},
    {"name": "nextcloud_open",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str opcional"},
     "description": "Abre la web de Nextcloud en el navegador.",
     "label": "Abriendo Nextcloud",
     "handler": nextcloud_open_handler},
]