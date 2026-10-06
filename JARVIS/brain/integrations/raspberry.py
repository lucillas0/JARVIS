"""Raspberry Pi "lucillas" vía SSH (paramiko) y Nextcloud vía WebDAV.

Servidor: lucillas.ddns.net — SSH en 22, Nextcloud en /nextcloud.
Credenciales SOLO en .env (RASPBERRY_* / NEXTCLOUD_*), jamás en logs.
"""
import logging
import os
import shlex
import threading

log = logging.getLogger("integrations.raspberry")


def _cfg():
    try:
        from config import Config
        return {
            "host": (Config.RASPBERRY_HOST or "").strip(),
            "port": int(Config.RASPBERRY_PORT or "22"),
            "user": (Config.RASPBERRY_USER or "").strip(),
            "password": (Config.RASPBERRY_PASSWORD or "").strip(),
            "nc_host": (Config.NEXTCLOUD_URL or "").strip(),
            "nc_user": (Config.NEXTCLOUD_USER or "").strip(),
            "nc_password": (Config.NEXTCLOUD_PASSWORD or "").strip(),
        }
    except Exception:
        log.warning("Raspberry: sin Credentials? usando env directo.")
        return {
            "host": os.environ.get("RASPBERRY_HOST", "lucillas.ddns.net").strip(),
            "port": int(os.environ.get("RASPBERRY_PORT", "22")),
            "user": os.environ.get("RASPBERRY_USER", "").strip(),
            "password": os.environ.get("RASPBERRY_PASSWORD", "").strip(),
            "nc_host": os.environ.get("NEXTCLOUD_URL", "https://lucillas.ddns.net/nextcloud").strip(),
            "nc_user": os.environ.get("NEXTCLOUD_USER", "").strip(),
            "nc_password": os.environ.get("NEXTCLOUD_PASSWORD", "").strip(),
        }


def _ssh_client(cfg):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=cfg["host"], port=cfg["port"],
              username=cfg["user"], password=cfg["password"],
              timeout=15, banner_timeout=15, auth_timeout=15)
    return c


def ssh_exec(command: str, timeout: int = 60, cwd: str = "") -> dict:
    """Ejecuta un comando en la Raspberry. Devuelve {output, error, code}."""
    cfg = _cfg()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return {"error": "Raspberry sin credenciales (RASPBERRY_USER/PASSWORD)."}
    try:
        c = _ssh_client(cfg)
    except Exception as e:
        return {"error": f"No pude conectar por SSH: {e}"}
    try:
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        stdin, stdout, stderr = c.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {"output": out, "error": err, "code": code}
    except Exception as e:
        return {"error": f"SSH ejecución falló: {e}"}
    finally:
        try:
            c.close()
        except Exception:
            pass


def ssh_read(path: str, timeout: int = 30) -> dict:
    """Lee un fichero remoto por SSH (sin volcarlo entero: hasta 200 KB)."""
    cfg = _cfg()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return {"error": "Raspberry sin credenciales (RASPBERRY_USER/PASSWORD)."}
    try:
        import paramiko
        from paramiko import SFTPClient
        c = _ssh_client(cfg)
        try:
            sftp: SFTPClient = c.open_sftp()
            st = sftp.stat(path)
            size = st.st_size
            if size > 250 * 1024:
                return {"error": f"Fichero muy grande ({size} bytes); leo solo un fragmento.",
                        "path": path, "size": size}
            with sftp.open(path, "r") as fh:
                content = fh.read().decode("utf-8", "replace")
            sftp.close()
            return {"content": content, "size": size, "path": path}
        finally:
            c.close()
    except Exception as e:
        return {"error": f"No pude leer {path}: {e}"}


def ssh_write(path: str, content: str, timeout: int = 30) -> dict:
    """Escribe un fichero remoto por SFTP (mkdir -p no incluido)."""
    cfg = _cfg()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return {"error": "Raspberry sin credenciales (RASPBERRY_USER/PASSWORD)."}
    try:
        import paramiko
        from paramiko import SFTPClient
        c = _ssh_client(cfg)
        try:
            sftp: SFTPClient = c.open_sftp()

            def _ensure(remotepath):
                parts = remotepath.split("/")[1:-1]
                cur = ""
                for p in parts:
                    cur = f"{cur}/{p}"
                    try:
                        sftp.stat(cur)
                    except IOError:
                        try:
                            sftp.mkdir(cur)
                        except Exception:
                            pass
            _ensure(path)
            with sftp.open(path, "w") as fh:
                fh.write(content.encode("utf-8"))
            sftp.close()
            return {"path": path, "bytes": len(content.encode("utf-8"))}
        finally:
            c.close()
    except Exception as e:
        return {"error": f"No pude escribir {path}: {e}"}


# --------------------------------------------------------------------------- #
# Nextcloud vía WebDAV (sincrónico con requests)
# --------------------------------------------------------------------------- #

def _nc_base(cfg):
    return (cfg["nc_host"] or "https://lucillas.ddns.net/nextcloud").rstrip("/")


def nextcloud_list(path: str = "") -> dict:
    """Lista un directorio de Nextcloud. path relativo a la raíz del usuario.
    Devuelve {items: [{name, type, size, modified}]}."""
    import requests
    from requests.auth import HTTPBasicAuth
    from xml.etree import ElementTree as ET
    cfg = _cfg()
    if not cfg["nc_user"] or not cfg["nc_password"]:
        return {"error": "Nextcloud sin credenciales (NEXTCLOUD_USER/PASSWORD)."}
    base = _nc_base(cfg)
    dav = f"{base}/remote.php/dav/files/{cfg['nc_user'].strip('/')}/{path.strip('/')}"
    try:
        r = requests.request("PROPFIND", dav + "/",
                             auth=HTTPBasicAuth(cfg["nc_user"], cfg["nc_password"]),
                             headers={"Depth": "1"}, timeout=25)
    except Exception as e:
        return {"error": f"No pude listar Nextcloud: {e}"}
    if r.status_code in (401, 403):
        return {"error": "Credenciales Nextcloud rechazadas (401/403)."}
    if r.status_code != 207:
        return {"error": f"WebDAV devolvió {r.status_code}."}
    ns = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
    items = []
    try:
        root = ET.fromstring(r.text)
        for resp in root.findall("d:response", ns):
            href = resp.findtext("d:href", "", ns).strip()
            name = href.rstrip("/").split("/")[-1]
            if not name:
                continue
            prop = resp.find("d:propstat", ns)
            if prop is None:
                continue
            p = prop.find("d:prop", ns)
            if p is None:
                continue
            is_dir = p.find("d:resourcetype/d:collection", ns) is not None
            size = int((p.findtext("d:getcontentlength", "0", ns) or "0"))
            modified = p.findtext("d:getlastmodified", "", ns)
            items.append({"name": name, "type": "folder" if is_dir else "file",
                          "size": size, "modified": modified})
    except Exception as e:
        return {"error": f"No pude parsear la respuesta: {e}"}
    items = [i for i in items if i["name"] not in
             {"", "home", "files", "trashbin", "avatar.png"}]
    return {"items": items, "path": path or "/"}


def nextcloud_read(path: str, limit: int = 200_000) -> dict:
    """Descarga un fichero de Nextcloud por WebDAV. Devuelve texto si es
    legible; si es binario o muy grande, info de tamaño."""
    import requests
    from requests.auth import HTTPBasicAuth
    cfg = _cfg()
    if not cfg["nc_user"] or not cfg["nc_password"]:
        return {"error": "Nextcloud sin credenciales (NEXTCLOUD_USER/PASSWORD)."}
    base = _nc_base(cfg)
    url = f"{base}/remote.php/dav/files/{cfg['nc_user'].strip('/')}/{path.strip('/')}"
    try:
        with requests.get(url, auth=HTTPBasicAuth(cfg["nc_user"], cfg["nc_password"]),
                          stream=True, timeout=30) as r:
            if r.status_code == 404:
                return {"error": f"No existe {path} en Nextcloud."}
            if r.status_code in (401, 403):
                return {"error": "Credenciales Nextcloud rechazadas (401/403)."}
            if r.status_code != 200:
                return {"error": f"WebDAV devolvió {r.status_code}."}
            size = int(r.headers.get("Content-Length", "0") or 0)
            data = b""
            for chunk in r.iter_content(8192):
                data += chunk
                if len(data) > limit:
                    return {"error": f"Fichero grande ({size} bytes); muestro solo un fragmento.",
                            "path": path, "size": size, "truncated": True}
    except Exception as e:
        return {"error": f"No pude leer de Nextcloud: {e}"}
    for enc in ("utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            return {"content": text, "size": len(data), "path": path}
        except UnicodeDecodeError:
            continue
    return {"error": "El fichero parece binario (no se muestra como texto).",
            "path": path, "size": len(data)}


def nextcloud_download_bytes(path: str, limit: int = 25_000_000) -> dict:
    """Descarga bytes crudos de Nextcloud por WebDAV (para indexar).
    Devuelve {data, size, path} o {error}."""
    import requests
    from requests.auth import HTTPBasicAuth
    cfg = _cfg()
    if not cfg["nc_user"] or not cfg["nc_password"]:
        return {"error": "Nextcloud sin credenciales (NEXTCLOUD_USER/PASSWORD)."}
    base = _nc_base(cfg)
    url = f"{base}/remote.php/dav/files/{cfg['nc_user'].strip('/')}/{path.strip('/')}"
    try:
        with requests.get(url, auth=HTTPBasicAuth(cfg["nc_user"], cfg["nc_password"]),
                          stream=True, timeout=60) as r:
            if r.status_code == 404:
                return {"error": f"No existe {path} en Nextcloud."}
            if r.status_code in (401, 403):
                return {"error": "Credenciales Nextcloud rechazadas (401/403)."}
            if r.status_code != 200:
                return {"error": f"WebDAV devolvió {r.status_code}."}
            size = int(r.headers.get("Content-Length", "0") or 0)
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > limit:
                    return {"error": f"Fichero demasiado grande ({size} bytes).",
                            "path": path, "size": size}
    except Exception as e:
        return {"error": f"No pude descargar de Nextcloud: {e}"}
    return {"data": data, "size": len(data), "path": path}