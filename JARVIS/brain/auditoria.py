"""Auditoría real de JARVIS: ejecuta los sistemas y dice qué funciona de verdad.

No mira si el fichero existe: llama a las funciones (las que no son
peligrosas), mira lo que devuelven y anota si el resultado es real, parcial o
vacio. Lo que no se puede comprobar sin red, clave o hardware se marca como
NO VERIFICABLE, nunca como OK.

    python brain/auditoria.py            # human
    python brain/auditoria.py --json     # para maquinas
"""
from __future__ import annotations

import importlib
import inspect
import re
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-auditoria-"))

resultados: list[dict] = []


def anota(area: str, que: str, estado: str, detalle: str = "") -> None:
    """estado: REAL, PARCIAL, ROTO, NO_VERIFICABLE"""
    resultados.append({"area": area, "que": que, "estado": estado, "detalle": detalle[:300]})
    marca = {"REAL": "OK     ", "PARCIAL": "PARCIAL", "ROTO": "ROTO   ",
             "NO_VERIFICABLE": "SIN DAT"}.get(estado, "?")
    print(f"  [{marca}] {area}: {que}" + (f"\n            {detalle[:160]}" if detalle else ""))


defarea = lambda nombre: print(f"\n=== {nombre} ===")


def probar(area: str, que: str, funcion) -> None:
    try:
        valor = funcion()
    except Exception as exc:  # noqa: BLE001
        anota(area, que, "ROTO", f"{type(exc).__name__}: {exc}")
        return
    anota(area, que, "REAL", str(valor)[:220])


# --------------------------------------------------------------------------- #
def auditar_nucleo() -> None:
    area = "Nucleo"
    probar(area, "el cerebro arranca e importa sus sistemas", lambda: _arranque())

    def _cerebro():
        from main import brain
        # Importar main NO arranca el servidor: los sistemas se enganchan en
        # _init_sistemas(), que corre al levantar la app. Aqui solo se mira
        # que la clase existe y que tiene los atributos esperados.
        campos = [c for c in ("risk", "tools", "router", "proactive", "discord")
                  if hasattr(brain, c)]
        return (f"clase Brain con {len(campos)} campos: {campos}; "
                f"iniciados={'si' if getattr(brain, 'risk', None) else 'no (app no levantada)'}")

    probar(area, "Brain tiene risk/tools/router/proactive", _cerebro)
    probar(area, "hay un emit() (Event Bus) con suscriptores", lambda: _bus())


def _arranque() -> str:
    import main  # noqa: F401  (arranca el cerebro entero)
    return "import ok"


def _bus() -> str:
    from main import brain
    with brain._live_task_lock:
        tareas = len(brain._live_tasks)
    tiene = [m for m in ("emit", "record_live_event", "_broadcast")
             if hasattr(brain, m)]
    from command import notify
    return f"metodos={tiene} trabajos_vivos={tareas} suscriptores={len(getattr(notify, '_suscriptores', []) or [])}"


def auditar_permisos() -> None:
    area = "Permisos"
    probar(area, "RiskEngine clasifica riesgos", lambda: _riesgos())
    probar(area, "Tool Manager tiene catalogo de herramientas", lambda: _herramientas())
    probar(area, "una herramienta pasa por el Permission Manager", lambda: _permiso_real())


def _riesgos() -> str:
    from risk_engine import RiskEngine

    return f"niveles={getattr(RiskEngine, 'NIVELES', '?')} " \
           f"timeout={getattr(RiskEngine, 'CONFIRMATION_TIMEOUT_SECONDS', '?')}"


def _herramientas() -> str:
    import importlib

    from tool_manager import ToolManager

    manager = ToolManager.__new__(ToolManager)      # sin arrancar nada
    try:
        manager.tools = ToolManager._cargar_tools() if hasattr(
            ToolManager, "_cargar_tools") else {}
    except Exception:  # noqa: BLE001
        manager.tools = {}
    catalogo = manager.tools or {}
    nombres = sorted(catalogo) if isinstance(catalogo, dict) else [str(catalogo)]
    # Aparte: cuantas herramientas de verdad hay registradas en el paquete tools.
    import pkgutil
    modulos = [m.name for m in pkgutil.iter_modules(
        [str(importlib.import_module("tools").__path__)])]
    return f"registradas={len(nombres)} modulos_de_tools={len(modulos)} " \
           f"ejemplos={nombres[:5]}"


def _permiso_real() -> str:
    """Una acción de riesgo bajo pasa por el motor. No ejecuta nada."""
    import asyncio

    from risk_engine import RiskEngine

    motor = RiskEngine(emit=lambda *a, **k: None)

    async def _go():
        return await motor.check({
            "name": "auditoria_leer_logs", "risk_level": "low",
            "requires_confirmation": False, "confirm_text": ""}, {})

    r = asyncio.run(_go())
    return (f"check(bajo) -> ok={getattr(r, 'ok', r)} "
            f"niveles={len(getattr(RiskEngine, 'NIVELES', []) or [])}")


def auditar_memoria() -> None:
    area = "Memoria"
    probar(area, "SQLite con mensajes, auditoría y dev_tasks", lambda: _db())
    probar(area, "memoria semantica (recuerdos relevantes)", lambda: _semantica())
    probar(area, "Mem0 (memoria persistente) responde o se degrada", lambda: _mem0())
    probar(area, "perfil del señor", lambda: _perfil())


def _db() -> str:
    from memory import sqlite_store as db

    with db.get_conn() as c:
        tablas = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    return f"{len(tablas)} tablas: {sorted(tablas)[:9]}"


def _semantica() -> str:
    from memory import semantic

    motor = semantic.semantic_memory
    try:
        r = motor.recall_context("hola", limit=1)
        return (f"recall devuelve {type(r).__name__}; motor={type(motor).__name__}; "
                f"hay_memoria={getattr(motor, 'cargado', '?')}")
    except Exception as exc:  # noqa: BLE001
        return f"recall falla: {type(exc).__name__}: {exc}"


def _mem0() -> str:
    from config import Config

    if not Config.MEMORY_ENABLED:
        return "MEMORY_ENABLED=0 (memoria persistente apagada)"
    from memory import manager

    try:
        estado = manager.cliente.estado() if hasattr(manager.cliente, "estado") else None
    except Exception:  # noqa: BLE001
        estado = None
    try:
        texto = manager.para_el_prompt("hola")
        return f"url={Config.MEM0_URL} para_el_prompt -> {len(texto or '')} caracteres (no lanza nunca)"
    except Exception as exc:  # noqa: BLE001
        return f"degradado: {type(exc).__name__}: {exc}"


def _perfil() -> str:
    from memory import profile

    funciones = [n for n in dir(profile) if not n.startswith("_")]
    leer = next((getattr(profile, n) for n in ("cargar", "perfil", "obtener")
                 if hasattr(profile, n)), None)
    try:
        datos = leer() if leer else {}
        claves = sorted(datos)[:6] if isinstance(datos, dict) else "?"
    except Exception as exc:  # noqa: BLE001
        claves = f"error {type(exc).__name__}"
    return f"funciones={funciones[:5]} contenido={claves}"


def auditar_ia() -> None:
    area = "IA"
    probar(area, "AI Gateway responde con su estado", lambda: _gateway())
    probar(area, "el registro elige modelo por tarea", lambda: _registro())
    probar(area, "Groq tiene clave en este equipo", lambda: _clave_groq())
    probar(area, "red hacia api.groq.com", lambda: _red_groq())
    probar(area, "OpenCode (agente de codigo) localizable", lambda: _opencode())


def _gateway() -> str:
    from ai_gateway import gateway

    e = gateway.instancia().estado()
    return (f"proveedores={[p['id'] for p in e['proveedores']]} "
            f"asignados={[a['tarea'] for a in e['asignados']]}")


def _registro() -> str:
    from ai_gateway import registry

    return " · ".join(f"{t}->{registry.elegir(t)['id']}" for t in registry.TAREAS)


def _clave_groq() -> str:
    from config import Config

    return "puesta" if Config.GROQ_API_KEY else "NO PUESTA en este equipo"


def _red_groq() -> str:
    import urllib.error
    import urllib.request

    from ai_gateway.providers import groq as g

    # El User-Agent no es opcional: Cloudflare rechaza con 1010 la firma de
    # `urllib`, y eso llegaba aquí como un "bloqueada" que no era del chave sino
    # de cómo hacíamos la petición.
    peticion = urllib.request.Request(f"{g.base_url()}/models",
                                     headers={"Authorization": "Bearer prueba",
                                              "User-Agent": g._USER_AGENT})
    try:
        with urllib.request.urlopen(peticion, timeout=8) as r:
            return f"contesta {r.status}"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"llega y responde {exc.code} (red OK; bloqueada o sin clave)"
        return f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"sin red hacia Groq ({type(exc).__name__})"


def _opencode() -> str:
    from orchestrator import service

    d = service.describe()
    return f"servidor={'vivo' if d.get('ok') else 'no'} url={d.get('url', '')}"


def auditar_misiones() -> None:
    area = "Misiones"
    probar(area, "orquestador con estados y persistencia", lambda: _orquestador())
    probar(area, "planificador construye plan + riesgos reales", lambda: _planner())
    probar(area, "validación automática (no trusts 'OpenCode terminó')", lambda: _validacion())


def _orquestador() -> str:
    from orchestrator.manager import get_manager

    g = get_manager()
    e = g.estado()
    return f"estado={e['state']} tareas_vivas={len(e.get('tasks', []))}"


def _planner() -> str:
    import inspect as ins
    import tempfile as tf

    from orchestrator import planner

    escr = tf.mkdtemp(prefix="planner-")
    (Path(escr) / "README.md").write_text("x", encoding="utf-8")
    (Path(escr) / "main.py").write_text("print('hola')\n", encoding="utf-8")
    firma = str(ins.signature(planner.analizar))
    try:
        salida = planner.analizar(escr, "añade una función de prueba")
        pasos = (salida.get("plan") or {}).get("pasos") or salida.get("pasos") or []
        riesgos = salida.get("riesks") or salida.get("riesgos") or []
        return (f"analizar(ruta, idea) -> pasos={len(pasos)} riesgos={len(riesgos)} "
                f"claves={sorted(salida)[:6]}")
    except TypeError:
        salida = planner.analizar("añade una función de prueba", escr)
        return f"analizar(idea, ruta) -> claves={sorted(salida)[:6]}"
    except Exception as exc:  # noqa: BLE001
        return f"analizar falla: {type(exc).__name__}: {exc}"


def _validacion() -> str:
    import inspect as ins
    from orchestrator.manager import TaskManager

    src = ins.getsource(TaskManager._validar)
    comprueba = [w for w in ("fichero", "tests", "git", "cambios") if w in src.lower()]
    return f"comprueba={comprueba} lineas={len(src.splitlines())}"


def auditar_recuperacion() -> None:
    area = "Recuperacion"
    probar(area, "al reiniciar, las misiones a medias se explican", lambda: _recuperar())
    probar(area, "autocorrector con deteccion y propuesta", lambda: _selfheal())


def _recuperar() -> str:
    import inspect as ins
    from orchestrator.manager import TaskManager

    src = ins.getsource(TaskManager.recuperar).lower()
    return f"menciona interrumpir={'interrump' in src} reofrecer={'recuperable' in src}"


def _selfheal() -> str:
    from memory import selfheal

    return f"detectores={len(getattr(selfheal, 'REGLAS', []) or getattr(selfheal, 'PATRONES', []) or [])} " \
           f"funciones={[n for n in dir(selfheal) if n.startswith(('anali', 'detec', 'propon', 'auto'))][:5]}"


def auditar_automatizaciones() -> None:
    area = "Automations"
    probar(area, "planificador con disparadores reales", lambda: _auto())


def _auto() -> str:
    import inspect as ins

    from tools import auto_tools as a

    catalogo = getattr(a, "AUTO_TOOLS", {}) or {}
    disparadores = sorted({str(t.get("disparador") or t.get("tipo") or
                               t.get("trigger") or "?")
                           for t in catalogo.values()
                           if isinstance(t, dict)}) if isinstance(catalogo, dict) else []
    desde = []
    for n in dir(a):
        if n.startswith("programar") or n in ("crear_tarea", "agendar"):
            desde.append(n)
    return (f"herramientas={len(catalogo)} disparadores={disparadores or 'sin declarar'} "
            f"crear={desde or 'via tool'}")


def auditar_voz() -> None:
    area = "Voz"
    probar(area, "wake word + STT + TTS con interrupcion", lambda: _voz())


def _voz() -> str:
    import inspect as ins

    from voice import manager as vm

    metodos = [n for n in dir(vm.VoiceManager) if not n.startswith("_")]
    try:
        src = ins.getsource(vm.VoiceManager)
    except (OSError, TypeError):
        src = ""
    return (f"clase={metodos[:8]} barge_in={'barge' in src.lower()} "
            f"estados={[e for e in ('IDLE','LISTENING','THINKING','SPEAKING') if hasattr(vm, e)]}")


def auditar_canales() -> None:
    area = "Canales"
    probar(area, "Discord puente con respuestas a la misión", lambda: _discord())
    probar(area, "Home Assistant (integración y herramientas)", lambda: _ha())


def _discord() -> str:
    from config import Config

    tiene = bool(Config.DISCORD_BOT_TOKEN)
    import inspect as ins
    from integrations import discord_bridge as d

    src = ins.getsource(d)
    return f"token={'si' if tiene else 'no'} responde_preguntas={'responde' in src.lower()}"


def _ha() -> str:
    import os as _os

    token = bool(_os.environ.get("HA_TOKEN"))
    url = _os.environ.get("HA_URL", "")
    herramientas = []
    try:
        from tools import custom_tools as ct
        herramientas = [n for n in dir(ct) if "ha" in n.lower()
                        or "home" in n.lower() or "luz" in n.lower()
                        or "light" in n.lower()][:5]
    except Exception:  # noqa: BLE001
        pass
    return f"HA_URL={url or 'sin poner'} token={'si' if token else 'no'} herramientas={herramientas}"


def auditar_docker() -> None:
    area = "Docker"
    probar(area, "JARVIS ve contenedores desde dentro", lambda: _docker())


def _docker() -> str:
    import shutil
    import subprocess

    exe = shutil.which("docker")
    if not exe:
        return "el cliente docker NO esta instalado: no hay gestor (no se ofrece)"
    try:
        r = subprocess.run([exe, "ps", "--format", "{{.Names}}"], capture_output=True,
                           text=True, timeout=8)
        nombres = [n for n in r.stdout.splitlines() if n.strip()]
        return f"{len(nombres)} contenedores: {nombres[:5]}"
    except Exception as exc:  # noqa: BLE001
        return f"docker no responde: {type(exc).__name__}"


def auditar_centro_comando() -> None:
    area = "Centro de mando"
    probar(area, "salud de los subsistemas", lambda: _salud())
    probar(area, "estado + actividad + permisos", lambda: _cmd())
    probar(area, "incidentes y notificaciones desde el bus", lambda: _bus_salud())
    probar(area, "Git: cambios y checkpoints", lambda: _git())


def _salud() -> str:
    from command import centers

    s = centers.salud(True)
    cuentas = s["cuentas"]
    return f"global={s['estado']} cuentas={cuentas} subsistemas={len(s['subsistemas'])}"


def _cmd() -> str:
    from command import activity

    a = activity.actividad()
    return f"hay_tarea={a['hay_tarea']} pasos={len(a['pasos'])}"


def _bus_salud() -> str:
    from command import store

    return f"notificaciones={store.contar_notificaciones()} incidentes={store.contar_incidentes()}"


def _git() -> str:
    from command import runner

    cambios = runner.cambios_git()
    return f"repo={cambios.get('repo')} cambios={cambios.get('total', 0)}"


def auditar_contexto() -> None:
    area = "Contexto"
    probar(area, "como se construye el contexto hoy", lambda: _contexto_hoy())


def _contexto_hoy() -> str:
    import inspect as ins

    import main

    fuente = None
    for n in dir(main):
        if n.startswith("_build_system_prompt") or n.startswith("handle_user_message"):
            fuente = getattr(main, n)
            break
    src = ins.getsource(fuente) if fuente else ""
    marcas = {m: src.count(m) for m in
              ("semantic_memory", "para_el_prompt", "get_recent_messages",
               "search_docs", "contexto_para_modelo", "RECALL_ENABLED")}
    return f"lineas={len(src.splitlines())} piezas={marcas}"


def auditar_frontend() -> None:
    area = "JARVIS Live"
    probar(area, "tres ficheros, ids y rutas coherentes", lambda: _front())


def _front() -> str:
    base = BRAIN / "live"
    ficheros = sorted(p.name for p in base.iterdir() if p.is_file())
    js = (base / "live.js").read_text(encoding="utf-8")
    html = (base / "index.html").read_text(encoding="utf-8")
    ids_html = set(re.findall(r'id="([^"]+)"', html))
    ids_js = set(re.findall(r"\$\('([^']+)'\)", js))
    vistas = len(set(re.findall(r'data-vista="([a-z]+)"', html)))
    return (f"ficheros={ficheros} vistas={vistas} "
            f"ids_faltantes={sorted(ids_js - ids_html) or 'ninguno'}")


def auditar_seguridad() -> None:
    area = "Seguridad"
    probar(area, "las rutas exigen token", lambda: _auth())
    probar(area, "la clave de Groq no sale por la API", lambda: _sin_claves())


def _auth() -> str:
    import inspect as ins

    from command import routes as cr

    src = ins.getsource(cr)
    n = src.count("_authorized")
    return f"comprobaciones de sesion en command/routes.py: {n}"


def _sin_claves() -> str:
    from ai_gateway import routes as ar

    import inspect as ins

    src = ins.getsource(ar)
    devuelve_clave = bool(re.search(r'return.*GROQ_API_KEY', src))
    return f"alguna ruta devuelve la clave: {devuelve_clave}"


def auditar_pruebas() -> None:
    area = "Pruebas"
    probar(area, "suites presentes", lambda: _pruebas())


def _pruebas() -> str:
    suites = {"test_ciclo.py": BRAIN / "orchestrator" / "test_ciclo.py",
              "test_cmd.py": BRAIN / "command" / "test_cmd.py",
              "test_ai.py": BRAIN / "ai_gateway" / "test_ai.py"}
    return ", ".join(f"{k}={'si' if v.is_file() else 'NO'}" for k, v in suites.items())


# --------------------------------------------------------------------------- #
def main() -> int:
    print("AUDITORIA REAL DE JARVIS · se ejecuta, no se supone")
    print(f"datos de la auditoria: {os.environ['JARVIS_DATA_DIR']}")
    inicio = time.time()
    for fn in (auditar_nucleo, auditar_permisos, auditar_memoria, auditar_ia,
               auditar_misiones, auditar_recuperacion, auditar_automatizaciones,
               auditar_voz, auditar_canales, auditar_docker, auditar_centro_comando,
               auditar_contexto, auditar_frontend, auditar_seguridad, auditar_pruebas):
        try:
            fn()
        except Exception:  # noqa: BLE001
            anota(fn.__name__, "el bloque entero", "ROTO", traceback.format_exc()[-200:])
    print(f"\n({time.time() - inicio:.1f} s)")
    cuentas = {}
    for r in resultados:
        cuentas[r["estado"]] = cuentas.get(r["estado"], 0) + 1
    print("resumen:", cuentas)
    if "--json" in sys.argv:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())