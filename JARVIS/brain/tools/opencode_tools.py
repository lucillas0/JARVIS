# -*- coding: utf-8 -*-
"""Puente con OpenCode CLI: delega reparaciones con verificación.

opencode_fix(task): snapshot de .py -> ejecuta `opencode run` con modelo
barato -> detecta cambios -> py_compile -> rollback si rompe.
NUNCA toca keys.env, *.db ni credenciales. Solo código del brain.
"""
import logging
import os
import subprocess
import time

log = logging.getLogger("tools.opencode")

# --- Progreso hacia los paneles (sin import circular) -----------------------
# main.py cablea brain.emit aqui al arrancar, igual que hace con
# status.set_emit. Las tools nunca importan main.
_progress_emit = None


def set_progress_emit(fn) -> None:
    """Lo llama main.py una vez: a partir de ahi los jobs emiten pasos."""
    global _progress_emit
    _progress_emit = fn


def _step(task: str, label: str, done: bool = False, error: str = "") -> None:
    fn = _progress_emit
    if fn is None:
        return
    try:
        fn({"type": "task_step", "task": task[:120], "label": label[:240],
            "done": bool(done), "error": (error or "")[:240]})
    except Exception as e:
        log.debug("progress emit: %s", e)

OC_CMD = r"C:\Users\Lucas\AppData\Roaming\npm\opencode.cmd"
OC_MODEL = "opencode/muse-spark-1.3-contributor-free"
OC_TIMEOUT = 480
PROTECT = ("keys.env", ".env", ".db", ".db-wal", ".db-shm", "auth.json",
           "creds.json", "opencode_tools.py")
# Directorios que OpenCode no pisa jamás (dependencias, venvs, instaladores).
SKIP_DIRS = ("node_modules", "venv", ".venv", "dist-installer", "win-unpacked",
             "__pycache__", ".git", "qdrant", "wa-session")
# Lockfiles: al cerrar el job se cuentan aparte para no ensuciar la lista.
_LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Cargo.lock", "Gemfile.lock", "composer.lock")


def ensure_opencode(timeout: int = 180) -> bool:
    """Garantiza OpenCode CLI: si falta, intenta instalarlo con npm."""
    if _cli_disponible():
        return True
    try:
        import subprocess as _sp
        _sp.run(["npm", "install", "-g", "opencode"],
                capture_output=True, timeout=timeout,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log.warning("auto-install opencode: %s", e)
    return bool(_cli_disponible())


def _cli_disponible() -> str:
    """Ruta del CLI si esta, sin instalar nada: comprobar es instantaneo.

    Se usa en el camino caliente (opencode_build). La autoinstalacion con npm
    tarda minutos y bloquea al servidor: solo la intenta opencode_fix, que ya
    era sincrono de por si.
    """
    if os.path.isfile(OC_CMD):
        return OC_CMD
    try:
        import shutil as _sh
        for cand in ("opencode.cmd", "opencode.exe", "opencode"):
            ruta = _sh.which(cand)
            if ruta:
                return ruta
    except Exception:
        pass
    return ""


def _prompt_proyecto(objetivo: str, workdir: str) -> str:
    # .replace y no .format: el objetivo lo escribe el señor y puede traer
    # llaves, que con .format revientan con KeyError.
    return BUILD_PROMPT.replace("{workdir}", workdir).replace("{objetivo}", objetivo)

REPAIR_PROMPT = """Eres el reparador del asistente JARVIS (repo Python en {workdir}).
Intento número {attempt}: NO te rindas, los intentos anteriores fallaron.

ERROR:
- origen: {source}
- mensaje: {error}
- contexto:
{context}

REGLAS DURAS:
1. Solo puedes editar ficheros .py/.js/.html/.css del CÓDIGO FUENTE (brain/,
   app/ui/, app/splash/, app/setup-wizard/). PROHIBIDO tocar keys.env,
   .env, *.db, credenciales, tokens, opencode_tools.py, o borrar nada.
2. PROHIBIDO entrar en node_modules, venv, dist-installer, win-unpacked,
   __pycache__, .git, qdrant o wa-session (ni leer ni escribir).
3. Cambios mínimos que eliminen la causa raíz (no parches cosméticos).
4. No añadas dependencias nuevas. No cambies firmas públicas.
5. Si falta una capacidad (el error dice "no tengo herramienta" o similar),
   crea la herramienta en brain/tools/custom_tools.py como entrada CUSTOM_TOOLS
   con handler + risk_level + description, y avisa que hay que recargar.
6. Al terminar, resume en 3 líneas: causa, ficheros tocados, verificación.
7. Si no puedes repararlo con seguridad, di "NO-FIX: <motivo>" y no toques nada.
"""

BUILD_TOOL_PROMPT = """Eres el ingeniero del asistente JARVIS (repo Python en {workdir}).

FALTA UNA CAPACIDAD:
- origen: {source}
- petición/error: {error}
- contexto:
{context}

TAREA: crea la herramienta que falta en brain/tools/custom_tools.py.
Añade un handler `def <nombre>_handler(params: dict) -> dict:` que haga el trabajo
de verdad (sin inventar datos: si necesita un dato real, obténlo del sistema) y
una entrada en CUSTOM_TOOLS con este formato exacto:
{{"name": "<nombre>", "risk_level": "low|medium|sensitive",
  "requires_confirmation": False, "parameters": {{}},
  "description": "...", "label": "...", "handler": <nombre>_handler}}
Usa risk_level sensitive + requires_confirmation True si borra, apaga o toca red.
PROHIBIDO tocar keys.env, .env, *.db o credenciales. Sin dependencias nuevas.
Al terminar resume: nombre de la tool, ficheros tocados, verificación.
Si es imposible con seguridad, di "NO-FIX: <motivo>".
"""


def _keys_env() -> dict:
    env = dict(os.environ)
    try:
        base = env.get("APPDATA", "")
        p = os.path.join(base, "JARVIS", "keys.env") if base else ""
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        if k.strip() and v.strip():
                            env[k.strip()] = v.strip()
    except Exception as e:
        log.debug("opencode keys: %s", e)
    return env


def _snapshot(workdir: str) -> dict:
    snap = {}
    for dp, _, fns in os.walk(workdir):
        if any(s in dp.replace("\\", "/") for s in SKIP_DIRS):
            continue
        for fn in fns:
            if fn.endswith((".py", ".js", ".ts", ".json", ".html", ".css", ".md", ".txt", ".cjs")):
                p = os.path.join(dp, fn)
                try:
                    with open(p, "rb") as f:
                        import hashlib
                        snap[p] = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    pass
    return snap


def _restore(snapshot: dict) -> None:
    """Rollback: devuelve los ficheros a su estado previo."""
    for p, h in snapshot.items():
        try:
            with open(p, "rb") as f:
                import hashlib
                if hashlib.sha256(f.read()).hexdigest() == h:
                    continue
            bak = p + ".ocbak"
            if os.path.isfile(bak):
                import shutil
                shutil.copy2(bak, p)
                log.warning("opencode rollback: %s", p)
        except Exception as e:
            log.debug("rollback %s: %s", p, e)


def _brain_dir() -> str:
    try:
        from config import BRAIN_DIR
        return str(BRAIN_DIR)
    except Exception:
        return os.getcwd()


def opencode_fix(task: str, timeout: int = OC_TIMEOUT, workdir: str = "") -> dict:
    """Ejecuta OpenCode con la tarea. Devuelve {ok, output, files, error}."""
    workdir = workdir or _brain_dir()
    if not ensure_opencode():
        return {"ok": False, "error": "OpenCode CLI no instalado (auto-instalación falló)."}
    # Jaula: el trabajo siempre ocurre dentro de workdir; fuera de estos
    # directorios OpenCode no escribe aunque lo intente (rollback + veto).
    before = _snapshot(workdir)
    try:
        import shutil as _sh
        for p in before:
            try:
                _sh.copy2(p, p + ".ocbak")
            except Exception:
                pass
    except Exception:
        pass
    full_task = task  # ya viene con rol+reglas desde selfheal
    try:
        r = subprocess.run(
            [OC_CMD, "run", "-m", OC_MODEL, "--format", "json", "--auto",
             "--dir", workdir, full_task],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_keys_env(), cwd=workdir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "OpenCode tardó demasiado."}
    except Exception as e:
        return {"ok": False, "error": f"No pude lanzar OpenCode: {e}"}
    out = (r.stdout or "")[-2000:]
    if "NO-FIX" in out:
        return {"ok": False, "error": "OpenCode declinó: " + out[-300:]}
    after = _snapshot(workdir)
    changed = [p for p in after if after.get(p) != before.get(p)]
    changed = [p for p in changed
               if not any(x in p.replace("\\", "/") for x in PROTECT)
               and not any(s in p.replace("\\", "/") for s in SKIP_DIRS)]
    # Limpieza de copias de seguridad de runs previos (no son cambios).
    changed = [p for p in changed if not p.endswith(".ocbak")]
    if not changed:
        _restore(before)
        return {"ok": False,
                "error": "OpenCode no modificó ningún fichero."}
    # verifica sintaxis de lo tocado (.py y .js)
    import py_compile
    bad = []
    for p in changed:
        try:
            if p.endswith(".py"):
                py_compile.compile(p, doraise=True)
            elif p.endswith(".js"):
                import subprocess as _sp2
                r2 = _sp2.run(["node", "--check", p], capture_output=True,
                              timeout=60,
                              creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0))
                if r2.returncode != 0:
                    bad.append((p, (r2.stderr or "")[:150]))
        except Exception as e:
            bad.append((p, str(e)[:150]))
    if bad:
        _restore(before)
        return {"ok": False,
                "error": "Sintaxis rota en: " + ", ".join(f"{p}: {e}" for p, e in bad)
                + " (revertido)"}
    # Limpia los .ocbak de este run (ya verificados).
    for p in before:
        try:
            if os.path.isfile(p + ".ocbak"):
                os.remove(p + ".ocbak")
        except Exception:
            pass
    return {"ok": True, "output": out[-800:], "files": changed}


BUILD_PROMPT = """Eres el programador del asistente JARVIS. Te piden un proyecto nuevo
desde cero en {workdir} (esa carpeta ya existe y esta vacia o con lo justo).

PROYECTO PEDIDO:
{objetivo}

REGLAS DURAS:
1. Trabaja SOLO dentro de {workdir}. PROHIBIDO salir de ahi, tocar keys.env,
   .env, *.db, credenciales, tokens o borrar nada fuera del proyecto.
2. Sin dependencias pesadas ni raras: lo normal es un proyecto pequeno que se
   pueda abrir y usar. Si necesita instalar algo, que sea con el gestor
   habitual (npm install / pip install) y quede anotado en un README.
3. Codigo que funcione a la primera: al terminar, el proyecto debe arrancar
   con UN comando (anotalo en README.md: como instalar y como arrancar).
4. Estructura minima y clara: README.md, el codigo y nada mas que haga falta.
5. Al terminar resume en 5 lineas: que hace, ficheros creados, como arrancar,
   que falta (si falta algo, dilo, no lo inventes).
6. Habla poco mientras trabajas: cada fichero que crees o pruebes deja rastro
   en tu salida para que se vea el avance.
"""


def _dir_proyectos() -> str:
    """Carpeta donde viven los proyectos que JARVIS desarrolla."""
    try:
        from config import DATA_DIR
        base = os.path.join(str(DATA_DIR), "proyectos")
    except Exception:
        base = os.path.join(os.getcwd(), "proyectos")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


def _sanitizar_proyecto(nombre: str) -> str:
    """Nombre seguro para carpeta. Sin rutas, sin puntos, sin sorpresas."""
    n = (nombre or "").strip().lower().replace(" ", "-")
    n = "".join(c for c in n if c.isascii() and (c.isalnum() or c in ("-", "_")))
    return n[:40] or "proyecto"


_JOBS: dict = {}
_JOBS_LOCK = None


def _jobs_lock():
    global _JOBS_LOCK
    if _JOBS_LOCK is None:
        import threading
        _JOBS_LOCK = threading.Lock()
    return _JOBS_LOCK


def _resumen_linea(obj) -> str:
    """Una linea JSON de `opencode run --format json` -> texto corto.

    El esquema exacto de OpenCode puede cambiar entre versiones, asi que no se
    ata a ningun campo: prueba las claves habituales y si no hay nada, pasa.
    """
    if not isinstance(obj, dict):
        return ""
    for k in ("text", "message", "content", "title", "summary"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:160]
    t = str(obj.get("type", "") or "")
    nombre = str(obj.get("name", "") or obj.get("tool", "") or "")
    if t and "step" in t:
        return f"Paso: {nombre or t}"[:160]
    if nombre and any(w in t for w in ("tool", "call", "exec", "write", "edit")):
        return f"{nombre}"[:160]
    return ""


def _es_paso_importante(texto: str) -> bool:
    t = (texto or "").lower()
    return any(w in t for w in (
        "crea", "creado", "creando", "modifica", "modificado", "escrib",
        "instala", "instalado", "instalando", "ejecuta", "probando", "prueba",
        "error", "fallo", "completado", "terminado", "readme", "fichero",
        "archivo", "carpeta", "create", "write", "edit", "install", "test",
        "error", "done", "complete"))


def _matar_arbol(proc) -> None:
    """Mata el proceso y sus hijos (opencode lanza npm/node por debajo).

    Sin esto, matar al padre deja huerfanos compilando en segundo plano y el
    panel dice "detenido" mientras la CPU sigue ardiendo.
    """
    try:
        import psutil as _ps
        try:
            root = _ps.Process(proc.pid)
            for hijo in root.children(recursive=True):
                try:
                    hijo.kill()
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _ejecutar_job(job: dict, cmd: list, env: dict, timeout: int) -> None:
    """Corre el agente en un hilo: emite pasos y cierra la tarea al final.

    El timeout se mide con el RELOJ, no con las vueltas del bucle: antes se
    restaba uno por linea leida, asi que un agente hablador moria a los pocos
    segundos y uno callado colgaba el hilo para siempre (readline bloquea).
    Ahora un lector aparte encola las lineas y este bucle solo vigila el
    reloj y si el proceso sigue vivo.
    """
    import queue as _q
    import threading as _th
    import time as _t

    task = job["task"]
    workdir = job["workdir"]
    _step(task, "Agente OpenCode en marcha", done=False)
    job["estado"] = "ejecutando"
    job["inicio"] = _t.time()
    ultimo_emit = 0.0
    salida: list = []
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=workdir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job["pid"] = p.pid
        job["proc"] = p
        cola: _q.Queue = _q.Queue()

        def _lector():
            try:
                while True:
                    linea = p.stdout.readline()
                    if not linea:
                        break
                    cola.put(linea)
            except Exception:
                pass

        _th.Thread(target=_lector, daemon=True,
                   name=f"oc-lector-{job['id']}").start()
        limite = _t.monotonic() + max(1, timeout)
        while True:
            while True:
                try:
                    linea = cola.get_nowait()
                except Exception:
                    break
                salida.append(linea)
                if len(salida) > 400:
                    salida = salida[-400:]
                texto = ""
                s = linea.strip()
                if s.startswith("{"):
                    try:
                        import json as _js
                        texto = _resumen_linea(_js.loads(s))
                    except Exception:
                        texto = ""
                if not texto and len(s) > 4 and not s.startswith("{"):
                    texto = s[:160]
                ahora = _t.time()
                if texto and (_es_paso_importante(texto)
                              or (ahora - ultimo_emit) > 20):
                    ultimo_emit = ahora
                    _step(task, texto)
            if job.get("cancelado"):
                _matar_arbol(p)
                job["estado"] = "cancelado"
                job["error"] = "Detenido por el usuario."
                _step(task, "Proyecto detenido por el usuario.", done=True)
                _cerrar_job(job, False, "Proyecto detenido por el usuario.")
                return
            if p.poll() is not None:
                break
            if _t.monotonic() >= limite:
                _matar_arbol(p)
                job["estado"] = "fallido"
                job["error"] = "Tardo demasiado y se detuvo."
                _step(task, "Se detuvo por tiempo.", done=True,
                      error=job["error"])
                _cerrar_job(job, False)
                return
            _t.sleep(0.5)
        resto_out = "".join(salida)[-2000:]
        if p.returncode not in (0, None):
            job["estado"] = "fallido"
            job["error"] = f"OpenCode termino con codigo {p.returncode}."
            _step(task, "OpenCode termino con error.", done=True,
                  error=job["error"])
            _cerrar_job(job, False)
            return
        # Ficheros nuevos o tocados dentro del proyecto. Los lockfiles se
        # cuentan aparte: son ruido del gestor de paquetes, no trabajo.
        after = _snapshot(workdir)
        todos = sorted(p for p in after
                       if after.get(p) != job["before"].get(p))
        lock = [p for p in todos if os.path.basename(p) in _LOCKFILES]
        changed = [p for p in todos if p not in lock]
        # Verifica sintaxis de lo tocado (mismo criterio que opencode_fix).
        import py_compile
        bad = []
        avisos = []
        for f in changed:
            try:
                if f.endswith(".py"):
                    py_compile.compile(f, doraise=True)
            except Exception as e:
                bad.append(f"{os.path.basename(f)}: {str(e)[:100]}")
        _hay_node = False
        try:
            import shutil as _sh
            _hay_node = bool(_sh.which("node"))
        except Exception:
            pass
        for f in changed:
            if f.endswith(".js") and _hay_node:
                try:
                    import subprocess as _sp2
                    r2 = _sp2.run(["node", "--check", f], capture_output=True,
                                  timeout=60, creationflags=getattr(
                                      _sp2, "CREATE_NO_WINDOW", 0))
                    if r2.returncode != 0:
                        bad.append(f"{os.path.basename(f)}: sintaxis JS")
                except Exception as e:
                    avisos.append(f"{os.path.basename(f)}: no comprobado ({e})")
        if any(f.endswith(".js") for f in changed) and not _hay_node:
            avisos.append("JS sin comprobar: no hay node en este equipo")
        job["ficheros"] = [os.path.relpath(p, workdir) for p in changed]
        if avisos:
            job["resumen"] = "Avisos: " + "; ".join(avisos[:4])
        if bad:
            job["estado"] = "fallido"
            job["error"] = "Sintaxis rota en: " + "; ".join(bad[:4])
            _step(task, "El codigo salio con errores de sintaxis.", done=True,
                  error=job["error"])
            _cerrar_job(job, False)
            return
        job["estado"] = "completado"
        job["resumen"] = resto_out[-500:]
        if lock:
            job["resumen"] += f" (+{len(lock)} lockfiles omitidos)"
        if avisos:
            job["resumen"] += " Avisos: " + "; ".join(avisos[:4])
        _step(task, f"Proyecto listo: {len(changed)} ficheros.", done=True)
        _cerrar_job(job, True)
    except Exception as e:
        job["estado"] = "fallido"
        job["error"] = str(e)[:200]
        _step(task, "El agente fallo al arrancar.", done=True, error=job["error"])
        _cerrar_job(job, False)


def _cerrar_job(job: dict, ok: bool, titulo: str = "") -> None:
    fn = _progress_emit
    if fn is None:
        return
    try:
        if not titulo:
            if ok:
                fich = job.get("ficheros") or []
                extra = f" ({len(fich)} ficheros: {', '.join(fich[:6])})" if fich else ""
                titulo = f"Proyecto listo{extra}"
            else:
                titulo = "El proyecto no salio."
        fn({"type": "live_task_end", "task_id": job["id"],
            "title": titulo[:160],
            "failed": not ok})
    except Exception as e:
        log.debug("live_task_end: %s", e)


def opencode_build(objetivo: str, proyecto: str = "",
                   timeout: int = 1500) -> dict:
    """Lanza a OpenCode a desarrollar un proyecto en segundo plano."""
    objetivo = (objetivo or "").strip()
    if not objetivo:
        return {"ok": False, "error": "Falta decir que proyecto quieres."}
    nombre = _sanitizar_proyecto(proyecto or objetivo.split("\n")[0][:30])
    if not _cli_disponible():
        # Comprobacion instantanea, sin intentar instalar: instalar tarda
        # minutos y bloquearia al servidor en cada lanzamiento.
        return {"ok": False,
                "error": "OpenCode CLI no esta disponible en este equipo. "
                         "En el servidor Docker no hay node ni opencode; "
                         "esto funciona en el JARVIS de escritorio."}
    base = _dir_proyectos()
    workdir = os.path.join(base, nombre)
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"No pude crear la carpeta: {e}"}
    import uuid as _uu
    job_id = "build-" + _uu.uuid4().hex[:10]
    tarea = f"Proyecto {nombre}: {objetivo[:80]}"
    job = {"id": job_id, "task": tarea, "proyecto": nombre,
           "workdir": workdir, "estado": "lanzado", "inicio": 0.0,
           "ficheros": [], "error": "", "resumen": "",
           "before": _snapshot(workdir)}
    with _jobs_lock():
        _JOBS[job_id] = job
    prompt = _prompt_proyecto(objetivo, workdir)
    cli = _cli_disponible()
    if not cli:
        return {"ok": False,
                "error": "OpenCode CLI no esta disponible en este equipo. "
                         "En el servidor Docker no hay node ni opencode; "
                         "esto funciona en el JARVIS de escritorio."}
    cmd = [cli, "run", "-m", OC_MODEL, "--format", "json", "--auto",
           "--dir", workdir, prompt]
    # live_task_start: los paneles lo muestran como trabajo activo. Lleva el
    # objetivo para que el trabajo salga en Recientes y se pueda reintentar.
    fn = _progress_emit
    if fn is not None:
        try:
            fn({"type": "live_task_start", "task_id": job_id,
                "title": tarea[:120], "message": objetivo[:2000],
                "description": "OpenCode desarrollando el proyecto"})
        except Exception as e:
            log.debug("live_task_start: %s", e)
    import threading as _th
    _th.Thread(target=_ejecutar_job,
               args=(job, cmd, _keys_env(), max(120, int(timeout or 1500))),
               daemon=True, name=f"opencode-{nombre}").start()
    return {"ok": True, "job_id": job_id, "proyecto": nombre,
            "carpeta": workdir,
            "mensaje": f"OpenCode ya esta trabajando en '{nombre}'. "
                       "Mira el avance en los paneles."}


def opencode_jobs() -> dict:
    """Lo que estan haciendo (o hicieron) los agentes de proyectos."""
    with _jobs_lock():
        jobs = [dict(j) for j in _JOBS.values()]
    for j in jobs:
        # Ni el snapshot ni el proceso son serializables ni aportan nada fuera.
        j.pop("before", None)
        j.pop("proc", None)
    return {"ok": True, "jobs": jobs}


def opencode_cancel(job_id: str = "") -> dict:
    """Detiene un proyecto en marcha. Lo ya escrito se queda como esta."""
    job_id = (job_id or "").strip()
    if not job_id:
        return {"ok": False, "error": "Falta el id del trabajo."}
    with _jobs_lock():
        job = _JOBS.get(job_id)
    if job is None:
        return {"ok": False, "error": "Ese trabajo no existe."}
    if job.get("estado") != "ejecutando":
        return {"ok": False, "error": f"Ya no esta en marcha ({job.get('estado')})."}
    job["cancelado"] = True
    proc = job.get("proc")
    if proc is not None:
        _matar_arbol(proc)
    return {"ok": True, "job_id": job_id,
            "mensaje": "Orden de parada enviada; el panel lo confirmara."}


def opencode_cancel_handler(params: dict) -> dict:
    """Tool: detiene un proyecto en marcha."""
    res = opencode_cancel(params.get("job_id") or params.get("id") or "")
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo detener."))
    return res


def opencode_build_handler(params: dict) -> dict:
    """Tool: JARVIS encarga un proyecto a OpenCode y se sigue en paneles."""
    res = opencode_build(
        objetivo=params.get("objetivo") or params.get("task") or "",
        proyecto=params.get("proyecto") or params.get("name") or "",
        timeout=params.get("timeout", 1500))
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "No se pudo lanzar."))
    return res


def opencode_jobs_handler(params: dict) -> dict:
    """Tool: estado de los proyectos en marcha."""
    return opencode_jobs()
    """Pregunta rápida al agente (sin editar nada): devuelve texto."""
    if not os.path.isfile(OC_CMD):
        return ""
    try:
        r = subprocess.run(
            [OC_CMD, "run", "-m", OC_MODEL, "--format", "json",
             "--dir", _brain_dir(), question],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_keys_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "")[-1500:]
    except Exception as e:
        log.debug("opencode ask: %s", e)
        return ""


def opencode_ask(question: str, timeout: int = 180) -> str:
    """Pregunta rápida al agente (sin editar nada): devuelve texto."""
    if not os.path.isfile(OC_CMD):
        return ""
    try:
        r = subprocess.run(
            [OC_CMD, "run", "-m", OC_MODEL, "--format", "json",
             "--dir", _brain_dir(), question],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=_keys_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "")[-1500:]
    except Exception as e:
        log.debug("opencode ask: %s", e)
        return ""


def opencode_fix_handler(params: dict) -> dict:
    """Tool: delega una reparación a OpenCode (con confirmación)."""
    task = (params.get("task", "") or "").strip()
    if not task:
        raise ValueError("Falta la tarea de reparación.")
    res = opencode_fix(task, timeout=int(params.get("timeout", OC_TIMEOUT) or OC_TIMEOUT))
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "OpenCode no pudo."))
    return res


def opencode_ask_handler(params: dict) -> dict:
    """Tool: pregunta rápida a OpenCode (solo lectura)."""
    q = (params.get("question", "") or "").strip()
    if not q:
        raise ValueError("Falta la pregunta.")
    return {"answer": opencode_ask(q)}


OCODE_TOOLS = [
    {"name": "opencode_fix",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Delego la reparación a OpenCode?",
     "parameters": {"task": "str", "timeout": "int opcional"},
     "description": "Delega una reparación de código a OpenCode (verifica sintaxis).",
     "label": "Reparando con OpenCode",
     "handler": opencode_fix_handler},
    {"name": "opencode_ask",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"question": "str"},
     "description": "Pregunta técnica a OpenCode (solo lectura, sin cambios).",
     "label": "Consultando a OpenCode",
     "handler": opencode_ask_handler},
    {"name": "opencode_build",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Encargo a OpenCode desarrollar el proyecto '{objetivo}' en su carpeta?",
     "parameters": {"objetivo": "str, qué programa o proyecto desarrollar",
                    "proyecto": "str opcional, nombre corto para la carpeta",
                    "timeout": "int opcional, segundos máximos"},
     "description": "Encarga a OpenCode desarrollar un proyecto o programa nuevo desde cero (una app, una web, un script, lo que pida el señor). Trabaja en segundo plano en su propia carpeta y el avance se ve en directo en los paneles.",
     "label": "Desarrollando proyecto con OpenCode",
     "handler": opencode_build_handler},
    {"name": "opencode_jobs",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {},
     "description": "Cómo van los proyectos que OpenCode está desarrollando: estado, ficheros creados y errores.",
     "label": "Viendo proyectos en marcha",
     "handler": opencode_jobs_handler},
    {"name": "opencode_cancel",
     "risk_level": "medium",
     "requires_confirmation": True,
     "confirm_text": "¿Detener el proyecto en marcha? Lo ya escrito se queda.",
     "parameters": {"job_id": "str, el id del trabajo (build-...)"},
     "description": "Detiene un proyecto que OpenCode está desarrollando ahora mismo. Lo ya escrito no se borra.",
     "label": "Deteniendo proyecto",
     "handler": opencode_cancel_handler},
]
