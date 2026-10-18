# -*- coding: utf-8 -*-
"""Diagnóstico completo del equipo, con el foco en JARVIS (TTS/STT) y en lo que
puede impedir que el sistema funcione o vaya rápido.

Solo lectura: no cambia nada. Devuelve los hallazgos y el motor de permisos
pregunta al señor antes de reparar nada.
"""
import logging
import os
import subprocess
import sys
import time

log = logging.getLogger("tools.diagnose")

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
       "-Command"]


def _ps(script: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(_PS + [script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip()
    except Exception as e:
        return f"ERR:{str(e)[:80]}"


def _step(text: str, first: bool = False) -> None:
    try:
        from status import step
        step(text, first=first)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# JARVIS: voz
# --------------------------------------------------------------------------- #
def _check_tts() -> list:
    out = []
    try:
        from voice import tts
        path = tts.ensure_voice()
        out.append({"area": "Voz (TTS)", "ok": True,
                    "detail": f"modelo Piper cargado ({os.path.basename(path)})"})
    except Exception as e:
        out.append({"area": "Voz (TTS)", "ok": False,
                    "detail": f"no puedo sintetizar: {str(e)[:140]}"})
    try:
        import sounddevice as sd
        outs = [d for d in sd.query_devices() if d["max_output_channels"] > 0]
        if outs:
            out.append({"area": "Altavoces", "ok": True,
                        "detail": f"{len(outs)} salidas disponibles"})
        else:
            out.append({"area": "Altavoces", "ok": False, "detail": "sin salidas de audio"})
    except Exception as e:
        out.append({"area": "Altavoces", "ok": False, "detail": str(e)[:120]})
    return out


def _check_stt() -> list:
    out = []
    try:
        from config import Config
        out.append({"area": "STT (Groq Whisper)", "ok": bool(Config.GROQ_API_KEY),
                    "detail": "clave presente" if Config.GROQ_API_KEY
                              else "falta GROQ_API_KEY: no habrá transcripción"})
    except Exception as e:
        out.append({"area": "STT (Groq Whisper)", "ok": False, "detail": str(e)[:120]})
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        ins = [d for d in devs if d["max_input_channels"] > 0]
        if not ins:
            out.append({"area": "Micrófono", "ok": False, "detail": "no hay entradas de audio"})
        else:
            opened = 0
            for i, d in enumerate(devs):
                if d["max_input_channels"] <= 0:
                    continue
                try:
                    with sd.InputStream(samplerate=16000, channels=1,
                                        device=i, dtype="float32") as s:
                        s.start(); s.stop()
                    opened += 1
                except Exception:
                    pass
            if opened:
                out.append({"area": "Micrófono", "ok": True,
                            "detail": f"{opened} de {len(ins)} entradas abren correctamente"})
            else:
                out.append({"area": "Micrófono", "ok": False,
                            "detail": f"el driver rechaza las {len(ins)} entradas: "
                                       "reinstale el controlador de audio"})
    except Exception as e:
        out.append({"area": "Micrófono", "ok": False, "detail": str(e)[:120]})
    return out


def _check_camera() -> list:
    try:
        from tools import camera_tools as _cam
        camaras = _cam._camaras()
        if not camaras:
            return [{"area": "Cámara", "ok": False,
                     "detail": "no hay cámara conectada"}]
        vivas = [c for c in camaras if c["presente"] and c["estado"] == "OK"]
        fantasmas = [c for c in camaras if not c["presente"] or c["estado"] != "OK"]
        if not vivas:
            return [{"area": "Cámara", "ok": False,
                     "detail": "está registrada pero no conectada: revise el "
                               "cable o el puerto USB"}]
        if fantasmas:
            # No es un problema real: la cámara buena está conectada y el
            # tracker captura con OpenCV (por índice), así que las fantasma de
            # puertos antiguos no le estorban. SeAvisa, pero no marca fallo.
            return [{"area": "Cámara", "ok": True,
                     "detail": f"{len(vivas)} cámara operativa(s); hay "
                               f"{len(fantasmas)} fantasma de puertos USB "
                               f"anteriores, pero el tracker usa la captura del "
                               f"cerebro y no le afectan (limpieza: camara_reparar)"}]
        return [{"area": "Cámara", "ok": True,
                 "detail": f"{len(vivas)} dispositivo(s) operativo(s)"}]
    except Exception as e:
        return [{"area": "Cámara", "ok": False, "detail": str(e)[:120]}]


# --------------------------------------------------------------------------- #
# Seguridad y rendimiento
# --------------------------------------------------------------------------- #
def _check_security() -> list:
    out = []
    st = _ps("try { $d=Get-MpComputerStatus; "
             "'RT=' + $d.RealTimeProtectionEnabled + '|Sig=' + $d.AntivirusSignatureLastUpdated "
             "+ '|Quick=' + $d.QuickScanEndTime } catch { 'ERR' }", timeout=60)
    if st.startswith("RT="):
        ok_rt = "RT=True" in st
        out.append({"area": "Defender", "ok": ok_rt,
                    "detail": st if ok_rt else "protección en tiempo real desactivada"})
    else:
        out.append({"area": "Defender", "ok": False, "detail": "no responde"})
    # Una deteccion que Defender ya limpio (fichero que no existe) no es un
    # problema: son avisos antiguos (EICAR, pruebas). Solo cuentan las activas.
    threats = _ps(
        "$t = Get-MpThreatDetection -ErrorAction SilentlyContinue; "
        "$activos = @($t | Where-Object { "
        "  $_.Resources | Where-Object { $_ -match '^[A-Za-z]:' -and (Test-Path ($_ -replace '^file:_?','')) } "
        "}).Count; "
        "$hist = @($t).Count; "
        "if ($t -and -not ($t | Where-Object { $_.ActionSuccess -eq $false })) "
        "{ \"$activos|$hist|resueltos\" } else { \"$activos|$hist|activos\" }",
        timeout=90)
    try:
        activo, total, estado = threats.strip().split("|")
        activo, total = int(activo), int(total)
    except Exception:
        activo, total, estado = -1, -1, "?"
    if activo > 0:
        out.append({"area": "Amenazas", "ok": False,
                    "detail": f"{activo} detecci\u00f3n(es) de Defender sin resolver"})
    elif total > 0:
        out.append({"area": "Amenazas", "ok": True,
                    "detail": f"sin amenazas activas ({total} aviso(s) antiguo(s) ya "
                               f"resueltos por Defender)"})
    else:
        out.append({"area": "Amenazas", "ok": True, "detail": "sin detecciones registradas"})
    out.extend(_check_virustotal_descargas())
    return out


_EXT_SOSPECHOSOS = (".exe", ".msi", ".scr", ".bat", ".cmd", ".com", ".pif", ".js")


def _check_virustotal_descargas() -> list:
    """Mira en VirusTotal los programas recientes de Descargas y del escritorio.

    Solo se consulta el HASH (SHA-256). No se sube ningún fichero: mandar
    documentos personales de su ordenador a un servicio externo sin avisar
    no es cosa que se haga por las buenas. Un hash no identifica el fichero
    para nadie más, y permite saber si lo marcan 70 antivirus.
    """
    import glob
    import hashlib
    import os
    from config import Config
    key = (getattr(Config, "VIRUSTOTAL_API_KEY", "") or "").strip()
    if not key:
        return [{"area": "VirusTotal", "ok": None,
                 "detail": "sin clave configurada"}]
    try:
        import urllib.request
        import json as _js
    except Exception:
        return [{"area": "VirusTotal", "ok": None, "detail": "sin urllib"}]

    rutas = []
    for base in (os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
                 os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                 os.environ.get("TEMP", "")):
        for ext in _EXT_SOSPECHOSOS:
            rutas += glob.glob(os.path.join(base, f"*{ext}"))
    # Los últimos modificados primero, que son los que acaban de llegar.
    rutas.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    rutas = rutas[:8]
    if not rutas:
        return [{"area": "VirusTotal", "ok": None,
                 "detail": "no hay ejecutables recientes que mirar"}]

    sospechosos, revisados, sin_datos = 0, 0, 0
    for p in rutas:
        try:
            if os.path.getsize(p) > 200 * 1024 * 1024:
                continue
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for trozo in iter(lambda: f.read(4 * 1024 * 1024), b""):
                    h.update(trozo)
            req = urllib.request.Request(
                "https://www.virustotal.com/api/v3/files/" + h.hexdigest(),
                headers={"x-apikey": key})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = _js.loads(r.read().decode("utf-8", "ignore"))
            st = d.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
            mal = int(st.get("malicious", 0) or 0)
            revisados += 1
            if mal:
                sospechosos += 1
                _step(f"⚠ VirusTotal marca {os.path.basename(p)}: {mal} detecciones")
            else:
                sin_datos += 1
        except Exception:
            continue
    if revisados == 0:
        return [{"area": "VirusTotal", "ok": None,
                 "detail": f"{len(rutas)} ficheros, ninguno conocido por VirusTotal "
                           "(suelen ser instaladores muy recientes)"}]
    if sospechosos:
        return [{"area": "VirusTotal", "ok": False,
                 "detail": f"{sospechosos} de {revisados} ficheros recientes "
                           f"marcados como maliciosos"}]
    return [{"area": "VirusTotal", "ok": True,
             "detail": f"{revisados} ficheros recientes, 0 detecciones"}]


def _check_performance() -> list:
    out = []
    cpu = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average")
    ram = _ps("$o=Get-CimInstance Win32_OperatingSystem; "
              "[math]::Round(100*($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/$o.TotalVisibleMemorySize,0)")
    try:
        if int(str(cpu).strip() or 0) >= 85:
            out.append({"area": "CPU", "ok": False, "detail": f"carga al {cpu}%"})
        else:
            out.append({"area": "CPU", "ok": True, "detail": f"carga al {cpu}%"})
    except Exception:
        out.append({"area": "CPU", "ok": None, "detail": f"carga {cpu}"})
    try:
        if int(str(ram).strip() or 0) >= 90:
            out.append({"area": "Memoria", "ok": False, "detail": f"ocupada al {ram}%"})
        else:
            out.append({"area": "Memoria", "ok": True, "detail": f"ocupada al {ram}%"})
    except Exception:
        out.append({"area": "Memoria", "ok": None, "detail": f"ocupada {ram}"})
    disks = _ps("Get-PSDrive -PSProvider FileSystem | ForEach-Object { "
                "$_.Name + ':' + [math]::Round(100*$_.Used/($_.Used+$_.Free),0) }")
    for line in [l.strip() for l in disks.splitlines() if l.strip()][:6]:
        try:
            nom, pct = line.rsplit(":", 1)
            p = int(pct)
            if p >= 92:
                out.append({"area": f"Disco {nom}", "ok": False,
                            "detail": f"al {p}%: conviene liberar espacio"})
        except Exception:
            continue
    top = _ps("Get-Process | Sort-Object CPU -Descending | Select-Object -First 4 | "
              "ForEach-Object { $_.Name }")
    if top and not top.startswith("ERR"):
        out.append({"area": "Procesos", "ok": None,
                    "detail": "más acumuladores: " + ", ".join(top.splitlines()[:4])})
    return out


def _check_health() -> list:
    out = []
    smart = _ps("Get-PhysicalDisk | ForEach-Object { "
                "$_.FriendlyName + '|' + $_.HealthStatus + '|' + $_.OperationalStatus }", timeout=60)
    if smart and not smart.startswith("ERR"):
        for line in smart.splitlines():
            if "Warning" in line or "Degraded" in line or "Unhealthy" in line:
                out.append({"area": "Disco físico", "ok": False, "detail": line.strip()})
        if not out:
            out.append({"area": "Disco físico", "ok": True, "detail": "SMART correcto"})
    # Los errores del registro System son de dos muy distintos tipos, y mezclados
    # dan una alarma falsa:
    #   1) averías de verdad del equipo (disco, memoria, kernel, perfil);
    #   2) ruido de terceros: servicios opcionales de Intel, del antivirus o del
    #      actualizador de Edge que expiran al arrancar, y los choques del
    #      FrameServer que dejó Chromium cuando aún usaba la cámara del sistema.
    # Solo cuenta el primer tipo.
    RUIDO = ("FrameServer", "Windows Error Reporting",
             "Intel(R) Platform License Manager", "Intel(R) TPM Provisioning",
             "cplspcon", "edgeupdate", "Intel(R) Express", "Intel(R) PROSet",
             "Wdf01000", "WudfInstall", "Microsoft-Windows-Search",
             "SysMain", "WSearch", "DiagTrack", "WerSvc")
    ruido = "|".join(r for r in RUIDO)
    eventos = _ps(
        "try { (Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; "
        "StartTime=(Get-Date).AddDays(-3)} -EA Stop | Where-Object { "
        "$_.ProviderName -notmatch '" + ruido + "' -and "
        "$_.Message -notmatch 'tiempo de espera.*para la conexi.n con el servicio' "
        "-and $_.Message -notmatch 'no pudo iniciarse debido al siguiente error' "
        # El registro de servicios trae el nombre del culpable en el texto, no en
        # el ProviderName (todos son 'Service Control Manager').
        "-and $_.Message -notmatch 'FrameServer|Intel|cplspcon|edgeupdate' "
        "} | Measure-Object).Count } catch { 0 }", timeout=90)
    try:
        n = int(eventos.strip() or 0)
        if n > 20:
            out.append({"area": "Eventos del sistema", "ok": False,
                        "detail": f"{n} errores críticos en 3 días"})
        else:
            out.append({"area": "Eventos del sistema", "ok": True,
                        "detail": f"{n} errores críticos en 3 días"})
    except Exception:
        pass
    return out


def _check_jarvis() -> list:
    out = []
    try:
        from config import Config, DATA_DIR
        need = ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]
        faltan = [k for k in need if not getattr(Config, k, "")]
        if faltan:
            out.append({"area": "Proveedores", "ok": True,
                        "detail": "sin clave: " + ", ".join(faltan)})
        else:
            out.append({"area": "Proveedores", "ok": True, "detail": "los tres proveedores con clave"})
    except Exception as e:
        out.append({"area": "Proveedores", "ok": False, "detail": str(e)[:120]})
    try:
        from memory import sqlite_store as db
        # get_conn() devuelve la conexión COMPARTIDA: nunca se cierra aquí.
        n = db.get_conn().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        out.append({"area": "Memoria", "ok": True,
                    "detail": f"historial con {n} mensajes"})
    except Exception as e:
        out.append({"area": "Memoria", "ok": False, "detail": str(e)[:120]})
    try:
        from config import BRAIN_DIR
        for nm in ("brain.err.log",):
            p = os.path.join(str(BRAIN_DIR), nm)
            if os.path.isfile(p) and os.path.getsize(p) > 5 * 1024 * 1024:
                out.append({"area": "Registro", "ok": False,
                            "detail": f"{nm} supera 5 MB: ocupa espacio sin Control Disk Cleanup"})
    except Exception:
        pass
    return out


def _check_net() -> list:
    # User-Agent: sin esto, Cloudflare devuelve 403 y este chequeo diría
    # "conectado" cuando en realidad JARVIS no puede hablar con nadie. Y si no
    # lo entiende como HTTP, diría "sin internet" con la red perfectamente
    # buena. Es el mismo problema que tenía el proveedor de Groq.
    import urllib.request
    peticion = urllib.request.Request(
        "https://api.groq.com",
        headers={"User-Agent": "openai-python/1.40.0"})
    try:
        urllib.request.urlopen(peticion, timeout=6)
        return [{"area": "Red", "ok": True, "detail": "conectado a internet"}]
    except urllib.error.HTTPError as e:
        # Un 403 de Cloudflare SÍ quiere decir que hay red: la petición llegó
        # hasta el borde y la rechazaron. Antes esto pasaba por el mismo `except`
        # que el de "sin internet", y no era lo mismo.
        if e.code in (401, 403):
            return [{"area": "Red", "ok": True,
                     "detail": f"conectado a internet (Groq responde {e.code}: "
                               f"revise la clave)"}]
        return [{"area": "Red", "ok": True, "detail": "conectado a internet"}]
    except Exception as e:
        return [{"area": "Red", "ok": False, "detail": f"sin internet: {str(e)[:80]}"}]


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #
def system_diagnose_handler(params: dict) -> dict:
    """Barrido completo del equipo, solo lectura, con progreso visible."""
    areas = (params.get("areas") or "").lower()
    want_jarvis = "jarvis" in areas or not areas
    want_sec = "seguridad" in areas or "virus" in areas or not areas
    want_perf = "rendimiento" in areas or "sistema" in areas or not areas
    want_hard = "hardware" in areas or "salud" in areas or not areas

    t0 = time.time()
    results = []
    try:
        from status import start_task
        start_task("Investigando el equipo")
    except Exception:
        pass

    if want_jarvis:
        _step("Revisando la voz de JARVIS (TTS)", first=True)
        results += _check_tts()
        _step("Revisando el micrófono y la transcripción (STT)")
        results += _check_stt()
        _step("Revisando la cámara")
        results += _check_camera()
        _step("Revisando la memoria de JARVIS")
        results += _check_jarvis()
    if want_sec:
        _step("Analizando la seguridad del sistema")
        results += _check_security()
    if want_perf:
        _step("Midiendo CPU, memoria y discos")
        results += _check_performance()
    if want_hard:
        _step("Comprobando la salud de los discos y del sistema")
        results += _check_health()
    _step("Comprobando la conexión")
    results += _check_net()

    fallos = [r for r in results if r.get("ok") is False]
    resumen = (f"Investigación terminada: {len(results)} comprobaciones, "
               f"{len(fallos)} con problemas.")
    try:
        from status import end_task
        end_task(resumen)
    except Exception:
        pass
    return {"total": len(results), "problems": len(fallos),
            "findings": results, "summary": resumen,
            "seconds": round(time.time() - t0, 1)}


DIAG_TOOLS = [
    {"name": "system_diagnose",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"areas": "str opcional: jarvis, seguridad, rendimiento, hardware"},
     "description": "Investiga el equipo entero de forma solo lectura: voz TTS/STT, "
                    "micrófono, cámara, seguridad, virus, CPU, memoria, discos, "
                    "salud y red. Devuelve los problemas encontrados.",
     "label": "Investigando el equipo",
     "handler": system_diagnose_handler},
]
