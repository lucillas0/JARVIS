"""Test Center y checkpoints de Git: las dos piezas de "controlar de verdad".

Test Center
    Detecta el comando de tests REAL del proyecto (package.json, pytest.ini,
    pyproject, go.mod, Cargo.toml...) y lo ejecuta de verdad, guardando
    comando, duración, salida y resultado. Si el proyecto no tiene tests, lo
    dice: no inventa ni un número.

Checkpoints
    Antes de una modificación importante se guarda el estado de Git como parche
    (`git diff` + `git diff --cached`), que se puede comparar, restaurar
    (con `git apply`) y borrar. Nunca hace checkout, nunca tira trabajo:
    restaurar es siempre una decisión del señor y pasa por el RiskEngine.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

from . import store

log = logging.getLogger("brain.cmd.runner")

TIMEOUT_DEFECTO = 600
_jobs: dict = {}
_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# Test Center
# --------------------------------------------------------------------------- #
def detectar_tests(ruta: str) -> dict:
    """El comando de tests que el propio proyecto declara. Nada inventado."""
    ruta = os.path.abspath(ruta or ".")
    if not os.path.isdir(ruta):
        return {"ok": False, "error": "Esa carpeta no existe."}
    candidatos: list[dict] = []

    paquete = os.path.join(ruta, "package.json")
    if os.path.isfile(paquete):
        try:
            datos = json.loads(open(paquete, "r", encoding="utf-8", errors="replace").read())
            for clave, valor in (datos.get("scripts") or {}).items():
                if re.search(r"^(test|check|verify)$", clave) or "test" in clave.lower():
                    candidatos.append({"tipo": "npm", "comando": f"npm run {clave}",
                                       "detalle": str(valor)[:120]})
        except (OSError, ValueError):
            pass

    for nombre, comando in (("pytest.ini", "pytest -q"), ("pyproject.toml", "pytest -q"),
                            ("tox.ini", "python -m pytest -q"),
                            ("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"),
                            ("phpunit.xml", "vendor/bin/phpunit"),
                            ("Makefile", "make test")):
        if os.path.isfile(os.path.join(ruta, nombre)):
            candidatos.append({"tipo": "proyecto", "comando": comando, "detalle": nombre})

    tests_dir = any(os.path.isdir(os.path.join(ruta, d)) for d in ("tests", "test"))
    if tests_dir or any(f.startswith("test_") and f.endswith(".py")
                        for f in os.listdir(ruta)):
        candidatos.append({"tipo": "python", "comando": "python -m pytest -q",
                           "detalle": "hay carpeta/ficheros de test"})
    return {"ok": True, "ruta": ruta,
            "comandos": candidatos,
            "tiene_tests": bool(candidatos),
            "detalle": ("El proyecto declara estos comandos de test."
                        if candidatos else
                        "No hay ningún comando de test declarado en este proyecto.")}


def ejecutar_tests(ruta: str, comando: str = "", timeout: int = TIMEOUT_DEFECTO) -> dict:
    """Ejecuta los tests de verdad en segundo plano y devuelve el trabajo."""
    deteccion = detectar_tests(ruta)
    if not deteccion.get("ok"):
        return deteccion
    elegido = comando or (deteccion["comandos"][0]["comando"]
                          if deteccion["comandos"] else "")
    if not elegido:
        return {"ok": False,
                "error": "No hay tests que ejecutar en este proyecto."}
    import shlex
    try:
        argv = shlex.split(elegido, posix=(os.name != "nt"))
    except ValueError as exc:
        return {"ok": False, "error": f"No se entiendo el comando: {exc}"[:120]}
    job_id = f"test-{int(time.time() * 1000) % 1000000}"
    trabajo = {"id": job_id, "comando": elegido, "ruta": ruta, "estado": "ejecutando",
               "inicio": time.time(), "salida": "", "codigo": None, "error": ""}
    with _lock:
        _jobs[job_id] = trabajo

    def _correr():
        try:
            proc = subprocess.run(argv, cwd=ruta, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=max(30, int(timeout)),
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            trabajo["salida"] = ((proc.stdout or "") + (proc.stderr or ""))[-8000:]
            trabajo["codigo"] = proc.returncode
            trabajo["estado"] = "completado" if proc.returncode == 0 else "fallido"
        except subprocess.TimeoutExpired:
            trabajo["estado"] = "fallido"
            trabajo["error"] = "Los tests tardaron demasiado y se pararon."
        except Exception as exc:  # noqa: BLE001
            trabajo["estado"] = "fallido"
            trabajo["error"] = str(exc)[:200]
        finally:
            trabajo["fin"] = time.time()
            trabajo["duracion"] = round(trabajo["fin"] - trabajo["inicio"], 1)
            trabajo["resumen"] = _resumen_tests(trabajo["salida"])

    threading.Thread(target=_correr, daemon=True, name=f"cmd-tests-{job_id}").start()
    return {"ok": True, "job_id": job_id, "comando": elegido,
            "mensaje": "Tests en marcha; se ven en el Test Center."}


def estado_test(job_id: str) -> dict:
    with _lock:
        trabajo = _jobs.get(job_id)
    if not trabajo:
        return {"ok": False, "error": "Ese trabajo de tests ya no está."}
    copia = dict(trabajo)
    if trabajo["estado"] == "ejecutando":
        copia["salida"] = (copia.get("salida") or "")[-1500:]
    return {"ok": True, **copia}


def listar_tests(limite: int = 10) -> dict:
    with _lock:
        trabajos = list(_jobs.values())[-limite:]
    return {"ok": True,
            "trabajos": [{"id": t["id"], "comando": t["comando"], "estado": t["estado"],
                          "duracion": t.get("duracion"), "resumen": t.get("resumen", ""),
                          "error": t.get("error", "")} for t in trabajos]}


def _resumen_tests(salida: str) -> str:
    cuentas = {"passed": None, "failed": None, "errors": None, "skipped": None}
    for clave, patron in (("passed", r"(\d+)\s+passed"), ("failed", r"(\d+)\s+failed"),
                          ("errors", r"(\d+)\s+error"), ("skipped", r"(\d+)\s+skipped")):
        m = re.search(patron, salida or "", re.I)
        if m:
            try:
                cuentas[clave] = int(m.group(1))
            except ValueError:
                pass
    primera = ""
    for linea in (salida or "").splitlines():
        if linea.strip():
            primera = linea.strip()[:200]
            if re.search(r"passed|failed|ok\b", primera, re.I):
                break
    return json.dumps({"cuentas": cuentas, "primera_linea": primera}, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Checkpoints de Git
# --------------------------------------------------------------------------- #
def _git(ruta: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", ruta, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, check=False)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def es_repo(ruta: str) -> bool:
    codigo, salida = _git(os.path.abspath(ruta or "."), "rev-parse", "--is-inside-work-tree")
    return codigo == 0 and salida.strip() == "true"


def _dir_checkpoints() -> str:
    try:
        from config import DATA_DIR
        base = os.path.join(str(DATA_DIR), "checkpoints")
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.expanduser("~"), ".jarvis", "checkpoints")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def crear_checkpoint(ruta: str, mission_id: str = "", nota: str = "") -> dict:
    """Guarda el estado actual: parche de lo ya versionado + copia de los
    ficheros nuevos (que `git diff` no ve porque no están en el índice).

    No toca el árbol de trabajo ni el índice: ni `git add`, ni `git reset`,
    ni checkout. Restaurar es siempre una decisión del señor.
    """
    ruta = os.path.abspath(ruta or ".")
    if not es_repo(ruta):
        return {"ok": False, "error": "Esa carpeta no es un repositorio Git."}
    codigo, cabeza = _git(ruta, "rev-parse", "HEAD")
    _, cambios = _git(ruta, "status", "--porcelain")
    _, parche = _git(ruta, "diff", "HEAD")
    _, staged = _git(ruta, "diff", "--cached")
    _, nuevos = _git(ruta, "ls-files", "--others", "--exclude-standard")
    if not parche and not staged and not nuevos.strip():
        return {"ok": True, "checkpoint": None,
                "mensaje": "No hay cambios sin guardar: no hace falta checkpoint."}
    marca = f"{int(time.time())}-{os.path.basename(ruta)}"
    carpeta = os.path.join(_dir_checkpoints(), marca)
    archivo = os.path.join(carpeta, "cambios.patch")
    copiados: list = []
    try:
        os.makedirs(carpeta, exist_ok=True)
        with open(archivo, "w", encoding="utf-8") as f:
            f.write(staged or "")
            f.write(parche or "")
        for relativo in [l.strip() for l in nuevos.splitlines() if l.strip()][:200]:
            origen = os.path.join(ruta, relativo)
            destino = os.path.join(carpeta, "nuevos", relativo)
            try:
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                shutil.copy2(origen, destino)
                copiados.append(relativo)
            except OSError:
                continue
    except OSError as exc:
        return {"ok": False, "error": f"No se pudo guardar el checkpoint: {exc}"[:160]}
    ficheros = len([l for l in cambios.splitlines() if l.strip()])
    fila = store.crear_checkpoint(path=ruta, head=cabeza.strip(),
                                  patch_path=carpeta, files=ficheros,
                                  mission_id=mission_id, note=nota, dirty=True)
    fila["nuevos"] = copiados
    return {"ok": True, "checkpoint": fila,
            "mensaje": f"Checkpoint guardado ({ficheros} cambios, "
                       f"{len(copiados)} ficheros nuevos). Para volver: «RESTAURAR»."}


def listar_checkpoints(ruta: str = "") -> dict:
    filas = store.listar_checkpoints(os.path.abspath(ruta) if ruta else "")
    return {"ok": True, "checkpoints": filas}


def comparar_checkpoint(cp_id: str) -> dict:
    """Que cambia este checkpoint respecto a lo que hay ahora mismo."""
    fila = store.obtener_checkpoint(cp_id)
    if not fila:
        return {"ok": False, "error": "Ese checkpoint no existe."}
    carpeta = fila.get("patch_path") or ""
    try:
        with open(os.path.join(carpeta, "cambios.patch"), "r", encoding="utf-8") as f:
            parche = f.read()
    except OSError as exc:
        return {"ok": False, "error": f"No se pudo leer el parche: {exc}"[:160]}
    nuevos = _nuevos_guardados(carpeta)
    _, estado = _git(fila["path"], "status", "--porcelain")
    return {"ok": True, "checkpoint": fila,
            "archivos_ahora": [l[3:] for l in estado.splitlines() if l.strip()][:60],
            "nuevos_guardados": nuevos[:60],
            "parche": parche[:20000],
            "resumen": _resumen_parche(parche)}


def _nuevos_guardados(carpeta: str) -> list:
    """Ficheros nuevos que el checkpoint guardó aparte (git diff no los ve)."""
    base = os.path.join(carpeta, "nuevos")
    salida = []
    for carpeta_raiz, _, ficheros in os.walk(base):
        for nombre in ficheros:
            salida.append(os.path.relpath(os.path.join(carpeta_raiz, nombre), base)
                          .replace("\\", "/"))
    return sorted(salida)
def _resumen_parche(parche: str) -> dict:
    ficheros: dict = {}
    for linea in (parche or "").splitlines():
        if linea.startswith("+++ b/"):
            nombre = linea[6:].strip()
            ficheros[nombre] = {"added": 0, "deleted": 0}
        elif linea.startswith("+") and not linea.startswith("+++") and ficheros:
            ultimo = list(ficheros)[-1]
            ficheros[ultimo]["added"] += 1
        elif linea.startswith("-") and not linea.startswith("---") and ficheros:
            ultimo = list(ficheros)[-1]
            ficheros[ultimo]["deleted"] += 1
    return {k: v for k, v in list(ficheros.items())[:40]}


def restaurar_checkpoint(cp_id: str) -> dict:
    """Aplica el parche y devuelve los ficheros nuevos.

    Nunca descarta nada: si el parche chocaría con lo que hay ahora, lo dice
    y no toca ni un fichero.
    """
    fila = store.obtener_checkpoint(cp_id)
    if not fila:
        return {"ok": False, "error": "Ese checkpoint no existe."}
    ruta = fila["path"]
    carpeta = fila.get("patch_path") or ""
    try:
        with open(os.path.join(carpeta, "cambios.patch"), "r", encoding="utf-8") as f:
            parche = f.read()
    except OSError as exc:
        return {"ok": False, "error": f"No se pudo leer el parche: {exc}"[:160]}
    nuevos = _nuevos_guardados(carpeta)
    if not parche.strip() and not nuevos:
        return {"ok": False, "error": "El checkpoint está vacío."}
    aplicados = []
    if parche.strip():
        codigo, salida = _git_con_entrada(ruta, "apply", "--check", "-", parche)
        if codigo != 0:
            return {"ok": False,
                    "error": "Restaurar ahora chocaría con los cambios actuales. "
                             "Revísalo antes de seguir.",
                    "detalle": salida[-400:]}
        codigo, salida = _git_con_entrada(ruta, "apply", "-", parche)
        if codigo != 0:
            return {"ok": False, "error": "Git no aplicó el parche.",
                    "detalle": salida[-400:]}
        aplicados.append("cambios versionados")
    for relativo in nuevos:
        origen = os.path.join(carpeta, "nuevos", relativo.replace("/", os.sep))
        destino = os.path.join(ruta, relativo.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origen, destino)
            aplicados.append(relativo)
        except OSError:
            continue
    return {"ok": True,
            "mensaje": "Checkpoint restaurado (" + ", ".join(aplicados[:4]) + ")."}


def _git_con_entrada(ruta: str, *args: str, entrada: str = "") -> tuple:
    try:
        proc = subprocess.run(["git", "-C", ruta, *args], input=entrada,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, check=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)
def borrar_checkpoint(cp_id: str) -> dict:
    return {"ok": store.borrar_checkpoint(cp_id),
            "mensaje": "Checkpoint borrado."}


def cambios_git(ruta: str = "") -> dict:
    """Estado real de Git: qué cambió, con añadidos y borrados."""
    ruta = os.path.abspath(ruta or ".")
    if not es_repo(ruta):
        return {"ok": False, "repo": False,
                "error": "Esta carpeta no es un repositorio Git."}
    _, rama = _git(ruta, "rev-parse", "--abbrev-ref", "HEAD")
    _, estado = _git(ruta, "status", "--porcelain")
    _, staged = _git(ruta, "diff", "--cached", "--stat")
    _, resumen = _git(ruta, "diff", "--stat")
    cambios = []
    for linea in estado.splitlines()[:300]:
        if len(linea) < 4:
            continue
        codigo, ruta_fichero = linea[:2].strip() or "?", linea[3:].strip()
        marca = {"M": "~", "A": "+", "D": "-", "R": "»", "C": "+"}.get(codigo[:1], "~")
        cambios.append({"path": ruta_fichero[:240], "codigo": codigo[:2], "marca": marca,
                        "estado": {"M": "modified", "A": "added", "D": "deleted",
                                   "R": "renamed", "C": "copied"}.get(codigo[:1], "modified")})
    _, log = _git(ruta, "log", "--oneline", "-5")
    return {"ok": True, "repo": True, "rama": rama.strip(), "cambios": cambios,
            "total": len(cambios), "stat": (resumen or staged)[-1200:],
            "ultimos_commits": [l for l in log.splitlines() if l.strip()][:5]}


def diff_fichero(ruta_repo: str, fichero: str) -> dict:
    """Diff real de un fichero (o del staging)."""
    if not es_repo(ruta_repo):
        return {"ok": False, "error": "No es un repositorio Git."}
    args = ["diff", "HEAD", "--", fichero] if fichero else ["diff", "HEAD"]
    codigo, salida = _git(ruta_repo, *args)
    return {"ok": codigo == 0, "fichero": fichero, "diff": salida[-20000:],
            "vacio": not salida.strip()}


def commit(ruta_repo: str, mensaje: str) -> dict:
    """Confirma cambios. Lo llama el panel con el RiskEngine delante."""
    if not es_repo(ruta_repo):
        return {"ok": False, "error": "No es un repositorio Git."}
    if not (mensaje or "").strip():
        return {"ok": False, "error": "El commit necesita un mensaje."}
    _, salida = _git(ruta_repo, "add", "-A", timeout=60)
    codigo, resultado = _git(ruta_repo, "commit", "-m", mensaje, timeout=60)
    if codigo != 0:
        return {"ok": False, "error": resultado[-300:]}
    return {"ok": True, "mensaje": "Cambios confirmados.", "salida": resultado[-300:]}
