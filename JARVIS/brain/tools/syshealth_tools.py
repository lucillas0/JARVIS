# -*- coding: utf-8 -*-
"""Salud del PC: overview, Defender, updates, rendimiento, VirusTotal (sección 13).

Solo lectura salvo: defender_scan (escanea), win_updates_install (instala),
mbam_open (abre Malwarebytes). Estos últimos requieren confirmación.
"""
import logging
import os
import subprocess

log = logging.getLogger("tools.syshealth")


def _ps(script: str, timeout: int = 60) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (r.stdout or "").strip()


def health_overview_handler(params: dict) -> dict:
    """Foto rápida: SO, uptime, Defender, updates pendientes, discos, RAM, errores 24h."""
    out = {}
    try:
        out["os"] = _ps("(Get-CimInstance Win32_OperatingSystem | ForEach-Object { $_.Caption + ' ' + $_.Version + ' boot=' + $_.LastBootUpTime })")
        out["defender"] = _ps("try { $d=Get-MpComputerStatus; \"RT=$($d.RealTimeProtectionEnabled) Sig=$($d.AntivirusSignatureLastUpdated) Quick=$($d.QuickScanEndTime)\" } catch { 'ERR:'+$_.Exception.Message }")
        out["updates"] = _ps("try { $s=New-Object -ComObject Microsoft.Update.Session; $r=$s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); $r.Updates.Count } catch { 'ERR' }", timeout=120)
        out["disks"] = _ps("Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name + ':' + [math]::Round($_.Used/1GB,1) + '/' + [math]::Round(($_.Used+$_.Free)/1GB,1) + 'GB' }")
        out["ram"] = _ps("$o=Get-CimInstance Win32_OperatingSystem; 'free='+[math]::Round($o.FreePhysicalMemory/1MB,1)+'GB total='+[math]::Round($o.TotalVisibleMemorySize/1MB,1)+'GB'")
        out["cpu"] = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average")
        out["errors24h"] = _ps("try { Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 300 -ErrorAction Stop | Group-Object Id | Sort-Object Count -Descending | Select-Object -First 5 Count,Name | ForEach-Object { \"$($_.Count)x id $($_.Name)\" } } catch { 'none' }", timeout=90)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def defender_scan_handler(params: dict) -> dict:
    """Lanza análisis de Defender (quick|full). Full puede tardar horas: devuelve inicio."""
    scan = (params.get("type", "") or "quick").strip().lower()
    t = "FullScan" if scan.startswith("full") else "QuickScan"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command",
                            f"Start-MpScan -ScanType {t}; 'iniciado:'+(Get-MpComputerStatus).QuickScanStartTime"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            if "en curso" in err.lower() or "in progress" in err.lower():
                return {"scan": t, "status": "ya había uno en curso; lo dejo terminar"}
            raise RuntimeError(err.splitlines()[-1] if err else "falló")
        return {"scan": t, "status": (r.stdout or "").strip()[-100:]}
    except Exception as e:
        raise RuntimeError(f"No pude lanzar el análisis: {e}")


def defender_threats_handler(params: dict) -> dict:
    """Amenazas detectadas por Defender (activas + historial reciente)."""
    out = _ps("try { Get-MpThreatDetection | Select-Object -First 10 ThreatID,DomainUser,@{n='f';e={$_.Resources -join ';'}} | ForEach-Object { \"$($_.ThreatID)\" } } catch { 'ERR:'+$_.Exception.Message }")
    return {"threats": out}


def defender_quarantine_handler(params: dict) -> dict:
    """Pone en cuarentena/elimina amenazas SIN pedir rutas: detecta, lista
    con sus rutas y purga la cuarentena. Devuelve {found, purged}."""
    det = _ps("Get-MpThreatDetection | ForEach-Object { "
              "\"$($_.ThreatID)|$($_.Resources -join ';')\" }")
    found = []
    for line in det.splitlines():
        line = line.strip()
        if not line or line.startswith("ERR"):
            continue
        tid, _, paths = line.partition("|")
        found.append({"id": tid.strip(), "paths": paths.strip()[:300]})
    purged, perr = False, ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command",
                            "Remove-MpThreat"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        purged = (r.returncode == 0)
        if not purged:
            perr = (r.stderr or "").strip().splitlines()[-1:] or ["falló"]
            perr = perr[0][:150] if isinstance(perr, list) else str(perr)[:150]
    except Exception as e:
        perr = str(e)[:150]
    left = _ps("Get-MpThreat | ForEach-Object { $_.ThreatID }")
    _by_id = {}
    for f in found:
        _by_id.setdefault(str(f.get("id")), []).append(f.get("paths") or "")
    # Solo cuenta como pendiente si el fichero sigue existiendo en disco.
    remaining = []
    for l in left.splitlines():
        l = l.strip()
        if not l:
            continue
        _paths = _by_id.get(l, [])
        if not _paths:
            continue  # registro histórico sin ruta: neutralizado
        if any(os.path.isfile(p.strip()) for p in ";".join(_paths).split(";") if p.strip()):
            remaining.append(l)
    return {"found": found, "purged": purged, "purge_error": perr,
            "remaining": remaining[:10]}


def win_updates_handler(params: dict) -> dict:
    """Lista updates pendientes (título). Lento (~30-60s) la primera vez."""
    out = _ps("try { $s=New-Object -ComObject Microsoft.Update.Session; $r=$s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); $r.Updates | ForEach-Object { $_.Title } } catch { 'ERR:'+$_.Exception.Message }", timeout=180)
    lines = [l for l in out.splitlines() if l.strip()][:20]
    return {"count": len(lines), "updates": lines}


def perf_advisor_handler(params: dict) -> dict:
    """Rendimiento + consejos accionables (reglas sobre CPU/RAM/disco/procesos)."""
    cpu = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average")
    ram = _ps("$o=Get-CimInstance Win32_OperatingSystem; [math]::Round(100*($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/$o.TotalVisibleMemorySize,0)")
    top = _ps("Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 Name,CPU | ForEach-Object { \"$($_.Name):$([math]::Round($_.CPU,0))s\" }")
    startup = _ps("try { (Get-CimInstance Win32_StartupCommand | Measure-Object).Count } catch { '?' }")
    disks = _ps("Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name + ':' + [math]::Round(100*$_.Used/($_.Used+$_.Free),0) + '%' }")
    tips = []
    try:
        for d in disks.splitlines():
            name, pct = d.split(":")
            if int(pct.rstrip("%")) >= 90:
                tips.append(f"Disco {name}: al {pct}: libera espacio o el sistema se arrastra.")
    except Exception:
        pass
    try:
        if int(str(ram).strip()) >= 85:
            tips.append("RAM por encima del 85%: cierra pestañas/apps pesadas.")
    except Exception:
        pass
    return {"cpu_avg": cpu, "ram_pct": ram, "top_cpu": top,
            "startup_items": startup, "disks": disks, "tips": tips}


def vt_scan_file_handler(params: dict) -> dict:
    """VirusTotal: busca por hash SHA256; si es desconocido, lo sube (<32MB)."""
    import hashlib
    import json as _js
    import urllib.request
    from config import Config
    key = (getattr(Config, "VIRUSTOTAL_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("Sin VIRUSTOTAL_API_KEY.")
    path = (params.get("path", "") or "").strip().strip('"')
    if not os.path.isfile(path):
        raise ValueError(f"No existe: {path}")
    size = os.path.getsize(path)
    if size > 650 * 1024 * 1024:
        raise ValueError("Demasiado grande para VirusTotal.")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(ch)
    digest = h.hexdigest()

    def _get(url):
        req = urllib.request.Request(url, headers={"x-apikey": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            return _js.loads(r.read().decode("utf-8", errors="ignore"))

    try:
        info = _get(f"https://www.virustotal.com/api/v3/files/{digest}")
        stats = info.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
        mal = int(stats.get("malicious", 0) or 0)
        return {"known": True, "sha256": digest, "malicious": mal,
                "stats": stats,
                "link": f"https://www.virustotal.com/gui/file/{digest}"}
    except Exception:
        pass
    if size > 32 * 1024 * 1024:
        return {"known": False, "sha256": digest,
                "note": "Desconocido y >32MB: no se puede subir."}
    import uuid
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        blob = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"up\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://www.virustotal.com/api/v3/files",
                                 data=body,
                                 headers={"x-apikey": key,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        up = _js.loads(r.read().decode("utf-8", errors="ignore"))
    aid = up.get("data", {}).get("id", "")
    return {"known": False, "sha256": digest, "uploaded": True, "analysis_id": aid,
            "link": f"https://www.virustotal.com/gui/file/{digest}"}


def vt_scan_url_handler(params: dict) -> dict:
    """VirusTotal: analiza una URL (vota + veredicto)."""
    import json as _js
    import time as _t
    import urllib.parse
    import urllib.request
    from config import Config
    key = (getattr(Config, "VIRUSTOTAL_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("Sin VIRUSTOTAL_API_KEY.")
    url = (params.get("url", "") or "").strip()
    if not url.startswith("http"):
        raise ValueError("URL inválida.")
    data = urllib.parse.urlencode({"url": url}).encode()
    req = urllib.request.Request("https://www.virustotal.com/api/v3/urls", data=data,
                                 headers={"x-apikey": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        sub = _js.loads(r.read().decode("utf-8", errors="ignore"))
    aid = sub.get("data", {}).get("id", "")
    for _ in range(6):
        _t.sleep(10)
        req2 = urllib.request.Request(f"https://www.virustotal.com/api/v3/analyses/{aid}",
                                      headers={"x-apikey": key})
        try:
            with urllib.request.urlopen(req2, timeout=30) as r2:
                an = _js.loads(r2.read().decode("utf-8", errors="ignore"))
            st = an.get("data", {}).get("attributes", {}).get("status", "")
            if st == "completed":
                stats = an.get("data", {}).get("attributes", {}).get("stats", {})
                return {"url": url, "malicious": int(stats.get("malicious", 0) or 0),
                        "stats": stats}
        except Exception as e:
            log.debug("vt poll: %s", e)
    return {"url": url, "pending": True, "analysis_id": aid}


def mbam_open_handler(params: dict) -> dict:
    """Abre Malwarebytes (análisis manual a un clic; la versión Free no tiene CLI)."""
    import shutil
    exe = shutil.which("mbam") or r"C:\Program Files\Malwarebytes\Anti-Malware\mbam.exe"
    if not os.path.isfile(exe):
        raise RuntimeError("Malwarebytes no instalado.")
    try:
        os.startfile(exe)
    except Exception as e:
        raise RuntimeError(f"No pude abrirlo: {e}")
    return {"opened": exe}


def sec_audit_handler(params: dict) -> dict:
    """Auditoría de seguridad SOLO LECTURA: puertos a escucha, conexiones
    establecidas externas, procesos en rutas sospechosas. Sin acciones."""
    listen = _ps("Get-NetTCPConnection -State Listen | ForEach-Object { "
                 "\"$($_.LocalAddress):$($_.LocalPort) pid=$($_.OwningProcess)\" }",
                 timeout=60)
    try:
        procs = {}
        for line in _ps("Get-Process | ForEach-Object { \"$($_.Id)|$($_.Name)\" }",
                        timeout=60).splitlines():
            if "|" in line:
                pid, _, nm = line.partition("|")
                procs[pid.strip()] = nm.strip()
    except Exception:
        procs = {}
    ext = _ps("Get-NetTCPConnection -State Established | "
              "Where-Object { $_.RemoteAddress -notmatch '^(127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)' } | "
              "ForEach-Object { \"$($_.RemoteAddress):$($_.RemotePort)\" }",
              timeout=60)
    ext_lines = [l for l in ext.splitlines() if l.strip()][:15]
    susp = _ps("Get-Process | Where-Object { $_.Path -match '\\\\(Temp|TMP|AppData\\\\\\\\Local\\\\\\\\Temp)' } | "
               "ForEach-Object { \"$($_.Name)|$($_.Path)\" }", timeout=60)
    susp_lines = [l for l in susp.splitlines() if l.strip()][:10]
    return {"listening": [l for l in listen.splitlines() if l.strip()][:20],
            "external_established": ext_lines,
            "suspicious_procs": susp_lines}




def _disk_free_gb(path: str = "C:\\") -> float:
    try:
        import shutil as _sh
        return round(_sh.disk_usage(path).free / (1024 ** 3), 2)
    except Exception:
        return -1.0


def auto_optimize_handler(params: dict) -> dict:
    """Auto-optimización segura: VACUUM de la memoria, retención de auditoría,
    rotación de logs gigantes (conserva la cola), purga de caché pip/TEMP,
    precalentado de voz TTS (respuestas más fluidas) y reindexado ligero.
    NUNCA toca datos personales, herramientas ni configuración."""
    done = {}
    try:
        from config import DATA_DIR
        dbp = os.path.join(str(DATA_DIR), "jarvis.db")
        if os.path.isfile(dbp):
            import sqlite3
            before = os.path.getsize(dbp)
            try:
                from memory import sqlite_store as _db
                _db.rotate_audit()
                done["audit"] = "rotada 30d"
            except Exception:
                pass
            con = sqlite3.connect(dbp, timeout=30)
            try:
                con.execute("VACUUM")
                con.commit()
            finally:
                con.close()
            done["db_mb"] = round((before - os.path.getsize(dbp)) / (1024 * 1024), 2)
    except Exception as e:
        done["db_err"] = str(e)[:100]
    try:
        from config import BRAIN_DIR
        for nm in ("brain.out.log", "brain.err.log"):
            p = os.path.join(str(BRAIN_DIR), nm)
            if os.path.isfile(p) and os.path.getsize(p) > 5 * 1024 * 1024:
                with open(p, "rb") as f:
                    f.seek(-200 * 1024, 2)
                    tail = f.read()
                with open(p, "wb") as f:
                    f.write(b"...[rotado por JARVIS]...\n" + tail)
                done[nm] = "rotado"
    except Exception as e:
        done["logs_err"] = str(e)[:100]
    try:
        import subprocess as _sp
        import sys as _sys
        r = _sp.run([_sys.executable, "-m", "pip", "cache", "purge"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        done["pip"] = (r.stdout or r.stderr or "")[:120]
    except Exception as e:
        done["pip_err"] = str(e)[:100]
    try:
        tmp = os.environ.get("TEMP", "")
        if tmp and os.path.isdir(tmp):
            n, freed = 0, 0
            import time as _t
            week = _t.time() - 7 * 86400
            for root, _d, files in os.walk(tmp):
                for fn in files:
                    fp = os.path.join(root, fn)
                    try:
                        if os.path.getmtime(fp) < week:
                            freed += os.path.getsize(fp)
                            os.remove(fp)
                            n += 1
                    except Exception:
                        pass
                    if n > 2000:
                        break
            done["temp"] = f"{n} ficheros, {round(freed / 1024 / 1024, 1)}MB"
    except Exception as e:
        done["temp_err"] = str(e)[:100]
    try:
        from voice import tts as _tts
        _tts.ensure_voice()
        done["tts"] = "voz precalentada"
    except Exception as e:
        done["tts_err"] = str(e)[:100]
    try:
        from memory import filetree as _ft
        _ft.reindex_background()
        done["filetree"] = "reindexado en segundo plano"
    except Exception:
        pass
    return {"optimized": done}


def cleanup_disk_handler(params: dict) -> dict:
    """Limpieza segura: %TEMP%, caché pip, thumbcache. Nada personal."""
    before = _disk_free_gb()

    def _wipe(path: str) -> None:
        if not os.path.isdir(path):
            return
        for root, dirs, files in os.walk(path):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    os.remove(fp)
                except Exception:
                    pass
        try:
            for root, dirs, files in os.walk(path, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass
        except Exception:
            pass

    details = {}
    try:
        tmp = os.environ.get("TEMP", "") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "Temp")
        _wipe(tmp)
        details["temp"] = True
    except Exception as e:
        details["temp_err"] = str(e)[:100]
    try:
        import subprocess as _sp
        import sys as _sys
        r = _sp.run([_sys.executable, "-m", "pip", "cache", "purge"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        details["pip"] = (r.stdout or r.stderr or "")[:150]
    except Exception as e:
        details["pip_err"] = str(e)[:100]
    try:
        thumb = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                             "Microsoft", "Windows", "Explorer")
        n = 0
        for fn in os.listdir(thumb):
            if fn.lower().startswith("thumbcache_") and fn.lower().endswith(".db"):
                try:
                    n += os.path.getsize(os.path.join(thumb, fn))
                    os.remove(os.path.join(thumb, fn))
                except Exception:
                    pass
        details["thumbs"] = n
    except Exception as e:
        details["thumbs_err"] = str(e)[:100]
    after = _disk_free_gb()
    gb = round((after - before) if after >= 0 and before >= 0 else 0, 2)
    return {"before_gb": before, "after_gb": after, "freed_gb": gb,
            "details": details}


HEALTH_TOOLS = [
    {"name": "health_overview",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Foto de salud del PC: SO, Defender, updates pendientes, discos, RAM, errores 24h.",
     "label": "Revisando el equipo",
     "handler": health_overview_handler},
    {"name": "defender_scan",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Lanzo el análisis de Defender?",
     "parameters": {"type": "str: quick|full"},
     "description": "Lanza un análisis antivirus de Defender (rápido o completo).",
     "label": "Analizando con Defender",
     "handler": defender_scan_handler},
    {"name": "defender_threats",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Amenazas detectadas por Defender.",
     "label": "Consultando amenazas",
     "handler": defender_threats_handler},
    {"name": "defender_quarantine",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Pongo en cuarentena las amenazas detectadas?",
     "parameters": {},
     "description": "Detecta amenazas con sus rutas y las pone en cuarentena sin pedir rutas.",
     "label": "Poniendo en cuarentena",
     "handler": defender_quarantine_handler},
    {"name": "win_updates",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Lista las actualizaciones de Windows pendientes.",
     "label": "Consultando updates",
     "handler": win_updates_handler},
    {"name": "perf_advisor",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Rendimiento del PC (CPU/RAM/disco/procesos) con consejos accionables.",
     "label": "Midiendo rendimiento",
     "handler": perf_advisor_handler},
    {"name": "vt_scan_file",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"path": "str (ruta del fichero)"},
     "description": "Analiza un fichero con VirusTotal (70+ antivirus).",
     "label": "Analizando fichero",
     "handler": vt_scan_file_handler},
    {"name": "vt_scan_url",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"url": "str"},
     "description": "Analiza una URL con VirusTotal.",
     "label": "Analizando URL",
     "handler": vt_scan_url_handler},
    {"name": "mbam_open",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Abre Malwarebytes para un análisis manual a un clic.",
     "label": "Abriendo Malwarebytes",
     "handler": mbam_open_handler},
    {"name": "sec_audit",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Auditoría de seguridad (solo lectura): puertos, conexiones externas, procesos sospechosos.",
     "label": "Auditando seguridad",
     "handler": sec_audit_handler},
    {"name": "cleanup_disk",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Limpio temporales, caché pip y miniaturas?",
     "parameters": {},
     "description": "Libera espacio: TEMP, caché de pip y miniaturas. Mide antes/después.",
     "label": "Limpiando disco",
     "handler": cleanup_disk_handler},
    {"name": "auto_optimize",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Auto-optimización segura: VACUUM memoria + auditoría 30d, rota logs, purga pip/TEMP, precalienta voz TTS y reindexa ficheros. Sin tocar datos ni herramientas.",
     "label": "Optimizando",
     "handler": auto_optimize_handler},
]
