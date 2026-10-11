# -*- coding: utf-8 -*-
"""Cámara: diagnóstico y limpieza de dispositivos fantasma.

El fallo típico de este equipo no es que la cámara esté rota, sino que Windows
se queda con registros de cámaras que ya no están (cambiaste de puerto USB) y
Chromium elige uno de esos: "Could not start video source" o "Requested device
not found", aunque la cámara real funcione perfectamente. Se distingue por
Get-PnpDevice: las fantasma aparecen con Present=False / estado distinto de OK.

Quitar esos registros es tarea de administrador, así que la reparación pide
permisos con el diálogo de UAC (un solo aviso) y luego comprueba que ya solo
queda la cámara de verdad.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time

log = logging.getLogger("tools.camera")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PS = "powershell"
_emitir = None


def set_emisor(fn) -> None:
    """El brain pasa aqui su funcion para avisar a la interfaz."""
    global _emitir
    _emitir = fn


def _ps(script: str, timeout: int = 60) -> str:
    r = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout,
                       creationflags=_NO_WINDOW)
    return (r.stdout or "").strip()


def _camaras() -> list:
    """Cada cámara con su estado y si realmente está conectada ahora."""
    ps = ("Get-PnpDevice -Class Camera -ErrorAction SilentlyContinue | "
          "ForEach-Object { "
          "$_.InstanceId + '|' + $_.Status + '|' + $_.Present + '|' + $_.FriendlyName }")
    out = []
    for linea in _ps(ps).splitlines():
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) < 4 or not partes[0]:
            continue
        out.append({"instancia": partes[0], "estado": partes[1],
                    "presente": partes[2].lower() == "true",
                    "nombre": "|".join(partes[3:])})
    return out


def _fantasmas(camaras: list) -> list:
    return [c for c in camaras if not c["presente"] or c["estado"] != "OK"]


def _soy_admin() -> bool:
    try:
        return "Admin" in _ps(
            "([Security.Principal.WindowsPrincipal]"
            "[Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)", 30)
    except Exception:
        return False


def _privacidad() -> str:
    clave = (r"HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion"
             r"\CapabilityAccessManager\ConsentStore\webcam")
    return _ps(f"(Get-ItemProperty '{clave}' -ErrorAction SilentlyContinue).Value", 30)


def _frameserver_caidas(minutos: int = 60) -> int:
    """Cuántas veces se ha caído el servicio de fotogramas de la cámara.

    Si se cae cada vez que algo abre la cámara, el problema no está en quien
    llama (navegador, Electron) sino en Windows: el servicio revienta al
    negociar el vídeo. Es lo que hace que Chromium diga 'Could not start video
    source' y se quede colgado.
    """
    ps = ("$n=(Get-Date).AddMinutes(-{m}); "
          "(Get-WinEvent -FilterHashtable "
          "@{{LogName='System'; Id=7034; StartTime=$n}} -ErrorAction SilentlyContinue | "
          "Where-Object {{ $_.Message -match 'FrameServer' }} | "
          "Measure-Object).Count").format(m=minutos)
    try:
        return int(_ps(ps, 60) or 0)
    except Exception:
        return 0


def _h_estado(params: dict) -> dict:
    camaras = _camaras()
    vivas = [c for c in camaras if c["presente"] and c["estado"] == "OK"]
    fantasmas = [c for c in camaras if not c["presente"] or c["estado"] != "OK"]
    caidas = _frameserver_caidas()
    salida = {
        "camaras": camaras,
        "vivas": len(vivas),
        "fantasmas": len(fantasmas),
        "servicio_fotogramas_caido_veces": caidas,
        "privilegios_admin": _soy_admin(),
        "privacidad_windows": _privacidad(),
    }
    if not camaras:
        salida["diagnostico"] = ("Windows no ve ninguna cámara. Si es USB, "
                                 "prueba otro puerto (trasero mejor) o otro cable.")
    elif not vivas:
        salida["diagnostico"] = ("Hay cámaras registradas pero ninguna está "
                                 "presente: la cámara está desconectada o el "
                                 "puerto USB no da corriente.")
    elif caidas >= 2:
        salida["diagnostico"] = (
            f"El servicio de la cámara de Windows se ha caído {caidas} veces en "
            "la última hora: revienta al abrir el vídeo, y por eso el tracker "
            "dice 'Could not start video source'. La cámara y su driver están "
            "bien; lo que está roto es Windows. Se arregla reparando el "
            "sistema con 'sfc /scannow' en consola de administrador (tarda "
            "10-20 minutos y puede pedir reiniciar).")
        salida["arreglo_windows"] = "sfc /scannow"
    elif fantasmas:
        salida["diagnostico"] = (
            f"La cámara real funciona, pero Windows conserva {len(fantasmas)} "
            "cámara(s) fantasma de puertos USB anteriores. El tracker elige una "
            "de esas y falla con 'Could not start video source'. Se arregla con "
            "camara_reparar (pide permisos de administrador).")
    else:
        salida["diagnostico"] = (f"{len(vivas)} cámara(s) operativa(s) y sin "
                                 "fantasmas. Si el tracker falla, el problema es "
                                 "de permisos o de que otra app la tenga "
                                 "ocupada.")
    return salida


def _script_powershell(ids: list, resultado: str) -> str:
    lista = ", ".join("'" + i.replace("'", "''") + "'" for i in ids)
    return (
        "$ErrorActionPreference = 'Continue'\n"
        "$salida = @()\n"
        # 1) El servicio de fotogramas puede tener la cámara cogida.
        "try { Stop-Service -Name FrameServer -Force -ErrorAction SilentlyContinue\n"
        "      $salida += 'fotogramas: servicio detenido' }\n"
        "catch { $salida += ('fotogramas: ' + $_.Exception.Message) }\n"
        # 2) Fuera las cámaras fantasma de puertos USB anteriores.
        f"$ids = @({lista})\n"
        "foreach ($id in $ids) {\n"
        "  try {\n"
        "    $r = (pnputil /remove-device $id 2>&1 | Out-String).Trim()\n"
        "    $salida += ($id + '|OK|' + ($r -replace \"`r?`n\", ' '))\n"
        "  } catch {\n"
        "    $salida += ($id + '|ERR|' + $_.Exception.Message)\n"
        "  }\n"
        "}\n"
        # 3) Reiniciar la cámara de verdad: si quedó atascada, esto la suelta.
        "try {\n"
        "  $viva = (Get-PnpDevice -Class Camera -ErrorAction SilentlyContinue |\n"
        "           Where-Object { $_.Present -eq $true -and $_.Status -eq 'OK' } |\n"
        "           Select-Object -First 1).InstanceId\n"
        "  if ($viva) {\n"
        "    $r = (pnputil /restart-device $viva 2>&1 | Out-String).Trim()\n"
        "    $salida += ('reinicio|' + $viva + '|' + ($r -replace \"`r?`n\", ' '))\n"
        "  } else { $salida += 'reinicio|no hay camara presente' }\n"
        "} catch { $salida += ('reinicio|ERR|' + $_.Exception.Message) }\n"
        # 4) Que Windows vuelva a enumerar el hardware.
        "try { $r = (pnputil /scan-devices 2>&1 | Out-String).Trim()\n"
        "      $salida += ('scan|' + ($r -replace \"`r?`n\", ' ')) }\n"
        "catch { $salida += ('scan|ERR|' + $_.Exception.Message) }\n"
        "$salida -join \"`n\" | Set-Content -Path '" + resultado.replace("'", "''") + "' -Encoding UTF8\n"
    )


def _h_reparar(params: dict) -> dict:
    antes = _camaras()
    fantasmas = _fantasmas(antes)
    vivas = [c for c in antes if c["presente"] and c["estado"] == "OK"]
    if not fantasmas and not vivas:
        return {"ok": False,
                "mensaje": "No hay cámaras fantasma, pero tampoco ninguna "
                           "cámara presente: revisa el cable o el puerto USB."}
    if not fantasmas and not params.get("reiniciar"):
        return {"estado": _h_estado(params),
                "nota": "No hay cámaras fantasma. Si la cámara sigue sin abrir, "
                        "se puede reiniciar el dispositivo con "
                        "camara_reparar reiniciar."}
    if not _soy_admin():
        temporal = tempfile.gettempdir()
        script = os.path.join(temporal, "jarvis_camara_fix.ps1")
        resultado = os.path.join(temporal, "jarvis_camara_fix.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(_script_powershell([c["instancia"] for c in fantasmas], resultado))
        if os.path.exists(resultado):
            try:
                os.remove(resultado)
            except Exception:
                pass
        cmd = (f"Start-Process -FilePath '{PS}' -Verb RunAs -Wait -ArgumentList "
               f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{script}'")
        try:
            r = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", cmd],
                               capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace",
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"ok": False,
                    "mensaje": "El diálogo de permisos se quedó esperando. "
                                "Acepta el aviso de administrador de Windows y "
                                "vuelvo a intentarlo."}
        if r.returncode != 0 or not os.path.exists(resultado):
            detalle = ((r.stderr or r.stdout) or "").strip()[:300]
            return {"ok": False,
                    "mensaje": "No se pudo hacer con permisos de administrador. "
                               "Puede que se haya rechazado el aviso de UAC."
                               + (f" Detalle: {detalle}" if detalle else ""),
                    "comando_manual": (
                        "Abre PowerShell como administrador y ejecuta: "
                        "Stop-Service FrameServer -Force; "
                        "pnputil /restart-device \"" + vivas[0]["instancia"] + "\"") }
        detalle = ""
        try:
            with open(resultado, "r", encoding="utf-8", errors="replace") as f:
                detalle = f.read().strip()[:800]
        except Exception as e:
            detalle = f"no se pudo leer el log: {e}"
    else:
        detalle = ""
        subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command",
                        "Stop-Service -Name FrameServer -Force"],
                       capture_output=True, text=True, timeout=60,
                       creationflags=_NO_WINDOW)
        for c in fantasmas:
            subprocess.run(["pnputil", "/remove-device", c["instancia"]],
                           capture_output=True, text=True, timeout=90,
                           creationflags=_NO_WINDOW)
        for c in vivas:
            subprocess.run(["pnputil", "/restart-device", c["instancia"]],
                           capture_output=True, text=True, timeout=90,
                           creationflags=_NO_WINDOW)
        subprocess.run(["pnputil", "/scan-devices"], capture_output=True,
                       text=True, timeout=90, creationflags=_NO_WINDOW)

    time.sleep(3)
    despues = _h_estado(params)
    return {"ok": not despues["fantasmas"] and despues["vivas"] > 0,
            "antes": {"camaras": len(antes), "fantasmas": len(fantasmas),
                      "vivas": len(vivas)},
            "que_hizo": detalle,
            "despues": despues}


def _h_gestos(params: dict) -> dict:
    """Enciende o apaga el tracker de gestos de la mano."""
    estado = str(params.get("estado") or params.get("state") or "on").lower()
    if estado in ("off", "apagar", "para", "no"):
        estado = "off"
    else:
        estado = "on"
    if _emitir is None:
        raise RuntimeError("El cerebro no me deja hablar con la interfaz.")
    _emitir({"type": "gestures", "state": estado})
    return {"pedido": estado,
            "nota": "El HUD contesta con gestures_result cuando la cámara abre "
                    "o cuando falla y por qué."}


def _h_mirar(params: dict) -> dict:
    """Hace una foto con la cámara del cerebro y la analiza con visión."""
    peticion = (params.get("peticion") or params.get("prompt")
                or "Describe lo que ves en la cámara en una frase.")
    try:
        from vision.webcam import obtener as _wc
        jpg = _wc().jpeg(70)
    except Exception as e:
        raise RuntimeError(f"no pude abrir la cámara: {e}")
    if not jpg:
        raise RuntimeError("la cámara no entrega imagen. Dime 'estado de la "
                           "cámara' para ver qué pasa.")
    import base64
    from vision.analyzer import analyze_webcam_frame
    b64 = base64.b64encode(jpg).decode("ascii")
    texto = analyze_webcam_frame(b64, str(peticion))
    return {"peticion": str(peticion), "analisis": texto,
            "estado_camara": _wc().estado()}


CAMERA_TOOLS = [
    {"name": "camara_estado",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Estado de las cámaras del equipo: cuáles están de verdad, "
                    "cuántas cámaras fantasma ha dejado Windows y por qué no "
                    "logra abrirse la cámara.",
     "label": "Mirando la cámara", "handler": _h_estado},

    {"name": "camara_reparar",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Limpio y reinicio la cámara? Pedirá permisos de administrador.",
     "parameters": {"reiniciar": "bool opcional (reiniciar aunque no haya fantasmas)"},
     "description": "Arregla la cámara que no abre: quita las cámaras fantasma "
                    "de puertos USB anteriores, reinicia el dispositivo y suelta "
                    "el servicio de fotogramas. Pide permisos de administrador.",
     "label": "Arreglando la cámara", "handler": _h_reparar},

    {"name": "gestos_camara",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"estado": "on o off"},
     "description": "Enciende o apaga el control por gestos de la mano con la "
                    "cámara. Úsala cuando me pidan encender, apagar, activar o "
                    "probar los gestos, las manos, el tracker o la cámara por "
                    "gestos. Devuelve el error exacto si la cámara no abre.",
     "label": "Encendiendo los gestos", "handler": _h_gestos},

    {"name": "camara_mirar",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"peticion": "str opcional (qué quieres que mire)"},
     "description": "Hace una foto con la cámara del cerebro y te dice lo que "
                    "ve. Úsala cuando me pidan mirar por la cámara, qué ve, "
                    "qué hay delante, o el análisis de una imagen de la webcam.",
     "label": "Mirando por la cámara", "handler": _h_mirar},
]
