"""De una idea a un panel de Live, comprobado paso a paso.

Qué se pregunta: si el señor dice "desarrollame una app que...", ¿JARVIS lo
Piensa, lo dice a OpenCode, y sale en el panel de Live?

Este fichero no da por buena ninguna de las tres cosas porque estén escritas
en el código. Comprueba, en este orden:

  1. Que una idea de desarrollo se detecta (y que una que NO lo es no se
     detecta: si todo fuera "desarrollo", la detección no valdría nada).
  2. Qué se le dice exactamente a OpenCode, mirando el comando de verdad que
     se construiría. OpenCode no se ejecuta: se mira lo que se le pasaría.
  3. Que el trabajo aparece en el panel de Live.
  4. Y el punto que más duele: si la idea pasa por el orquestador —el único
     que PIENSA la idea, la analiza, le saca riesgos y pide permiso— o si se
     la pasa tal cual.

    python test_idea_a_live.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-idea-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def seccion(t):
    print(f"\n{t}")


# --------------------------------------------------------------------------- #
# 1. Detección de la intención
# --------------------------------------------------------------------------- #
def probar_deteccion():
    seccion("1. JARVIS distingue «desarrolla esto» de «no es una desarrollo»")
    import main

    develops = [
        "desarrollame una app para llevar las gastos del mes",
        "programame un script que ordene mis fotos",
        "hazme una web para mi taller",
        "creame un programa de notas",
        "construye una tienda online",
        "quiero una app que me avise del tiempo",
    ]
    for frase in develops:
        nombre = main._intencion_desarrollo(frase)
        comprobar(bool(nombre),
                  f"«{frase[:44]}» → proyecto «{nombre or 'NO DETECTADO'}»")

    # Esto NO soncoxiones de desarrollo. Si saliera, la detección no valdría.
    no_desarrolla = [
        "ponme una alarma a las 7",
        "apunta que tengo que llamar al médico",
        "qué es un servidor web",
        "explícame cómo funciona python",
        "cuéntame un chiste",
        "cuánto gas queda",
        "recuerda que el viernes es el anniversary",
        "crea un recordatorio para mañana",
        "hazme una lista de la compra",   # 'hazme' pero no es software
    ]
    for frase in no_desarrolla:
        nombre = main._intencion_desarrollo(frase)
        comprobar(not nombre,
                  f"«{frase[:44]}» → no es desarrollo ({nombre or 'correcto'})")


# --------------------------------------------------------------------------- #
# 2. Qué se le dice a OpenCode
# --------------------------------------------------------------------------- #
def probar_que_se_le_dice():
    seccion("2. Qué recibe OpenCode exactamente")
    from tools import opencode_tools as oc

    # Se captura el comando SIN ejecutarlo: lo que se quiere saber es qué se
    # le pasa, no qué contesta (contestar es otra prueba y cuesta minutos).
    captado = {}

    def _cli_falso():
        return r"C:\falso\opencode.exe"

    def _ejecuta_falso(job, cmd, env, timeout):
        captado["cmd"] = cmd
        captado["workdir"] = job.get("workdir")
        job["estado"] = "capturado"

    oc._cli_disponible = _cli_falso
    oc._ejecutar_job = _ejecuta_falso

    idea = "desarrollame una app para llevar las gastos del mes"
    r = oc.opencode_build(idea, "gastos")

    comprobar(r.get("ok") is True, f"acepta la idea: {r.get('error', '')}")
    comprobar(bool(r.get("job_id")), f"devuelve un identificador: {r.get('job_id')}")

    # El hilo se lanza en segundo plano: se espera un momento a que corra.
    for _ in range(40):
        if "cmd" in captado:
            break
        time.sleep(0.05)

    cmd = captado.get("cmd")
    comprobar(bool(cmd), "lanzó el comando de OpenCode")
    if cmd:
        unido = " ".join(str(x) for x in cmd)
        comprobar("run" in cmd, "usa el subcomando `run`")
        comprobar("--auto" in cmd,
                  f"y lo pasa con --auto (o sea, sin preguntar nada) "
                  f"→ {'SÍ' if '--auto' in cmd else 'no'}")
        # Lo importante: ¿qué prompt se le da?
        prompt = cmd[-1] if cmd else ""
        comprobar(len(prompt) > 20,
                  f"le pasa un prompt de verdad ({len(prompt)} caracteres)")
        print(f"    prompt (primeros 200): {prompt[:200]!r}")

    return r, captado


# --------------------------------------------------------------------------- #
# 3. Que aparezca en Live
# --------------------------------------------------------------------------- #
def probar_live():
    seccion("3. El trabajo sale en el panel de Live")
    from tools import opencode_tools as oc

    recibidos = []
    oc.set_progress_emit(lambda ev: recibidos.append(ev))
    try:
        oc._cli_disponible = lambda: r"C:\falso\opencode.exe"
        oc._ejecutar_job = lambda job, cmd, env, timeout: job.update(estado="x")
        oc.opencode_build("hazme una web de prueba para Live", "prueba-live")
        for _ in range(40):
            if recibidos:
                break
            time.sleep(0.05)
    finally:
        oc.set_progress_emit(None)

    Live = [e for e in recibidos if e.get("type") == "live_task_start"]
    comprobar(bool(Live), f"emite live_task_start ({len(recibidos)} eventos)")
    if Live:
        ev = Live[0]
        comprobar(bool(ev.get("task_id")), "con identificador de tarea")
        comprobar(bool(ev.get("title")), f"con título: {str(ev.get('title'))[:60]!r}")
        comprobar(bool(ev.get("message")),
                  "y con el mensaje, para poder reintentarlo")


def probar_live_de_verdad():
    """El evento anterior era un doble. Este pasa por el Brain de verdad, que
    es quien guarda el trabajo para que se vea en el panel."""
    seccion("4. Y el Brain de verdad lo guarda para el panel")

    import main

    brain = main.brain
    if brain is None:
        comprobar(False, "no hay Brain instanciado")
        return
    antes = len(getattr(brain, "_live_tasks", {}))
    brain.emit({"type": "live_task_start", "task_id": "prueba-idea-live",
                "title": "Prueba de idea", "description": "Comprobación",
                "message": "una idea de prueba"})
    time.sleep(0.2)
    tareas = getattr(brain, "_live_tasks", {})
    comprobar("prueba-idea-live" in tareas,
              f"la tarea quedó en la lista del panel "
              f"({antes} → {len(tareas)})")
    if "prueba-idea-live" in tareas:
        t = tareas["prueba-idea-live"]
        comprobar(t.get("state") == "ejecutando",
                  f"y aparece como trabajo en marcha: {t.get('state')!r}")
        # Limpieza: no dejar basura en el panel del señor.
        with brain._live_task_lock:
            tareas.pop("prueba-idea-live", None)


# --------------------------------------------------------------------------- #
# 5. El punto que más duele: ¿piensa o solo pasa el texto?
# --------------------------------------------------------------------------- #
def probar_si_piensa():
    seccion("5. ¿JARVIS piensa la idea, o se la pasa tal cual a OpenCode?")

    from fastapi.testclient import TestClient
    import main

    # --- El orquestador existe y sabe pensar -----------------------------
    with TestClient(main.app):
        gestor = getattr(main.brain, "dev", None)
    comprobar(gestor is not None,
              "con la app arrancada existe el orquestador "
              "(analiza, predice riesgos y pide permiso)")

    # --- Y la conversación lo usa ---------------------------------------
    from tools import opencode_tools as oc
    src = Path(oc.__file__).read_text(encoding="utf-8")
    usa_orquestador = ("orchestrator" in src or "brain.dev" in src
                       or "get_manager" in src)
    comprobar(not usa_orquestador,
              "`opencode_build` NO lo usa: por ahí la idea va sin pensar "
              "(es el camino de reserva)")

    # La conexión nueva: la idea entra por el orquestador.
    ored = {"creo": [], "analice": []}

    class _Gestor:
        def crear(self, idea="", project_path="", **kw):
            ored["creo"].append(idea)
            return {"ok": True, "task": {"id": "T-PRUEBA"}}

        def analizar(self, task_id):
            ored["analice"].append(task_id)

    anterior = getattr(main.brain, "dev", None)
    main.brain.dev = _Gestor()
    try:
        r = main._encargar_al_taller("quiero una app de notas", "notas")
    finally:
        main.brain.dev = anterior

    comprobar(bool(r), f"la idea llega al orquestador y contesta: {str(r)[:70]!r}")
    comprobar(bool(ored["creo"]) and ored["creo"][0].startswith("quiero una app"),
              "con la idea TAL CUAL la escribió el señor")
    comprobar(ored["analice"] == ["T-PRUEBA"],
              "y se pone a analizarla sin esperar en el hilo del mensaje")

    # --- Y si el orquestador no está, no se rompe nada -------------------
    main.brain.dev = None
    comprobar(main._encargar_al_taller("quiero una app", "x") is None,
              "sin orquestador devuelve None y se usa el camino viejo")

    class _Roto:
        def crear(self, **kw):
            raise RuntimeError("se rompió")

    main.brain.dev = _Roto()
    try:
        comprobar(main._encargar_al_taller("x", "y") is None,
                  "y si el orquestador falla, tampoco revienta")
    finally:
        main.brain.dev = anterior

    # --- Y qué le llega a OpenCode por el camino viejo -------------------
    captado = {}
    oc._cli_disponible = lambda: r"C:\falso\opencode.exe"
    oc._ejecutar_job = lambda job, cmd, env, timeout: captado.update(cmd=cmd)
    oc.opencode_build("quiero una app de notas", "notas")
    for _ in range(40):
        if "cmd" in captado:
            break
        time.sleep(0.05)

    prompt = captado.get("cmd", [""])[-1] if captado.get("cmd") else ""
    # Estas palabras SOLO aparecen si algo ha analizado la idea. El
    # BUILD_PROMPT son instrucciones fijas y no tiene ninguna (ni "riesgo",
    # ni "pasos", ni "arquitectura": se comprobó una por una).
    marcas_de_plan = ("riesgo", "estimaci", "arquitectura", "plan de", "pasos")
    parece_plan = [m for m in marcas_de_plan if m in prompt.lower()]
    comprobar(not parece_plan,
              f"el camino de reserva pasa la idea con instrucciones fijas, "
              f"sin plan (marcas: {parece_plan})")


def main_() -> int:
    print("IDEA -> LIVE · ¿llega de punta a punta?")
    probar_deteccion()
    probar_que_se_le_dice()
    probar_live()
    probar_live_de_verdad()
    probar_si_piensa()

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("IDEA -> LIVE: LA CADENA FUNCIONA.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())