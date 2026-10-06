"""Máquinas virtuales VirtualBox vía VBoxManage (sección 5).

Listar, arrancar (gui/headless), apagar (savestate/poweroff/acpi), ver info
y crear (vm + disco + ISO opcional). Sin borrar: no hay tool de borrado.
"""
import logging
import os
import re
import subprocess

log = logging.getLogger("tools.virtualbox")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_VBOX_CANDS = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "Oracle", "VirtualBox", "VBoxManage.exe"),
    os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"),
                 "Oracle", "VirtualBox", "VBoxManage.exe"),
]


def vboxmanage() -> str:
    for p in _VBOX_CANDS:
        if p and os.path.exists(p):
            return p
    try:
        r = subprocess.run(["where.exe", "VBoxManage"], capture_output=True,
                           text=True, timeout=5, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    raise RuntimeError("No encuentro VBoxManage: ¿está instalado VirtualBox?")


def _run(*args: str, timeout: int = 90) -> str:
    exe = vboxmanage()
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("VBoxManage tardó demasiado (>%ds)" % timeout)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError("; ".join(err[-2:]) if err else "VBoxManage falló")
    return r.stdout or ""


def _parse_list_vms(out: str) -> list[dict]:
    vms = []
    for line in out.splitlines():
        m = re.match(r'\s*"(.+)"\s+\{([0-9a-f-]{36})\}\s*$', line.strip(), re.I)
        if m:
            vms.append({"name": m.group(1), "uuid": m.group(2)})
    return vms


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _resolve(name_or_uuid: str) -> str:
    """Devuelve el nombre exacto si coincide (tolerante a puntos/espacios)."""
    q = (name_or_uuid or "").strip().strip('"')
    if not q:
        raise ValueError("Falta el nombre de la máquina")
    if re.fullmatch(r"[0-9a-f-]{36}", q, re.I):
        return q
    vms = _parse_list_vms(_run("list", "vms"))
    nq = _norm(q)
    exact = [v for v in vms if _norm(v["name"]) == nq]
    if exact:
        return exact[0]["name"]
    part = [v for v in vms if nq and nq in _norm(v["name"])]
    if len(part) == 1:
        return part[0]["name"]
    if len(part) > 1:
        names = ", ".join(v["name"] for v in part[:6])
        raise RuntimeError(f"Hay varias que coinciden: {names}. Sé más concreto, señor.")
    # Sin coincidencia: listar para orientar
    known = ", ".join(v["name"] for v in vms[:8]) or "ninguna"
    raise RuntimeError(f"No encuentro la máquina '{q}'. Las que veo: {known}.")


def vbox_list_handler(params: dict) -> dict:
    vms = _parse_list_vms(_run("list", "vms"))
    try:
        running = {_norm_uuid(v["uuid"]) for v in _parse_list_vms(_run("list", "runningvms"))}
    except Exception:
        running = set()
    out = []
    for v in vms:
        state = "encendida"
        try:
            # Autoridad: VMState en vivo (cubre running/paused/saved/poweroff).
            info = _run("showvminfo", v["name"], "--machinereadable", timeout=30)
            m = re.search(r'^VMState="([^"]+)"', info, re.M)
            st = (m.group(1) if m else "").lower()
            state = {"running": "encendida", "paused": "en pausa",
                     "saved": "guardada"}.get(st, "apagada")
        except Exception:
            state = "encendida" if _norm_uuid(v["uuid"]) in running else "apagada"
        out.append({"name": v["name"], "state": state})
    return {"vms": out}


def _norm_uuid(u: str) -> str:
    return (u or "").strip().lower()


def vbox_info_handler(params: dict) -> dict:
    name = _resolve(params.get("name", ""))
    out = _run("showvminfo", name, "--machinereadable")
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip().strip('"')
    keep = {k: info[k] for k in ("name", "ostype", "memory", "cpus", "VMState",
                                 "VRAMSize", "nic1") if k in info}
    return {"vm": name, "info": keep}


def _vm_state(name: str) -> str:
    """Estado en vivo: running|paused|saved|poweroff|... (minúsculas)."""
    try:
        out = _run("showvminfo", name, "--machinereadable", timeout=30)
        m = re.search(r'^VMState="([^"]+)"', out, re.M)
        return (m.group(1) if m else "").lower()
    except Exception:
        return ""


def vbox_start_handler(params: dict) -> dict:
    name = _resolve(params.get("name", ""))
    mode = (params.get("mode") or "gui").strip().lower()
    if mode not in ("gui", "headless", "sdl"):
        mode = "gui"
    st = _vm_state(name)
    if st == "running":
        return {"vm": name, "mode": mode, "state": "running", "already": True}
    if st == "paused":
        _run("controlvm", name, "resume", timeout=60)
        return {"vm": name, "mode": mode, "state": "running", "already": False,
                "via": "resume"}
    try:
        _run("startvm", name, "--type", mode, timeout=120)
    except RuntimeError as e:
        msg = str(e)
        if "INVALID_OBJECT_STATE" in msg or "already" in msg.lower():
            st2 = _vm_state(name)
            if st2 == "running":
                return {"vm": name, "mode": mode, "state": "running", "already": True}
            raise RuntimeError("No pude arrancar (¿bloqueada por VirtualBox?). Estado: "
                               + (st2 or "desconocido"))
        raise
    return {"vm": name, "mode": mode, "state": "arrancando"}


def vbox_stop_handler(params: dict) -> dict:
    name = _resolve(params.get("name", ""))
    how = (params.get("how") or "savestate").strip().lower()
    if how not in ("savestate", "poweroff", "acpipowerbutton"):
        how = "savestate"
    st = _vm_state(name)
    if st in ("poweroff", "aborted"):
        return {"vm": name, "how": how, "already": True, "state": st}
    if st == "saved" and how == "savestate":
        return {"vm": name, "how": how, "already": True, "state": st}
    _run("controlvm", name, how, timeout=120)
    return {"vm": name, "how": how}


def vbox_create_handler(params: dict) -> dict:
    name = (params.get("name") or "").strip().strip('"')
    if not name:
        raise ValueError("Falta el nombre de la máquina")
    ostype = (params.get("ostype") or "Ubuntu_64").strip()
    memory = int(params.get("memory_mb") or 2048)
    cpus = int(params.get("cpus") or 2)
    disk_mb = int(params.get("disk_mb") or 20000)
    iso = (params.get("iso") or "").strip().strip('"')
    if not (512 <= memory <= 65536):
        raise ValueError("Memoria entre 512 y 65536 MB")
    if not (1 <= cpus <= 16):
        raise ValueError("CPUs entre 1 y 16")
    if not (1024 <= disk_mb <= 2000000):
        raise ValueError("Disco entre 1024 MB y 2 TB")
    base = os.path.join(os.path.expanduser("~"), "VirtualBox VMs", name)
    vdi = os.path.join(base, name + ".vdi")
    _run("createvm", "--name", name, "--ostype", ostype, "--register", timeout=120)
    try:
        _run("modifyvm", name, "--memory", str(memory), "--cpus", str(cpus),
             "--nic1", "nat", "--boot1", "dvd" if iso else "disk",
             "--boot2", "disk", timeout=60)
        _run("createhd", "--filename", vdi, "--size", str(disk_mb),
             "--variant", "Standard", timeout=300)
        _run("storagectl", name, "--name", "SATA", "--add", "sata", timeout=60)
        _run("storageattach", name, "--storagectl", "SATA", "--port", "0",
             "--type", "hdd", "--medium", vdi, timeout=60)
        if iso:
            if not os.path.exists(iso):
                raise FileNotFoundError(f"No veo la ISO: {iso}")
            _run("storageattach", name, "--storagectl", "SATA", "--port", "1",
                 "--type", "dvddrive", "--medium", iso, timeout=60)
    except Exception as e:
        raise RuntimeError(f"Creada a medias '{name}': {e}")
    return {"vm": name, "ostype": ostype, "memory_mb": memory, "cpus": cpus,
            "disk_mb": disk_mb, "iso": iso or None}


# ----------------------------------------------------------------------------- #
# Invitado: ejecutar, leer y escribir dentro de la VM (Guest Control).
# Requiere Guest Additions en la VM. Credenciales solo en .env (VBOX_GUEST_*),
# jamás en logs ni descripciones: se inyectan aquí en el servidor.
# ----------------------------------------------------------------------------- #
def _guest_creds() -> tuple[str, str]:
    try:
        from config import Config
        user = (Config.VBOX_GUEST_USER or "").strip()
        pw = (Config.VBOX_GUEST_PASSWORD or "").strip()
    except Exception:
        user, pw = "", ""
    if not user or not pw:
        raise RuntimeError("Sin credenciales de invitado (VBOX_GUEST_USER/PASSWORD)")
    return user, pw


def _guest_os(name: str) -> str:
    try:
        info = vbox_info_handler({"name": name}).get("info", {})
        return str(info.get("ostype", ""))
    except Exception:
        return ""


def _guest_run(name: str, command: str, timeout: int = 120) -> dict:
    user, pw = _guest_creds()
    ostype = _guest_os(name).lower()
    if "windows" in ostype:
        exe, shell_args = r"C:\Windows\System32\cmd.exe", ["/c", command]
    else:
        exe, shell_args = "/bin/sh", ["-c", command]
    exe_path = vboxmanage()
    cmd = [exe_path, "guestcontrol", name, "--username", user,
           "--password", pw, "run", "--exe", exe, "--"] + shell_args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("El comando tardó demasiado (>%ds)" % timeout)
    out = (r.stdout or "")[:4000]
    err = (r.stderr or "")[:500]
    if r.returncode != 0 and not out:
        if "not ready" in err.lower() or "notready" in err.lower():
            raise RuntimeError("El servicio de ejecución no está listo: actualiza las "
                               "Guest Additions dentro de la VM e iníciala de nuevo.")
        raise RuntimeError(err or f"Comando falló (rc={r.returncode})")
    return {"rc": r.returncode, "output": out, "error": err}


# Programas interactivos (necesitan terminal): se rechazan en el acto en vez
# de colgar el timeout. Y apagados/reinicios del invitado: por seguridad van
# con vbox_stop, no con comandos sueltos.
_INTERACTIVE_RE = re.compile(
    r"(?:^|[\s;&|]+)(nano|vim?|vi|emacs|top|htop|atop|less|more|most|"
    r"watch|tail\s+-f|ssh\s|passwd|su\b|screen|tmux)\b",
    re.I)
_POWER_RE = re.compile(
    r"(?:^|[\s;&|]+)(reboot|poweroff|halt|shutdown(\s+-h|\s+now)?|init\s+[06])\b(?!\s+--help|\s+-h\b)",
    re.I)


def _guard_command(command: str) -> None:
    if _INTERACTIVE_RE.search(command or ""):
        raise RuntimeError("Eso necesita pantalla interactiva, señor: pídame leer, "
                           "escribir o ejecutar comandos no interactivos en su lugar.")
    if _POWER_RE.search(command or ""):
        raise RuntimeError("Apagados y reinicios por seguridad van con la acción "
                           "de apagar máquina, señor.")


def _need_running(name: str) -> None:
    """Falla claro si la VM no está en marcha (evita errores crípticos)."""
    st = _vm_state(name)
    if st and st != "running":
        how = {"poweroff": "apagada", "saved": "guardada", "paused": "en pausa",
               "aborted": "abortada"}.get(st, st)
        raise RuntimeError(f"{name} está {how}, señor. Dígame «arráncala» y lo hago después.")


def vbox_exec_handler(params: dict) -> dict:
    """Ejecuta un comando dentro de la VM y devuelve su salida.

    sudo automático: si empieza por 'sudo ' se inyecta la clave (servidor).
    timeout opcional en segundos (30-600, defecto 120).
    """
    name = _resolve(params.get("name", ""))
    command = str(params.get("command", ""))[:4000]
    if not command.strip():
        raise ValueError("Falta el comando")
    _guard_command(command)
    _need_running(name)
    try:
        timeout = int(params.get("timeout", 120) or 120)
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(30, min(600, timeout))
    stripped = command.strip()
    if re.match(r"(?i)^sudo\b", stripped) and "-S" not in stripped.split()[:3] \
            and "-A" not in stripped.split()[:3]:
        _, pw = _guest_creds()
        rest = re.sub(r"(?i)^sudo\s+", "", stripped, count=1)
        command = "echo %s | sudo -S %s" % (_sh_quote(pw), rest)
    res = _guest_run(name, command, timeout=timeout)
    res["vm"] = name
    return res


def vbox_read_file_handler(params: dict) -> dict:
    """Lee un fichero de dentro de la VM (código, configs, logs)."""
    import tempfile
    name = _resolve(params.get("name", ""))
    guest_path = str(params.get("path", "")).strip()
    if not guest_path:
        raise ValueError("Falta la ruta dentro de la VM")
    _need_running(name)
    user, pw = _guest_creds()
    tmpdir = tempfile.mkdtemp(prefix="vboxread_")
    try:
        exe_path = vboxmanage()
        base = os.path.basename(guest_path.rstrip("/\\")) or "file"
        local = os.path.join(tmpdir, base)
        # copyfrom exige la ruta destino COMPLETA (no solo el directorio).
        cmd = [exe_path, "guestcontrol", name, "copyfrom",
               f"--target-directory={local}",
               f"--username={user}", f"--password={pw}",
               guest_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Lectura tardó demasiado")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip().splitlines()[-1:]
                               and (r.stderr or "").strip().splitlines()[-1]
                               or "No pude leer el fichero")
        base = os.path.basename(guest_path.rstrip("/\\")) or "file"
        local = os.path.join(tmpdir, base)
        if not os.path.exists(local):
            got = os.listdir(tmpdir)
            local = os.path.join(tmpdir, got[0]) if got else None
            if not local:
                raise RuntimeError("La VM no devolvió el fichero")
        if os.path.getsize(local) > 100 * 1024:
            raise RuntimeError("Fichero mayor de 100 KB: pídame una parte concreta")
        with open(local, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"vm": name, "path": guest_path, "content": content}
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def vbox_write_file_handler(params: dict) -> dict:
    """Escribe (sobrescribe) un fichero dentro de la VM."""
    import tempfile
    name = _resolve(params.get("name", ""))
    guest_path = str(params.get("path", "")).strip()
    content = str(params.get("content", ""))
    if not guest_path:
        raise ValueError("Falta la ruta dentro de la VM")
    if len(content) > 500 * 1024:
        raise ValueError("Contenido mayor de 500 KB")
    _need_running(name)
    user, pw = _guest_creds()
    tmpdir = tempfile.mkdtemp(prefix="vboxwrite_")
    try:
        base = os.path.basename(guest_path.rstrip("/\\")) or "file"
        local = os.path.join(tmpdir, base)
        with open(local, "w", encoding="utf-8") as f:
            f.write(content)
        guest_dir = os.path.dirname(guest_path.rstrip("/\\")) or "/tmp"
        exe_path = vboxmanage()
        # copyto exige la ruta destino COMPLETA (no solo el directorio).
        cmd = [exe_path, "guestcontrol", name, "copyto",
               f"--target-directory={guest_path}",
               f"--username={user}", f"--password={pw}", local]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Escritura tardó demasiado")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip().splitlines()[-1:]
                               and (r.stderr or "").strip().splitlines()[-1]
                               or "No pude escribir el fichero")
        return {"vm": name, "path": guest_path, "bytes": len(content.encode())}
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# Scancodes set-1 (make); break = make + 0x80. Dígitos y minúsculas.
_SCAN = {"1": "02", "2": "03", "3": "04", "4": "05", "5": "06", "6": "07",
         "7": "08", "8": "09", "9": "0a", "0": "0b", "enter": "1c",
         "a": "1e", "b": "30", "c": "2e", "d": "20", "e": "12", "f": "21",
         "g": "22", "h": "23", "i": "17", "j": "24", "k": "25", "l": "26",
         "m": "32", "n": "31", "o": "18", "p": "19", "q": "10", "r": "13",
         "s": "1f", "t": "14", "u": "16", "v": "2f", "w": "11", "x": "2d",
         "y": "15", "z": "2c", " ": "39"}


def _password_scancodes(password: str) -> list[str]:
    codes: list[str] = []
    for ch in password:
        if ch.isupper():
            codes += ["2a", _SCAN[ch.lower()], "aa"]  # shift pulsado
        elif ch.lower() in _SCAN:
            codes.append(_SCAN[ch.lower()])
        else:
            raise ValueError(f"Carácter no tecleable: {ch!r}")
    press = lambda c: [c, "%02x" % (int(c, 16) + 0x80)]  # make + break
    out: list[str] = []
    for c in codes:
        out += press(c)
    return out + press("1c")  # Enter final


def _kbd_put(name: str, codes: list[str]) -> None:
    exe = vboxmanage()
    cmd = [exe, "controlvm", name, "keyboardputscancode", *codes]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("El teclado no responde")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip().splitlines()[-1:]
                           and (r.stderr or "").strip().splitlines()[-1]
                           or "No pude pulsar teclas")


def _console_state(name: str, user: str) -> tuple[bool, bool]:
    """(nuestro_login_en_tty, alguien_en_tty). Vía loginctl (`who` no es
    fiable aquí: utmp vacío aun con sesión). Lanza si no se puede comprobar:
    NUNCA se teclea a ciegas."""
    res = _guest_run(name, "loginctl list-sessions --no-legend")
    ours = any_ = False
    for line in (res.get("output") or "").splitlines():
        # SESSION UID USER SEAT LEADER CLASS TTY IDLE SINCE…
        parts = line.split()
        if len(parts) >= 7:
            u, tty = parts[2], parts[6]
            if tty.startswith("tty"):
                any_ = True
                if u == user:
                    ours = True
    return ours, any_


def _console_logged_in(name: str, user: str) -> bool:
    ours, _ = _console_state(name, user)
    return ours


def _type_string(name: str, text: str) -> None:
    import time as _time
    codes: list[str] = []
    for ch in text:
        if ch.isupper():
            codes += ["2a", _SCAN[ch.lower()], "aa"]
        elif ch.lower() in _SCAN:
            codes.append(_SCAN[ch.lower()])
        else:
            raise ValueError(f"Carácter no tecleable: {ch!r}")
    press = lambda c: [c, "%02x" % (int(c, 16) + 0x80)]
    seq: list[str] = []
    for c in codes:
        seq += press(c)
    seq += press("1c")  # Enter
    _kbd_put(name, seq)
    _time.sleep(1.0)


def vbox_unlock_handler(params: dict) -> dict:
    """Desbloquea/inicia la sesión del invitado (login en consola o lock)."""
    import time as _time
    name = _resolve(params.get("name", ""))
    _need_running(name)
    user, pw = _guest_creds()
    ostype = _guest_os(name).lower()
    if "windows" not in ostype:
        # 1) Si ya hay login nuestro en consola, nada que hacer.
        #    Si la consola la ocupa OTRO usuario, no teclear jamás.
        try:
            ours, any_tty = _console_state(name, user)
        except Exception as e:
            raise RuntimeError(f"No pude comprobar la consola: {e}. No tecleo a ciegas.")
        if ours:
            return {"vm": name, "unlocked": True, "how": "ya con sesión"}
        if any_tty:
            raise RuntimeError("La consola la ocupa otro usuario; no tecleo la clave ahí.")
        # 2) Intentar desbloqueo de sesión gráfica (por si la hay).
        try:
            res = _guest_run(name, "echo %s | sudo -S loginctl unlock-sessions" % _sh_quote(pw))
            if res.get("rc", 1) == 0 and _console_logged_in(name, user):
                return {"vm": name, "unlocked": True, "how": "loginctl"}
        except Exception:
            pass
        # 3) Login en consola de texto: limpiar línea, usuario, Enter, clave, Enter.
        try:
            _kbd_put(name, ["1c", "9c"])  # Enter: vacía línea a medias
            _time.sleep(1.0)
            _kbd_put(name, ["1d", "2e", "ae", "9d"])  # Ctrl+C de verdad
            _time.sleep(1.0)
            _type_string(name, user)
            _time.sleep(2.0)
            _type_string(name, pw)
        except Exception as e:
            raise RuntimeError(f"No pude teclear el login: {e}")
        # El MOTD tarda: reintentar la comprobación hasta ~30s.
        for _ in range(6):
            _time.sleep(5.0)
            if _console_logged_in(name, user):
                return {"vm": name, "unlocked": True, "how": "login en consola"}
        raise RuntimeError("Tecleé las credenciales pero no hay sesión. ¿Usuario o clave distintos?")
    # Windows: Ctrl+Alt+Supr, Enter para el campo, contraseña, Enter.
    _kbd_put(name, ["1d", "38", "53", "d3", "b8", "9d"])
    _time.sleep(3)
    _kbd_put(name, ["1c", "9c"])
    _time.sleep(1)
    try:
        codes = _password_scancodes(pw)
    except ValueError:
        raise RuntimeError("La contraseña tiene caracteres que no puedo teclear")
    _kbd_put(name, codes)
    _time.sleep(4)
    return {"vm": name, "unlocked": True, "how": "ctrl+alt+supr+clave"}


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ----------------------------------------------------------------------------- #
# Consola visible + tecleado en consola (layout es/us autodetectado)
# ----------------------------------------------------------------------------- #
def _vbox_exe() -> str:
    mgr = os.path.join(os.path.dirname(vboxmanage()), "VirtualBox.exe")
    if os.path.exists(mgr):
        return mgr
    raise RuntimeError("No encuentro VirtualBox.exe")


def vbox_console_handler(params: dict) -> dict:
    """Muestra la ventana/consola de la VM (abre VirtualBox con ella)."""
    name = _resolve(params.get("name", ""))
    try:
        subprocess.Popen([_vbox_exe(), "--startvm", name],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                         close_fds=True)
    except Exception as e:
        raise RuntimeError(f"No pude mostrar la consola: {e}")
    return {"vm": name, "console": "mostrada"}


# Códigos set-1 por POSICIÓN física. Letras/dígitos/espacio/enter: idénticos.
# (scancode, shift, altgr)
_US_EXTRA = {
    "/": ("35", 0, 0), ".": ("34", 0, 0), ",": ("33", 0, 0), ";": ("3b", 0, 0),
    "'": ("28", 0, 0), "-": ("0c", 0, 0), "=": ("0d", 0, 0), "[": ("1a", 0, 0),
    "]": ("1b", 0, 0), "\\": ("2b", 0, 0), "`": ("29", 0, 0),
    ":": ("3b", 1, 0), '"': ("28", 1, 0), "_": ("0c", 1, 0), "+": ("0d", 1, 0),
    "{": ("1a", 1, 0), "}": ("1b", 1, 0), "?": ("35", 1, 0), "!": ("02", 1, 0),
    "@": ("03", 1, 0), "#": ("03", 1, 0), "$": ("04", 1, 0), "%": ("05", 1, 0),
    "^": ("06", 1, 0), "&": ("07", 1, 0), "*": ("08", 1, 0), "(": ("09", 1, 0),
    ")": ("0a", 1, 0), "<": ("33", 1, 0), ">": ("34", 1, 0), "|": ("2b", 1, 0),
    "~": ("29", 1, 0),
}
_ES_EXTRA = {
    ",": ("33", 0, 0), ".": ("34", 0, 0), "-": ("35", 0, 0), "'": ("0c", 0, 0),
    "¡": ("0d", 0, 0), "º": ("29", 0, 0), "ç": ("2b", 0, 0), "ñ": ("27", 0, 0),
    ":": ("34", 1, 0), ";": ("33", 1, 0), "_": ("35", 1, 0), "/": ("08", 1, 0),
    "(": ("09", 1, 0), ")": ("0a", 1, 0), "=": ("0b", 1, 0), "!": ("02", 1, 0),
    '"': ("03", 1, 0), "<": ("56", 0, 0), ">": ("56", 1, 0),
    "@": ("03", 0, 1), "#": ("03", 0, 1), "|": ("02", 0, 1),
    "$": ("04", 1, 0), "%": ("05", 1, 0), "&": ("06", 1, 0),
}
_LAYOUT_CACHE: dict[str, str] = {}


def detect_layout(name: str) -> str:
    """es|us según localectl (Linux). Cacheado por VM."""
    if name in _LAYOUT_CACHE:
        return _LAYOUT_CACHE[name]
    layout = "us"
    try:
        res = _guest_run(name, "localectl status 2>/dev/null || cat /etc/default/keyboard 2>/dev/null")
        blob = (res.get("output") or "").lower()
        m = re.search(r"(?:vc keymap|x11 layout|xkblayout)[\s:=\"]*([a-z]{2})", blob)
        if m and m.group(1).startswith("es"):
            layout = "es"
    except Exception:
        pass
    _LAYOUT_CACHE[name] = layout
    return layout


def _char_codes(ch: str, layout: str) -> list[str]:
    """Códigos make+break (+shift/altgr) para un carácter."""
    # Tildes españolas: tecla muerta ´ (0x28) + vocal. Solo layout es.
    _ACC = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
            "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U"}
    if ch in ("ü", "Ü"):
        if layout != "es":
            raise ValueError(f"Carácter no tecleable en layout {layout}: {ch!r}")
        seq = ["2a", "28", "a8", "aa"]  # Shift+´ = diéresis
        bcode = _SCAN["u"]
        if ch == "Ü":
            seq.append("2a")
        seq += [bcode, "%02x" % (int(bcode, 16) + 0x80)]
        if ch == "Ü":
            seq.append("aa")
        return seq
    if ch in _ACC:
        if layout != "es":
            raise ValueError(f"Carácter no tecleable en layout {layout}: {ch!r}")
        seq = ["28", "a8"]
        base = _ACC[ch]
        if base.isupper():
            seq.append("2a")
        bcode = _SCAN[base.lower()]
        seq += [bcode, "%02x" % (int(bcode, 16) + 0x80)]
        if base.isupper():
            seq.append("aa")
        return seq
    seq: list[str] = []
    needs_shift, needs_altgr, code = False, False, None
    if ch == "\n":
        code = "1c"
    elif ch == "\t":
        code = "0f"
    elif ch == " ":
        code = "39"
    elif ch.isalpha() and len(ch) == 1:
        low = ch.lower()
        if low in _SCAN:
            code = _SCAN[low]
            needs_shift = ch.isupper()
        elif layout == "es" and low in ("ñ", "ç"):
            code = {"ñ": "27", "ç": "2b"}[low]
            needs_shift = ch.isupper()
    elif ch.isdigit() and len(ch) == 1:
        code = _SCAN[ch]
    else:
        table = _ES_EXTRA if layout == "es" else _US_EXTRA
        if ch in table:
            code, needs_shift, needs_altgr = table[ch][0], bool(table[ch][1]), bool(table[ch][2])
    if code is None:
        raise ValueError(f"Carácter no tecleable en layout {layout}: {ch!r}")
    if needs_altgr:
        seq += ["1d", "e0", "38"]  # Ctrl + AltGr der.
    if needs_shift:
        seq.append("2a")
    seq += [code, "%02x" % (int(code, 16) + 0x80)]
    if needs_shift:
        seq.append("aa")
    if needs_altgr:
        seq += ["e0", "b8", "9d"]
    return seq


def vbox_type_handler(params: dict) -> dict:
    """Teclea texto literal en la consola de la VM (caja negra con layout)."""
    name = _resolve(params.get("name", ""))
    text = str(params.get("text", ""))
    if not text:
        raise ValueError("Falta el texto")
    if len(text) > 2000:
        raise ValueError("Máximo 2000 caracteres por tecleado")
    try:
        layout = detect_layout(name)
    except Exception:
        layout = "unknown"
    if layout == "unknown":
        bad = sorted({c for c in text if not (c.isalnum() or c in " \n\t")})
        if bad:
            raise RuntimeError(f"Sin layout detectado solo [a-z0-9]: sobra {''.join(bad)}")
        layout = "us"
    codes: list[str] = []
    try:
        for ch in text:
            codes += _char_codes(ch, layout)
    except ValueError as e:
        raise RuntimeError(f"{e}. Reformule sin esos símbolos, señor.")
    for i in range(0, len(codes), 120):
        _kbd_put(name, codes[i:i + 120])
    return {"vm": name, "typed_chars": len(text), "layout": layout}


def vbox_ip_handler(params: dict) -> dict:
    """IP invitada de la VM vía Guest Additions (Net/0..3)."""
    import ipaddress
    name = _resolve(params.get("name", ""))
    state = _vm_state(name)
    if state not in ("encendida", "running"):
        return {"vm": name, "state": state, "ips": [],
                "note": "apagada (sin IP invitada)"}
    ips = []
    for i in range(4):
        try:
            out = _run("guestproperty", "get", name,
                       f"/VirtualBox/GuestInfo/Net/{i}/V4/IP")
        except Exception:
            continue
        m = re.search(r"Value:\s*(\S+)", out or "")
        if not m:
            continue
        ip = m.group(1).strip()
        try:
            if ipaddress.ip_address(ip).is_loopback:
                continue
        except Exception:
            continue
        if ip not in ips:
            ips.append(ip)
    return {"vm": name, "state": state, "ips": ips}


def _first_bridged_if() -> str:
    try:
        out = _run("list", "bridgedifs")
    except Exception:
        return ""
    for line in out.splitlines():
        if line.strip().lower().startswith("name:"):
            name = line.split(":", 1)[1].strip()
            if name:
                return name
    return ""


def vbox_net_handler(params: dict) -> dict:
    """Cambia la red de una VM APAGADA/GUARDADA (nic1-4): nat|bridged|hostonly|none.
    Opcional: adapter (nombre del adaptador físico para bridged), cable on/off."""
    name = _resolve(params.get("name", ""))
    try:
        nic = int(params.get("nic", 1) or 1)
    except (TypeError, ValueError):
        nic = 1
    if nic < 1 or nic > 4:
        raise ValueError("nic 1-4, señor")
    if _vm_state(name) not in ("apagada", "guardada", "poweroff", "saved", "aborted"):
        raise RuntimeError(f"{name} está en marcha: apágala para cambiar la red.")
    modes = {"nat": "nat", "bridged": "bridged", "bridge": "bridged",
             "puente": "bridged", "hostonly": "hostonly", "host": "hostonly",
             "anfitrion": "hostonly", "none": "none", "nada": "none",
             "desconectada": "none", "off": "none", "intnet": "intnet",
             "internal": "intnet", "interna": "intnet", "red interna": "intnet"}
    mode = (params.get("mode", "") or "").strip().lower()
    if mode not in modes:
        raise ValueError("mode: nat|bridged|hostonly|none")
    m = modes[mode]
    args = ["modifyvm", name, f"--nic{nic}", m]
    adapter = (params.get("adapter", "") or "").strip()
    if m == "bridged":
        adapter = adapter or _first_bridged_if()
        if not adapter:
            raise RuntimeError("No veo adaptadores físicos para puente.")
        args += [f"--bridgeadapter{nic}", adapter]
    _run(*args)
    cable = params.get("cable", None)
    if cable is not None:
        on = str(cable).strip().lower() not in ("0", "false", "no", "off")
        _run("modifyvm", name, f"--cableconnected{nic}", "on" if on else "off")
    return {"vm": name, "nic": nic, "mode": m,
            "adapter": adapter if m == "bridged" else "",
            "cable": cable}


VB_TOOLS = [
    {"name": "vbox_list",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Lista las máquinas virtuales VirtualBox y si están encendidas.",
     "label": "Listando máquinas virtuales",
     "handler": vbox_list_handler},
    {"name": "vbox_info",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"name": "str (nombre o parte)"},
      "description": "Muestra RAM, CPUs, sistema y estado de una máquina virtual.",
      "label": "Viendo máquina virtual",
      "handler": vbox_info_handler},
     {"name": "vbox_ip",
      "risk_level": "low",
      "requires_confirmation": False,
      "parameters": {"name": "str (nombre o parte)"},
      "description": "IP invitada de la VM (Guest Additions). Vacía si está apagada.",
      "label": "Viendo IP de la máquina",
      "handler": vbox_ip_handler},
     {"name": "vbox_net",
      "risk_level": "medium",
      "requires_confirmation": False,
      "parameters": {"name": "str", "mode": "str: nat|bridged|hostonly|none",
                     "nic": "int 1-4 opcional", "adapter": "str opcional",
                     "cable": "on|off opcional"},
      "description": "Cambia la red de una VM APAGADA (modo NAT/puente/solo-anfitrión).",
      "label": "Cambiando red de la máquina",
      "handler": vbox_net_handler},
    {"name": "vbox_start",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "mode": "str: gui|headless"},
     "description": "Arranca una máquina virtual (gui con ventana, headless en fondo).",
     "label": "Arrancando máquina virtual",
     "handler": vbox_start_handler},
    {"name": "vbox_stop",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "how": "str: savestate|poweroff|acpipowerbutton"},
     "description": "Apaga una máquina (savestate guarda el estado).",
     "label": "Apagando máquina virtual",
     "handler": vbox_stop_handler},
    {"name": "vbox_create",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "ostype": "str ej Ubuntu_64/Debian_64/Windows11_64",
                    "memory_mb": "int", "cpus": "int", "disk_mb": "int", "iso": "str opcional"},
     "description": "Crea una máquina virtual (RAM, CPUs, disco e ISO opcional).",
     "label": "Creando máquina virtual",
     "handler": vbox_create_handler},
    {"name": "vbox_exec",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "command": "str", "timeout": "int opcional 30-600"},
     "description": "Ejecuta CUALQUIER comando no interactivo dentro de la VM (ip, systemctl, apt…) con sudo automático. Sin editores (nano/vim) ni apagados.",
     "label": "Ejecutando en la máquina",
     "handler": vbox_exec_handler},
    {"name": "vbox_read_file",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "path": "str ruta dentro de la VM"},
     "description": "Lee un fichero de dentro de la VM (código, configs, logs).",
     "label": "Leyendo fichero de la máquina",
     "handler": vbox_read_file_handler},
    {"name": "vbox_write_file",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "path": "str", "content": "str"},
     "description": "Escribe (sobrescribe) un fichero dentro de la VM.",
     "label": "Escribiendo fichero en la máquina",
     "handler": vbox_write_file_handler},
    {"name": "vbox_unlock",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str"},
     "description": "Desbloquea la pantalla de bloqueo de la VM (sesión del invitado).",
     "label": "Desbloqueando máquina",
     "handler": vbox_unlock_handler},
    {"name": "vbox_console",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str"},
     "description": "Muestra la ventana/consola visible de la VM en este PC.",
     "label": "Mostrando consola",
     "handler": vbox_console_handler},
    {"name": "vbox_type",
     "risk_level": "medium",
     "requires_confirmation": False,
     "parameters": {"name": "str", "text": "str hasta 2000 caracteres"},
     "description": "Teclea texto literal en la consola de la VM (tildes y símbolos ES incluidos).",
     "label": "Tecleando en consola",
     "handler": vbox_type_handler},
]
